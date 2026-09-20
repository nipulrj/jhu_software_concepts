"""The application factory, and the page it serves.

Marked ``web``.  Everything here runs against a Flask test client with a canned
analysis provider -- no database, no network, and no browser: the assignment
forbids tests that need someone to click something, and this file is the reason
it does not need one.
"""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

import conftest
import db_config
import flask_app
import pull_data

pytestmark = pytest.mark.web

#: Every route the application is required to expose.
REQUIRED_ROUTES = {
    ("/", frozenset({"GET"})),
    ("/analysis", frozenset({"GET"})),
    ("/pull-data", frozenset({"POST"})),
    ("/update-analysis", frozenset({"POST"})),
    ("/status", frozenset({"GET"})),
}


def soup(response) -> BeautifulSoup:
    """Parse a response body for assertions about structure rather than bytes."""
    return BeautifulSoup(response.get_data(as_text=True), "html.parser")


# ----------------------------------------------------------------------
# The factory
# ----------------------------------------------------------------------
def test_create_app_returns_a_testable_app(app):
    """The factory produces a configured Flask application, not a global one."""
    assert app.name == "flask_app"
    assert app.config["TESTING"] is True
    assert app.config["SECRET_KEY"], "a signing key is needed for the session cookie"


def test_create_app_builds_independent_applications(make_app):
    """Two applications can coexist, each with its own collaborators.

    Worth asserting because it is the property that makes the rest of the suite
    possible: a module-level ``app`` shared between tests would leak the busy
    flag and the cached analysis from one test into the next.
    """
    first = make_app(state=pull_data.MemoryState())
    second = make_app(state=pull_data.MemoryState(busy=True))

    assert first is not second
    assert flask_app.get_services(first).state.is_busy() is False
    assert flask_app.get_services(second).state.is_busy() is True


def test_create_app_registers_every_required_route(app):
    """Each required path is registered, with the required method."""
    registered = {
        (rule.rule, frozenset(rule.methods) & {"GET", "POST"})
        for rule in app.url_map.iter_rules()
    }
    missing = REQUIRED_ROUTES - registered
    assert not missing, "routes missing from the application: {0}".format(sorted(missing))


def test_create_app_applies_config_overrides(make_app):
    """Values handed to the factory reach ``app.config``."""
    app = make_app(config={"SOME_SETTING": "value", "TESTING": True})
    assert app.config["SOME_SETTING"] == "value"


def test_create_app_database_url_redirects_every_connection(make_app, monkeypatch):
    """``DATABASE_URL`` passed to the factory redirects psycopg and the ORM alike.

    This is the hook the database tests rely on, so it is asserted directly
    rather than only through them.
    """
    monkeypatch.delenv("PGDATABASE", raising=False)
    make_app(config={"DATABASE_URL": "postgresql://tester:pw@example.test:5544/scratch"})

    assert db_config.connect_kwargs()["dbname"] == "scratch"
    assert db_config.describe() == "tester@example.test:5544/scratch"


def test_create_app_defaults_to_the_real_collaborators(monkeypatch):
    """Called with nothing, the factory wires up production, not test doubles."""
    monkeypatch.setattr(pull_data, "default_state", lambda: pull_data.MemoryState())
    services = flask_app.get_services(flask_app.create_app())

    assert services.analysis.provider is flask_app.default_analysis_provider
    assert services.pulls == []


# ----------------------------------------------------------------------
# GET /
# ----------------------------------------------------------------------
def test_root_redirects_to_the_analysis_page(client):
    """``/`` is a signpost; the application is the analysis page."""
    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/analysis")


def test_root_redirect_followed_reaches_the_page(client):
    """Following the redirect lands on a rendered page, not another redirect."""
    response = client.get("/", follow_redirects=True)

    assert response.status_code == 200
    assert soup(response).find("h1").get_text(strip=True) == "Analysis"


# ----------------------------------------------------------------------
# GET /analysis
# ----------------------------------------------------------------------
def test_analysis_page_loads(client):
    """The required status code."""
    assert client.get("/analysis").status_code == 200


def test_analysis_page_is_titled_analysis(client):
    """The page says what it is, in its heading and in its title."""
    page = soup(client.get("/analysis"))

    assert page.find("h1").get_text(strip=True) == "Analysis"
    assert "Analysis" in page.title.get_text()


def test_analysis_page_has_both_buttons(client):
    """Both buttons are present, with the stable selectors the tests rely on."""
    page = soup(client.get("/analysis"))

    pull = page.select_one('[data-testid="pull-data-btn"]')
    update = page.select_one('[data-testid="update-analysis-btn"]')

    assert pull is not None, 'no element with data-testid="pull-data-btn"'
    assert update is not None, 'no element with data-testid="update-analysis-btn"'
    assert pull.get_text(strip=True) == "Pull Data"
    assert update.get_text(strip=True) == "Update Analysis"


def test_analysis_page_buttons_are_enabled_when_idle(client):
    """Nothing is running, so nothing is disabled."""
    page = soup(client.get("/analysis"))

    assert not page.select_one('[data-testid="pull-data-btn"]').has_attr("disabled")
    assert not page.select_one('[data-testid="update-analysis-btn"]').has_attr("disabled")


def test_analysis_page_disables_the_buttons_while_a_pull_runs(make_app):
    """A pull in flight is visible in the markup, not only in the JSON."""
    busy = pull_data.MemoryState(busy=True)
    page = soup(make_app(state=busy).test_client().get("/analysis"))

    assert page.select_one('[data-testid="pull-data-btn"]').has_attr("disabled")
    assert page.select_one('[data-testid="update-analysis-btn"]').has_attr("disabled")
    assert page.select_one('[data-testid="pull-status"]')["data-running"] == "true"


def test_analysis_page_shows_at_least_one_answer_label(client):
    """At least one "Answer:" label, as required."""
    text = client.get("/analysis").get_data(as_text=True)

    assert "Answer:" in text


def test_analysis_page_renders_every_answered_question(client):
    """Each answered question appears, and each carries its own answer block."""
    page = soup(client.get("/analysis"))

    for result in conftest.CANNED_RESULTS:
        assert page.select_one('[data-testid="question-{0}"]'.format(result.number))
        assert page.select_one('[data-testid="answer-{0}"]'.format(result.number))


def test_analysis_page_separates_my_own_questions(client):
    """The assignment's questions and my own are rendered in separate sections."""
    page = soup(client.get("/analysis"))

    assert page.select_one('[data-testid="required-analysis"]') is not None
    assert page.select_one('[data-testid="original-analysis"]') is not None


def test_analysis_page_shows_the_fall_2026_total_in_the_header(client):
    """Question 1's own answer supplies the header count, so they cannot differ."""
    page = soup(client.get("/analysis"))

    assert "1,234" in page.select_one(".subtitle").get_text()


def test_analysis_page_reports_which_database_it_read(client):
    """The masthead names the server -- and never carries the password."""
    page = soup(client.get("/analysis"))

    assert page.select_one(".brand-meta").get_text(strip=True) == db_config.describe()


def test_analysis_page_renders_tables_supporting_notes_and_caveats(client):
    """The optional parts of a question render when a question has them."""
    page = soup(client.get("/analysis"))
    question_3 = page.select_one('[data-testid="question-3"]')

    assert question_3.select_one("table") is not None
    assert question_3.select_one("ul.supporting") is not None
    assert question_3.select_one("p.caveat") is not None


def test_analysis_page_omits_the_optional_parts_when_there_are_none(client):
    """A question with only an answer renders only an answer."""
    page = soup(client.get("/analysis"))
    question_1 = page.select_one('[data-testid="question-1"]')

    assert question_1.select_one("table") is None
    assert question_1.select_one("ul.supporting") is None
    assert question_1.select_one("p.caveat") is None


def test_analysis_page_computes_the_analysis_once_per_load(client, analysis_provider):
    """The page renders a held snapshot; loading it again does not recompute.

    The counterpart to :mod:`test_buttons`: if the page recomputed on every
    load, Update Analysis would have nothing to do.
    """
    client.get("/analysis")
    client.get("/analysis")

    assert analysis_provider.calls == 1


def test_analysis_page_survives_an_unreachable_database(make_app):
    """A database that is down produces an explanation, not a stack trace."""
    from sqlalchemy.exc import OperationalError

    def broken():
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    page = soup(make_app(analysis_provider=broken).test_client().get("/analysis"))
    banner = page.select_one('[data-testid="analysis-error"]')

    assert banner is not None
    assert "Could not read the database" in banner.get_text()


def test_analysis_page_with_no_questions_still_renders(make_app):
    """An empty analysis renders a page rather than failing."""
    response = make_app(analysis_provider=lambda: []).test_client().get("/analysis")
    page = soup(response)

    assert response.status_code == 200
    assert page.select_one('[data-testid="required-analysis"]') is None
    assert page.select_one('[data-testid="pull-data-btn"]') is not None


# ----------------------------------------------------------------------
# GET /status
# ----------------------------------------------------------------------
def test_status_route_reports_an_idle_application(client):
    """``/status`` answers JSON the page can poll."""
    payload = client.get("/status").get_json()

    assert payload["running"] is False
    assert payload["state"] == "idle"
    assert payload["analysis_updated_at"] == flask_app.NEVER_UPDATED


def test_status_route_reports_a_running_pull(make_app):
    """The flag the page watches follows the injected state."""
    client = make_app(state=pull_data.MemoryState(busy=True)).test_client()

    assert client.get("/status").get_json()["running"] is True


def test_status_route_reports_when_the_analysis_was_computed(client):
    """Once the page has been loaded, the timestamp is a real one."""
    client.get("/analysis")
    payload = client.get("/status").get_json()

    assert payload["analysis_updated_at"] != flask_app.NEVER_UPDATED
    assert payload["analysis_updated_at"].startswith("20")
