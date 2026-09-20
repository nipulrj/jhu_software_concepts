"""Pull, update, render -- the whole system, with only the network faked.

Marked ``integration``.  These tests wire the real cleaner, the real loader, the
real ORM queries and the real template together and drive them through Flask's
test client.  The one thing replaced is the HTTP request to Grad Cafe: a
:class:`doubles.FakeScraper` serves rows from memory, so the suite reaches no
network and no scrape takes minutes.

The sequence under test is the one the two buttons describe:

1. ``POST /pull-data`` -- rows land in PostgreSQL;
2. ``POST /update-analysis`` -- the figures are recomputed from them;
3. ``GET /analysis`` -- the page shows the new figures, correctly formatted.

Because ``/analysis`` renders a held snapshot, each step is separately
observable: the page does not change until the update runs, which is what makes
this a sequence rather than three reads of the same query.
"""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

import doubles
import flask_app
import load_data
import pull_data
import query_data
from doubles import PERCENT_PATTERN, TWO_DECIMAL_PERCENT

pytestmark = pytest.mark.integration


def page_text(client) -> str:
    """The analysis page as visible text."""
    html = client.get("/analysis").get_data(as_text=True)
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)


def answer_for(client, number: int) -> str:
    """The rendered answer block for one question."""
    html = client.get("/analysis").get_data(as_text=True)
    block = BeautifulSoup(html, "html.parser").select_one(
        '[data-testid="answer-{0}"]'.format(number)
    )
    return block.get_text(" ", strip=True) if block else ""


# ----------------------------------------------------------------------
# Pull -> update -> render
# ----------------------------------------------------------------------
def test_end_to_end_pull_update_render(live_client, empty_database):
    """The full sequence, against a real database and the real ORM queries."""
    assert load_data.row_count(empty_database) == 0

    # 1. Pull: the fake scraper's records reach PostgreSQL.
    pulled = live_client.post("/pull-data")
    assert pulled.status_code == 200
    assert pulled.get_json()["ok"] is True
    assert load_data.row_count(empty_database) == len(doubles.SAMPLE_RAW_ROWS)

    # 2. Update: the analysis is recomputed, and says so.
    updated = live_client.post("/update-analysis")
    assert updated.status_code == 200
    assert updated.get_json()["ok"] is True
    assert updated.get_json()["questions"] == 11

    # 3. Render: the page shows figures drawn from the rows just loaded.
    text = page_text(live_client)
    assert "Answer:" in text
    assert "Fall 2026 applicant count: {0}".format(doubles.SAMPLE_FALL_2026) in text


def test_the_page_does_not_change_until_the_analysis_is_updated(
    live_client, empty_database
):
    """Pulling alone does not move the figures; that is Update Analysis's job.

    The property the whole three-step sequence rests on -- and the reason the
    second button is not decorative.
    """
    live_client.post("/update-analysis")
    before = answer_for(live_client, 1)
    assert "count: 0" in before

    live_client.post("/pull-data")
    assert load_data.row_count(empty_database) == len(doubles.SAMPLE_RAW_ROWS)
    assert answer_for(live_client, 1) == before

    live_client.post("/update-analysis")
    assert answer_for(live_client, 1) != before


def test_rendered_analysis_matches_the_rows_that_were_pulled(
    live_client, empty_database
):
    """Spot-checked against the fixture: the figures are of the loaded data.

    Each expectation is derived from the twelve sample rows rather than
    hard-coded from a previous run, so a change to the fixture that should move
    a figure will move it here too.
    """
    live_client.post("/pull-data")
    live_client.post("/update-analysis")

    rows = query_data.fetch_applicants(empty_database)
    fall_2026 = [row for row in rows if row["term"] == "Fall 2026"]
    with_nationality = [row for row in rows if row["us_or_international"]]
    international = [
        row for row in with_nationality if row["us_or_international"] == "International"
    ]

    assert "count: {0}".format(len(fall_2026)) in answer_for(live_client, 1)
    assert query_data.fmt_pct(
        100.0 * len(international) / len(with_nationality)
    ) in answer_for(live_client, 2)


def test_rendered_values_are_formatted_correctly(live_client):
    """Every percentage on the page of real figures carries two decimals."""
    live_client.post("/pull-data")
    live_client.post("/update-analysis")

    percentages = PERCENT_PATTERN.findall(page_text(live_client))

    assert percentages, "the loaded data should produce at least one percentage"
    wrong = [p for p in percentages if not TWO_DECIMAL_PERCENT.fullmatch(p)]
    assert not wrong, "percentages not shown to two decimals: {0}".format(wrong)


def test_every_question_is_answered_and_labelled(live_client):
    """All eleven analyses run against the loaded rows, each one labelled."""
    live_client.post("/pull-data")
    live_client.post("/update-analysis")

    html = live_client.get("/analysis").get_data(as_text=True)
    page = BeautifulSoup(html, "html.parser")

    for number in range(1, 12):
        block = page.select_one('[data-testid="answer-{0}"]'.format(number))
        assert block is not None, "question {0} was not rendered".format(number)
        assert block.get_text(" ", strip=True).startswith("Answer:")


def test_the_orm_and_the_raw_sql_agree_on_the_loaded_rows(seeded_database):
    """Both halves of the data layer answer identically, question by question.

    Worth an integration test rather than a unit one: the two are written
    independently -- hand-written SQL on one side, ``select()`` on the other --
    and agreeing on real rows is the only thing that shows they mean the same.
    """
    import orm_queries

    by_sql = {result.number: result.answer for result in query_data.run_all(seeded_database)}
    by_orm = {result.number: result.answer for result in orm_queries.answer_all()}

    assert by_orm == by_sql


# ----------------------------------------------------------------------
# Multiple pulls
# ----------------------------------------------------------------------
def test_two_overlapping_pulls_stay_consistent(overlapping_client, empty_database):
    """Pulling twice over overlapping data respects the uniqueness policy.

    Consecutive real pulls overlap heavily -- each walks the newest results --
    so this is the ordinary case, not an edge one.
    """
    overlapping_client.post("/pull-data")
    overlapping_client.post("/update-analysis")
    first_total = load_data.row_count(empty_database)
    first_answer = answer_for(overlapping_client, 1)

    overlapping_client.post("/pull-data")
    overlapping_client.post("/update-analysis")

    expected = len(
        {row["entry_id"] for row in doubles.SAMPLE_RAW_ROWS}
        | {row["entry_id"] for row in doubles.SAMPLE_OVERLAPPING_ROWS}
    )
    assert first_total == len(doubles.SAMPLE_RAW_ROWS)
    assert load_data.row_count(empty_database) == expected
    assert answer_for(overlapping_client, 1) != first_answer


def test_pulling_the_same_batch_twice_changes_nothing_on_the_page(
    live_client, empty_database
):
    """A pull that found only known entries leaves the figures where they were."""
    live_client.post("/pull-data")
    live_client.post("/update-analysis")
    before = page_text(live_client)

    live_client.post("/pull-data")
    live_client.post("/update-analysis")

    assert load_data.row_count(empty_database) == len(doubles.SAMPLE_RAW_ROWS)
    assert answer_for(live_client, 1) in before


def test_the_page_reports_the_completed_pull(live_client):
    """After a pull the status says what happened and prompts the next step."""
    live_client.post("/pull-data")

    status = live_client.get("/status").get_json()

    assert status["running"] is False
    assert status["state"] == "done"
    assert "Added {0:,} new records".format(len(doubles.SAMPLE_RAW_ROWS)) in status["message"]
    assert "Update Analysis" in status["message"]


# ----------------------------------------------------------------------
# Busy gating, end to end
# ----------------------------------------------------------------------
def test_a_pull_in_flight_blocks_an_update_end_to_end(
    live_client, state, empty_database
):
    """With real data behind it, a busy update still refuses and changes nothing."""
    live_client.post("/pull-data")
    live_client.post("/update-analysis")
    before = answer_for(live_client, 1)

    state.begin()
    try:
        refused = live_client.post("/update-analysis")
    finally:
        state.end()

    assert refused.status_code == 409
    assert refused.get_json()["busy"] is True
    assert answer_for(live_client, 1) == before


def test_an_empty_database_renders_rather_than_failing(live_client, empty_database):
    """The application is usable before anything has been pulled.

    Every analysis has to answer "nothing yet" without dividing by zero, which
    is easy to get wrong in a percentage.
    """
    live_client.post("/update-analysis")

    text = page_text(live_client)

    assert "Fall 2026 applicant count: 0" in text
    assert "n/a" in text


# ----------------------------------------------------------------------
# Fixtures used only here
# ----------------------------------------------------------------------
def live_app(make_app, state, scraper):
    """An application with nothing faked but the scrape.

    The real cleaner, the real loader and the real ORM-backed analysis -- so
    ``analysis_provider`` is left at its default rather than injected.
    """
    runner = doubles.FakePullRunner(
        lambda: pull_data.run_pipeline(
            scraper=scraper,
            standardizer=doubles.fake_standardizer,
            state=state,
        )
    )
    return make_app(
        state=state,
        pull_runner=runner,
        analysis_provider=flask_app.default_analysis_provider,
    )


@pytest.fixture
def live_client(make_app, state, empty_database):
    """A whole application whose scraper serves the twelve sample rows."""
    scraper = doubles.FakeScraper(doubles.SAMPLE_RAW_ROWS)
    return live_app(make_app, state, scraper).test_client()


@pytest.fixture
def overlapping_client(make_app, state, empty_database):
    """A whole application whose second pull overlaps its first."""
    scraper = doubles.FakeScraper(
        doubles.SAMPLE_RAW_ROWS, doubles.SAMPLE_OVERLAPPING_ROWS
    )
    return live_app(make_app, state, scraper).test_client()
