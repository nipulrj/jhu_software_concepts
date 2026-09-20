"""The two buttons, and what they do when a pull is already running.

Marked ``buttons``.  Every test here posts to the real endpoint through Flask's
test client; the scrape behind Pull Data is a fake, and the busy flag is a
:class:`pull_data.MemoryState` that a test sets directly.

There is no ``sleep`` in this file, and there could not be one: "a pull is in
progress" is a flag, so a test asserts against state it set rather than against
a scrape it hopes is still running.  The same goes for "and performs no
update" -- the analysis provider counts its calls, so a refused update is proved
by a number that did not move.
"""

from __future__ import annotations

import pytest

import conftest
import doubles
import flask_app
import pull_data

pytestmark = pytest.mark.buttons


# ----------------------------------------------------------------------
# POST /pull-data
# ----------------------------------------------------------------------
def test_pull_data_returns_ok_when_not_busy(client):
    """The required response: 200 (or 202) carrying ``{"ok": true}``."""
    response = client.post("/pull-data")

    assert response.status_code in (200, 202)
    assert response.get_json()["ok"] is True


def test_pull_data_answers_json(client):
    """The buttons are ``fetch`` calls, so the endpoint answers JSON."""
    response = client.post("/pull-data")

    assert response.mimetype == "application/json"
    assert response.get_json()["busy"] is False


def test_pull_data_triggers_the_runner(client, pull_runner):
    """Pressing the button starts a pull rather than only reporting one."""
    client.post("/pull-data")

    assert pull_runner.calls == 1


def test_pull_data_hands_the_loader_the_rows_the_scraper_returned(
    client_with_pipeline, scraper, loader
):
    """The scraper's rows reach the loader, through the real cleaner.

    The assignment's requirement in full: the loader is triggered *with the rows
    from the scraper*.  Both ends are fakes, so no network is touched and no
    database is written -- what is under test is the wiring between them.
    """
    response = client_with_pipeline.post("/pull-data")

    assert response.status_code == 200
    assert scraper.calls == 1
    assert loader.calls == 1
    assert len(loader.records) == len(doubles.SAMPLE_RAW_ROWS)

    scraped_ids = {row["entry_id"] for row in doubles.SAMPLE_RAW_ROWS}
    loaded_ids = {record["entry_id"] for record in loader.records}
    assert loaded_ids == scraped_ids


def test_pull_data_reports_the_summary_it_was_given(client, make_app):
    """What the pull did comes back in the response, not just "done"."""
    summary = {"inserted": 7, "total": 19, "scraped": 12}
    app = make_app(pull_runner=doubles.FakePullRunner(lambda: summary))

    body = app.test_client().post("/pull-data").get_json()

    assert body["summary"] == summary


def test_pull_data_records_each_pull_on_the_application(client, services):
    """The application keeps the summaries, so a later request can report them."""
    client.post("/pull-data")
    client.post("/pull-data")

    assert len(services.pulls) == 2


def test_pull_data_does_not_touch_the_analysis(client, analysis_provider):
    """Pulling loads rows; it does not recompute the figures.

    That separation is the whole point of having two buttons, so it is asserted
    rather than assumed.
    """
    client.get("/analysis")
    before = analysis_provider.calls

    client.post("/pull-data")

    assert analysis_provider.calls == before


# ----------------------------------------------------------------------
# POST /update-analysis
# ----------------------------------------------------------------------
def test_update_analysis_returns_200_when_not_busy(client):
    """The required response."""
    response = client.post("/update-analysis")

    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_update_analysis_recomputes_the_figures(client, analysis_provider):
    """It actually refreshes, rather than answering 200 and doing nothing."""
    client.get("/analysis")
    assert analysis_provider.calls == 1

    client.post("/update-analysis")

    assert analysis_provider.calls == 2


def test_update_analysis_reports_what_it_computed(client):
    """The response says how much analysis it refreshed, and when."""
    body = client.post("/update-analysis").get_json()

    assert body["questions"] == len(conftest.CANNED_RESULTS)
    assert body["updated_at"].startswith("20")
    assert "refreshed" in body["message"]


def test_update_analysis_moves_the_page_timestamp(client):
    """The refreshed snapshot is the one the page then renders."""
    first = client.get("/status").get_json()["analysis_updated_at"]
    client.get("/analysis")

    client.post("/update-analysis")
    second = client.get("/status").get_json()["analysis_updated_at"]

    assert first != second


def test_update_analysis_never_starts_a_pull(client, pull_runner, state):
    """Update Analysis reads; it does not scrape."""
    client.post("/update-analysis")

    assert pull_runner.calls == 0
    assert state.is_busy() is False


def test_update_analysis_reports_an_unreachable_database(make_app):
    """A refresh that could not read the database is not reported as success."""
    from sqlalchemy.exc import OperationalError

    def broken():
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    response = make_app(analysis_provider=broken).test_client().post("/update-analysis")

    assert response.status_code == 503
    assert response.get_json()["ok"] is False
    assert "Could not read the database" in response.get_json()["error"]


# ----------------------------------------------------------------------
# Busy gating
# ----------------------------------------------------------------------
def test_update_analysis_is_gated_while_a_pull_runs(make_app, analysis_provider):
    """409 with ``{"busy": true}``, and nothing recomputed.

    Both halves matter. The status code is what the assignment asks for; the
    unchanged call count is what proves the request was refused rather than
    served and then labelled busy.
    """
    busy = pull_data.MemoryState(busy=True)
    app = make_app(state=busy, analysis_provider=analysis_provider)
    client = app.test_client()
    before = analysis_provider.calls

    response = client.post("/update-analysis")

    assert response.status_code == 409
    assert response.get_json()["busy"] is True
    assert analysis_provider.calls == before


def test_pull_data_is_gated_while_a_pull_runs(make_app, pull_runner):
    """409 with ``{"busy": true}``, and no second scrape started."""
    busy = pull_data.MemoryState(busy=True)
    client = make_app(state=busy, pull_runner=pull_runner).test_client()

    response = client.post("/pull-data")

    assert response.status_code == 409
    assert response.get_json()["busy"] is True
    assert pull_runner.calls == 0


def test_busy_responses_explain_themselves(make_app):
    """The 409 carries a message and the current status, for the page to show."""
    client = make_app(state=pull_data.MemoryState(busy=True)).test_client()

    body = client.post("/pull-data").get_json()

    assert "already running" in body["message"]
    assert body["status"]["running"] is True


def test_gating_follows_the_flag_rather_than_the_clock(client, state, pull_runner):
    """The gate opens and closes with the flag, in either order, at once.

    This is what makes the busy tests deterministic: no scrape is started, and
    nothing is waited for -- the flag is set, the request is refused, the flag
    is cleared, the request succeeds.
    """
    assert client.post("/pull-data").status_code == 200

    state.begin()
    assert client.post("/pull-data").status_code == 409
    assert client.post("/update-analysis").status_code == 409

    state.end()
    assert client.post("/pull-data").status_code == 200
    assert client.post("/update-analysis").status_code == 200
    assert pull_runner.calls == 2


def test_pull_data_reports_a_race_as_busy(make_app):
    """A runner that finds the flag taken answers 409, not 500.

    The narrow window between the route's check and the runner claiming the
    flag. Being busy is not a failure, so it does not become one here either.
    """

    def runner():
        raise pull_data.PullInProgress("claimed by another request")

    client = make_app(pull_runner=runner).test_client()
    response = client.post("/pull-data")

    assert response.status_code == 409
    assert response.get_json()["busy"] is True


# ----------------------------------------------------------------------
# Error paths
# ----------------------------------------------------------------------
def test_pull_data_reports_a_failing_stage(make_app):
    """A stage that raises yields a non-200 response carrying the reason."""

    def runner():
        raise doubles.LoaderFailure("the database refused the write")

    response = make_app(pull_runner=runner).test_client().post("/pull-data")
    body = response.get_json()

    assert response.status_code == 500
    assert body["ok"] is False
    assert body["busy"] is False
    assert "the database refused the write" in body["error"]


def test_a_failed_pull_leaves_the_button_usable(make_app, state):
    """After a failure the flag is clear, so the button can be pressed again."""
    calls = {"n": 0}

    def runner():
        calls["n"] += 1
        if calls["n"] == 1:
            raise doubles.LoaderFailure("transient")
        return {"inserted": 1, "total": 1}

    client = make_app(state=state, pull_runner=runner).test_client()

    assert client.post("/pull-data").status_code == 500
    assert state.is_busy() is False
    assert client.post("/pull-data").status_code == 200


def test_a_failed_pull_records_nothing(make_app):
    """A pull that failed is not counted as one that happened."""

    def runner():
        raise doubles.LoaderFailure("transient")

    app = make_app(pull_runner=runner)
    app.test_client().post("/pull-data")

    assert flask_app.get_services(app).pulls == []


# ----------------------------------------------------------------------
# Fixtures used only here
# ----------------------------------------------------------------------
@pytest.fixture
def scraper() -> doubles.FakeScraper:
    """A scraper serving the twelve sample rows from memory."""
    return doubles.FakeScraper(doubles.SAMPLE_RAW_ROWS)


@pytest.fixture
def loader() -> doubles.RecordingLoader:
    """A loader that records what it was asked to write, and writes nothing."""
    return doubles.RecordingLoader()


@pytest.fixture
def client_with_pipeline(make_app, state, scraper, loader):
    """An application whose Pull Data runs the real pipeline between two fakes."""
    runner = doubles.FakePullRunner(
        lambda: pull_data.run_pipeline(
            scraper=scraper,
            standardizer=doubles.fake_standardizer,
            loader=loader,
            state=state,
        )
    )
    return make_app(state=state, pull_runner=runner).test_client()
