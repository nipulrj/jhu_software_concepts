"""What a pull writes into PostgreSQL, and what a second pull does not.

Marked ``db``.  These are the only tests that need a server: they run against
the scratch database named by ``TEST_DATABASE_URL``, which the fixtures refuse
to let be the application's own, and they empty the ``applicants`` table before
each test so nothing leaks from one to the next.

The schema is created by :func:`load_data.create_table` rather than by hand, so
what is asserted here is the schema the application really ships.
"""

from __future__ import annotations

import datetime as dt

import psycopg
import pytest

import doubles
import flask_app
import load_data
import models
import pull_data
import query_data

pytestmark = pytest.mark.db

#: Fields a stored row must carry a value for. Everything else is nullable,
#: because Grad Cafe entries are self-submitted and most leave the rest blank.
NON_NULL_FIELDS = ("p_id", "program", "status", "term", "degree")


def count_rows(connection: psycopg.Connection) -> int:
    """How many rows the applicants table holds right now."""
    return load_data.row_count(connection)


# ----------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------
def test_create_table_builds_the_required_schema(empty_database):
    """Every required column exists, with ``p_id`` as the primary key."""
    with empty_database.cursor() as cursor:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s ORDER BY ordinal_position",
            (load_data.TABLE_NAME,),
        )
        columns = [row[0] for row in cursor.fetchall()]

    assert columns == list(load_data.COLUMNS)


def test_p_id_is_the_primary_key(empty_database):
    """The uniqueness policy is enforced by the database, not by convention."""
    with empty_database.cursor() as cursor:
        cursor.execute(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid "
            "AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = %s::regclass AND i.indisprimary",
            (load_data.TABLE_NAME,),
        )
        key = [row[0] for row in cursor.fetchall()]

    assert key == ["p_id"]


def test_the_loader_and_the_model_describe_the_same_columns():
    """The raw-SQL schema and the ORM mapping cannot drift apart unnoticed.

    They are declared separately -- the loader owns the ``CREATE TABLE``, the
    model describes what is already there -- so this is the test that keeps the
    two definitions honest.
    """
    assert tuple(load_data.COLUMNS) == models.REQUIRED_FIELDS
    assert tuple(load_data.COLUMNS) == tuple(query_data.APPLICANT_FIELDS)
    assert tuple(models.Applicant.__table__.columns.keys()) == models.REQUIRED_FIELDS


def test_the_table_starts_empty(empty_database):
    """The precondition the insert tests rest on, asserted rather than assumed."""
    assert count_rows(empty_database) == 0


# ----------------------------------------------------------------------
# Inserting on pull
# ----------------------------------------------------------------------
def test_pull_data_inserts_new_rows(pull_client, empty_database):
    """Before: the table is empty.  After ``POST /pull-data``: it is not."""
    assert count_rows(empty_database) == 0

    response = pull_client.post("/pull-data")

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert count_rows(empty_database) == len(doubles.SAMPLE_RAW_ROWS)


def test_inserted_rows_carry_the_required_fields(pull_client, empty_database):
    """Each new row has a value in every field that must not be null."""
    pull_client.post("/pull-data")

    rows = query_data.fetch_applicants(empty_database)

    assert len(rows) == len(doubles.SAMPLE_RAW_ROWS)
    for row in rows:
        for field in NON_NULL_FIELDS:
            assert row[field] is not None, "{0} is null in {1}".format(field, row["p_id"])


def test_inserted_rows_keep_their_types(pull_client, empty_database):
    """Dates arrive as dates and scores as numbers, not as strings."""
    pull_client.post("/pull-data")

    row = query_data.fetch_applicant(empty_database, 900001)

    assert row["p_id"] == 900001
    assert isinstance(row["date_added"], dt.date)
    assert row["gpa"] == pytest.approx(3.90)
    assert row["gre"] == pytest.approx(168.0)
    assert row["term"] == "Fall 2026"
    assert row["status"] == "Accepted"
    assert row["program"].endswith("Massachusetts Institute of Technology (MIT)")


def test_inserted_rows_carry_the_llm_columns(pull_client, empty_database):
    """The standardizer's two columns are stored alongside the original fields."""
    pull_client.post("/pull-data")

    row = query_data.fetch_applicant(empty_database, 900003)

    assert row["llm_generated_program"] == "Computer Science"
    assert row["llm_generated_university"] == "Carnegie Mellon University"


def test_the_pull_reports_what_it_inserted(pull_client, empty_database):
    """The summary in the response matches what actually landed."""
    summary = pull_client.post("/pull-data").get_json()["summary"]

    assert summary["scraped"] == len(doubles.SAMPLE_RAW_ROWS)
    assert summary["inserted"] == len(doubles.SAMPLE_RAW_ROWS)
    assert summary["total"] == count_rows(empty_database)


# ----------------------------------------------------------------------
# Idempotency and uniqueness
# ----------------------------------------------------------------------
def test_pulling_the_same_data_twice_does_not_duplicate_rows(
    pull_client, empty_database
):
    """The uniqueness policy: the same entry loaded twice is one row.

    The fake scraper serves the same batch again, which is the realistic case --
    a pull walks the newest results, so consecutive pulls overlap heavily.
    """
    pull_client.post("/pull-data")
    after_first = count_rows(empty_database)

    pull_client.post("/pull-data")

    assert count_rows(empty_database) == after_first


def test_a_second_pull_reports_nothing_new(pull_client):
    """A pull that found only known entries says so rather than claiming inserts."""
    pull_client.post("/pull-data")

    summary = pull_client.post("/pull-data").get_json()["summary"]

    assert summary["inserted"] == 0
    assert summary["updated"] == len(doubles.SAMPLE_RAW_ROWS)


def test_overlapping_pulls_add_only_what_is_new(overlap_client, empty_database):
    """Two pulls with overlapping data leave one row per distinct result id."""
    overlap_client.post("/pull-data")
    assert count_rows(empty_database) == len(doubles.SAMPLE_RAW_ROWS)

    overlap_client.post("/pull-data")

    expected = len(
        {row["entry_id"] for row in doubles.SAMPLE_RAW_ROWS}
        | {row["entry_id"] for row in doubles.SAMPLE_OVERLAPPING_ROWS}
    )
    assert count_rows(empty_database) == expected


def test_a_repeated_row_is_refreshed_rather_than_ignored(overlap_client, empty_database):
    """An applicant who edits their entry updates the row we already hold."""
    overlap_client.post("/pull-data")
    assert query_data.fetch_applicant(empty_database, 900006)["status"] == "Wait listed"

    overlap_client.post("/pull-data")

    assert query_data.fetch_applicant(empty_database, 900006)["status"] == "Accepted"


def test_a_refresh_never_replaces_a_known_value_with_null(
    empty_database, sample_records
):
    """A later scrape missing a field leaves the value the first one captured.

    This is what the loader's ``COALESCE`` is for: the standardizer can be
    skipped on one run without wiping the LLM columns a previous run filled in.
    """
    load_data.load_into_database(records=sample_records, verbose=False)
    before = query_data.fetch_applicant(empty_database, 900001)

    stripped = dict(sample_records[0])
    stripped["llm-generated-university"] = None
    stripped["gpa"] = None
    load_data.load_into_database(records=[stripped], verbose=False)

    after = query_data.fetch_applicant(empty_database, 900001)
    assert after["llm_generated_university"] == before["llm_generated_university"]
    assert after["gpa"] == before["gpa"]


def test_a_genuine_change_still_updates(empty_database, sample_records):
    """COALESCE keeps known values; it does not freeze them."""
    load_data.load_into_database(records=sample_records, verbose=False)

    edited = dict(sample_records[0], applicant_status="Wait listed")
    load_data.load_into_database(records=[edited], verbose=False)

    assert query_data.fetch_applicant(empty_database, 900001)["status"] == "Wait listed"


def test_one_file_holding_the_same_entry_twice_inserts_one_row(
    empty_database, sample_records
):
    """Duplicates inside a single batch are collapsed before they reach SQL.

    ``ON CONFLICT`` cannot help here -- PostgreSQL refuses a statement that
    touches the same row twice -- so the loader de-duplicates first.
    """
    doubled = sample_records + sample_records

    summary = load_data.load_into_database(records=doubled, verbose=False)

    assert summary["read"] == len(doubled)
    assert summary["written"] == len(sample_records)
    assert count_rows(empty_database) == len(sample_records)


# ----------------------------------------------------------------------
# Reading rows back
# ----------------------------------------------------------------------
def test_fetch_applicant_returns_a_dict_with_the_required_keys(seeded_database):
    """The simple query function: one row, as a dict keyed by the schema."""
    row = query_data.fetch_applicant(seeded_database, 900004)

    assert isinstance(row, dict)
    assert set(row) == set(models.REQUIRED_FIELDS)
    assert row["p_id"] == 900004
    assert row["degree"] == "Masters"


def test_fetch_applicant_returns_none_for_an_unknown_id(seeded_database):
    """A result id we do not hold is absent, not an exception."""
    assert query_data.fetch_applicant(seeded_database, 1) is None


def test_fetch_applicants_returns_every_row(seeded_database):
    """Read back with no filter and the whole table comes out."""
    rows = query_data.fetch_applicants(seeded_database)

    assert len(rows) == len(doubles.SAMPLE_RAW_ROWS)
    assert all(set(row) == set(models.REQUIRED_FIELDS) for row in rows)


def test_fetch_applicants_honours_its_limit(seeded_database):
    """The limit is applied, newest result id first."""
    rows = query_data.fetch_applicants(seeded_database, limit=3)

    assert [row["p_id"] for row in rows] == [900012, 900011, 900010]


def test_fetch_applicants_keeps_missing_values_as_none(seeded_database):
    """A field the applicant left blank is a key with ``None``, never absent."""
    row = query_data.fetch_applicant(seeded_database, 900003)

    assert "gpa" in row
    assert row["gpa"] is None


def test_the_orm_reads_the_rows_the_loader_wrote(seeded_database, database):
    """The psycopg half and the SQLAlchemy half see one table, not two."""
    with models.session_scope() as session:
        applicant = session.get(models.Applicant, 900001)
        as_dict = applicant.as_dict()

    assert as_dict["program"] == query_data.fetch_applicant(
        seeded_database, 900001
    )["program"]
    assert set(as_dict) == set(models.REQUIRED_FIELDS)
    assert "900001" in repr(applicant)


# ----------------------------------------------------------------------
# Error paths
# ----------------------------------------------------------------------
def test_a_failing_loader_writes_nothing(make_app, state, empty_database):
    """The negative case: the pull fails, and the table is exactly as it was.

    The real loader opens one transaction and commits at the end, so a stage
    that raises before it cannot leave a partial write behind. Here the loader
    itself raises, which is the harsher version of the same guarantee.
    """
    failing = doubles.FailingLoader()
    runner = doubles.FakePullRunner(
        lambda: pull_data.run_pipeline(
            scraper=doubles.FakeScraper(doubles.SAMPLE_RAW_ROWS),
            standardizer=doubles.fake_standardizer,
            loader=failing,
            state=state,
        )
    )
    client = make_app(state=state, pull_runner=runner).test_client()

    response = client.post("/pull-data")

    assert response.status_code == 500
    assert failing.calls == 1
    assert count_rows(empty_database) == 0


def test_a_loader_error_is_recorded_in_the_status(make_app, state, empty_database):
    """The failure reaches the status the page polls, not only the response."""
    runner = doubles.FakePullRunner(
        lambda: pull_data.run_pipeline(
            scraper=doubles.FakeScraper(doubles.SAMPLE_RAW_ROWS),
            standardizer=doubles.fake_standardizer,
            loader=doubles.FailingLoader("out of disk"),
            state=state,
        )
    )
    client = make_app(state=state, pull_runner=runner).test_client()

    client.post("/pull-data")
    status = client.get("/status").get_json()

    assert status["state"] == "error"
    assert "out of disk" in status["message"]
    assert status["running"] is False


def test_loading_an_unreachable_database_explains_itself(monkeypatch, sample_records):
    """A server that is not there produces advice rather than a psycopg traceback.

    The refusal is injected rather than provoked by dialling a dead port: libpq
    spends over two minutes retrying one of those, and how long it takes depends
    on the machine. Raising the error psycopg would raise exercises the same
    branch, instantly and identically everywhere.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/nothing")

    def refuse(**_kwargs):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(psycopg, "connect", refuse)

    with pytest.raises(load_data.LoaderError) as raised:
        load_data.load_into_database(records=sample_records, verbose=False)

    assert "Could not connect to PostgreSQL" in str(raised.value)
    assert "127.0.0.1:1/nothing" in str(raised.value)


# ----------------------------------------------------------------------
# Fixtures used only here
# ----------------------------------------------------------------------
def pull_app(make_app, state, scraper):
    """An application whose Pull Data runs the real pipeline into the database."""
    runner = doubles.FakePullRunner(
        lambda: pull_data.run_pipeline(
            scraper=scraper,
            standardizer=doubles.fake_standardizer,
            state=state,
        )
    )
    return make_app(state=state, pull_runner=runner)


@pytest.fixture
def pull_client(make_app, state, empty_database):
    """Pull Data writes the twelve sample rows, every time it is pressed."""
    scraper = doubles.FakeScraper(doubles.SAMPLE_RAW_ROWS)
    return pull_app(make_app, state, scraper).test_client()


@pytest.fixture
def overlap_client(make_app, state, empty_database):
    """Pull Data writes the sample rows first, then the overlapping batch."""
    scraper = doubles.FakeScraper(
        doubles.SAMPLE_RAW_ROWS, doubles.SAMPLE_OVERLAPPING_ROWS
    )
    return pull_app(make_app, state, scraper).test_client()
