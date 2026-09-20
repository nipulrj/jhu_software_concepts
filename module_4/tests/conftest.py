"""Shared fixtures for the Module 4 suite.

Putting ``src/`` on ``sys.path`` here is what lets the tests ``import
flask_app`` rather than reaching through a package prefix, and is the only
import-time side effect in the suite.

The fixtures come in three groups:

**Application** -- :func:`app` and :func:`client` build a Flask app through the
factory with an in-memory busy flag, a synchronous pull runner and a canned
analysis provider.  Nothing in that app reaches the network or a database, so
the page and button tests run in milliseconds.

**Database** -- :func:`db_connection` and :func:`empty_database` connect to the
scratch database named by ``TEST_DATABASE_URL`` and leave the ``applicants``
table empty before each test.  They refuse to run against the application
database, so a mistyped variable cannot truncate real rows.

**Data** -- :func:`sample_raw_rows` and friends serve the fixtures in
:mod:`tests.doubles`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import psycopg  # noqa: E402  (after the sys.path set-up above)

import db_config  # noqa: E402
import flask_app  # noqa: E402
import load_data  # noqa: E402
import models  # noqa: E402
import pull_data  # noqa: E402
from query_data import QuestionResult  # noqa: E402

import doubles  # noqa: E402


# ----------------------------------------------------------------------
# Canned analysis
# ----------------------------------------------------------------------
def make_question(
    number: int,
    question: str,
    answer_lines: List[str],
    **kwargs: Any,
) -> QuestionResult:
    """One answered question, for tests that do not need a database."""
    kwargs.setdefault("sql", "SELECT {0}".format(number))
    kwargs.setdefault("explanation", "Explanation for question {0}.".format(number))
    return QuestionResult(
        number=number, question=question, answer_lines=answer_lines, **kwargs
    )


#: A small, fully formatted analysis: a count, two percentages, an average and
#: one question of my own, with a table and a caveat so every branch of the
#: template is exercised.  Every percentage carries exactly two decimals,
#: because that is the property the formatting tests assert about the page.
CANNED_RESULTS: List[QuestionResult] = [
    make_question(1, "How many entries are from Fall 2026 applicants?",
                  ["Fall 2026 applicant count: 1,234"]),
    make_question(2, "What percentage are international students?",
                  ["Percent international: 39.28%"]),
    make_question(3, "What are the average GPA and GRE scores?",
                  ["Average GPA: 3.62", "Average GRE Quantitative: 162.41"],
                  table={"columns": ["Metric", "Average"],
                         "rows": [["GPA (0-4.0)", "3.62"]]},
                  supporting=["Reported by 41.07% of entries."],
                  caveat="Two of these are not usable as test scores."),
    make_question(5, "What percentage of Fall 2026 entries are acceptances?",
                  ["Percent acceptances: 27.09%"]),
    make_question(10, "Which university is hardest to get into?",
                  ["Hardest: Stanford University at 18.47%"],
                  original=True),
]


# ----------------------------------------------------------------------
# Application fixtures
# ----------------------------------------------------------------------
@pytest.fixture
def state() -> pull_data.MemoryState:
    """The busy flag the application under test shares with its pull.

    In-memory, so "a pull is in progress" is one assignment away and no test
    ever waits for anything.
    """
    return pull_data.MemoryState()


@pytest.fixture
def analysis_provider() -> doubles.CountingProvider:
    """A provider serving :data:`CANNED_RESULTS` and counting its calls."""
    return doubles.CountingProvider(CANNED_RESULTS)


@pytest.fixture
def pull_runner() -> doubles.FakePullRunner:
    """A Pull Data runner that does nothing but record that it was called."""
    return doubles.FakePullRunner(lambda: {"inserted": 0, "total": 0, "scraped": 0})


@pytest.fixture
def make_app(
    state: pull_data.MemoryState,
    analysis_provider: doubles.CountingProvider,
    pull_runner: doubles.FakePullRunner,
) -> Callable[..., Any]:
    """Build an application through the factory, defaults already injected.

    Returns a callable so a test can override one collaborator without
    restating the other two.
    """

    def build(**overrides: Any):
        settings = {"TESTING": True}
        settings.update(overrides.pop("config", {}))
        return flask_app.create_app(
            settings,
            state=overrides.pop("state", state),
            pull_runner=overrides.pop("pull_runner", pull_runner),
            analysis_provider=overrides.pop("analysis_provider", analysis_provider),
            **overrides,
        )

    return build


@pytest.fixture
def app(make_app: Callable[..., Any]):
    """A testable application: no network, no database, no clock."""
    return make_app()


@pytest.fixture
def client(app):
    """Flask's test client for :func:`app`.

    Every request in this suite goes through this -- there is no browser
    automation anywhere, and no test clicks anything.
    """
    return app.test_client()


@pytest.fixture
def services(app) -> flask_app.Services:
    """The collaborators :func:`app` was built with."""
    return flask_app.get_services(app)


# ----------------------------------------------------------------------
# Data fixtures
# ----------------------------------------------------------------------
@pytest.fixture
def sample_raw_rows() -> List[Dict[str, Any]]:
    """Twelve raw scraped rows, in the shape :mod:`scrape` produces."""
    return [dict(row) for row in doubles.SAMPLE_RAW_ROWS]


@pytest.fixture
def overlapping_raw_rows() -> List[Dict[str, Any]]:
    """A second batch that repeats two of the first batch's rows and adds two."""
    return [dict(row) for row in doubles.SAMPLE_OVERLAPPING_ROWS]


@pytest.fixture
def sample_records(sample_raw_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The sample rows, cleaned and standardized, ready for the loader.

    Produced by the *real* cleaner, so the records the database tests insert
    are the records the application would really have inserted.
    """
    import clean

    return doubles.fake_standardizer(clean.clean_data(sample_raw_rows))


# ----------------------------------------------------------------------
# Database fixtures
# ----------------------------------------------------------------------
@pytest.fixture(scope="session")
def test_database_url() -> str:
    """The scratch database the ``db`` and ``integration`` tests may write to.

    Read from ``TEST_DATABASE_URL``, which must be set and must not name the
    same database as ``DATABASE_URL``: these tests truncate and drop tables, and
    the guard is what stops a mistyped variable from doing that to real rows.
    """
    db_config.load_dotenv()
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail(
            "TEST_DATABASE_URL is not set. The db and integration tests need a "
            "scratch PostgreSQL database -- create one with `createdb "
            "gradcafe_test` and point TEST_DATABASE_URL at it (see "
            "module_4/.env.example). The tests refuse to use DATABASE_URL, "
            "because they empty the table they test."
        )

    application_url = os.environ.get("DATABASE_URL")
    if application_url:
        test_db = db_config.parse_url(url).get("dbname")
        app_db = db_config.parse_url(application_url).get("dbname")
        if test_db == app_db:
            pytest.fail(
                "TEST_DATABASE_URL and DATABASE_URL both name {0!r}. These tests "
                "empty the applicants table; point TEST_DATABASE_URL at a "
                "separate database.".format(test_db)
            )
    return url


@pytest.fixture
def database(test_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Point the whole application at the scratch database for one test.

    Sets ``DATABASE_URL``, which :mod:`db_config` applies over everything else,
    so psycopg and SQLAlchemy both follow.  The ORM's cached Engines are dropped
    on the way in and on the way out, so no pooled connection outlives the
    redirect.
    """
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    models.reset_engine()
    try:
        yield test_database_url
    finally:
        models.reset_engine()


@pytest.fixture
def db_connection(database: str) -> Iterator[psycopg.Connection]:
    """An open connection to the scratch database, committed as you go."""
    try:
        connection = psycopg.connect(**db_config.connect_kwargs(), autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.fail(
            "Could not connect to the test database at {where}:\n  {exc}\n"
            "Start PostgreSQL and create the database named by "
            "TEST_DATABASE_URL.".format(where=db_config.describe(), exc=exc)
        )
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def empty_database(db_connection: psycopg.Connection) -> psycopg.Connection:
    """The scratch database with the schema in place and no rows in it.

    Created through :func:`load_data.create_table` rather than by hand, so what
    the tests assert against is the schema the application actually ships.
    """
    load_data.create_table(db_connection, recreate=True)
    return db_connection


@pytest.fixture
def seeded_database(
    empty_database: psycopg.Connection, sample_records: List[Dict[str, Any]]
) -> psycopg.Connection:
    """The scratch database holding the twelve sample records."""
    load_data.insert_rows(empty_database, load_data.build_rows(sample_records)[0])
    return empty_database
