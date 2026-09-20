"""Where the application decides which database to talk to.

Marked ``db``: this is the database layer's configuration, and it is what the
rest of the suite relies on to redirect the application at a scratch server.

Nothing here connects to anything.  The environment is the input and a dict of
connection keywords is the output, so every case -- including the ones that
would be awkward to arrange against a real server, like a password full of
punctuation -- is a plain function call.
"""

from __future__ import annotations

import os

import pytest

import db_config

pytestmark = pytest.mark.db

# The autouse fixture below replaces load_dotenv with a no-op so the other tests
# see a clean environment.  The tests of the reader itself need the real one, so
# it is captured here, before anything has a chance to patch it.
_real_load_dotenv = db_config.load_dotenv


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Start each test from an environment that says nothing.

    Otherwise a developer's ``.env`` -- or CI's exported variables -- would
    decide what these tests see.
    """
    for name in ("DATABASE_URL", "PGHOST", "PGPORT", "PGDATABASE", "PGUSER",
                 "PGPASSWORD", "TEST_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    # load_dotenv() would put them straight back.
    monkeypatch.setattr(db_config, "load_dotenv", lambda *_args, **_kwargs: None)


# ----------------------------------------------------------------------
# Defaults and the PG* variables
# ----------------------------------------------------------------------
def test_defaults_when_the_environment_says_nothing():
    """A default database name is harmless; a default password would not be."""
    kwargs = db_config.connect_kwargs()

    assert kwargs == {
        "host": "localhost",
        "port": 5432,
        "dbname": "gradcafe",
        "user": "postgres",
    }
    assert "password" not in kwargs


def test_pg_variables_are_read(monkeypatch):
    """The five standard libpq variables configure the connection."""
    monkeypatch.setenv("PGHOST", "db.internal")
    monkeypatch.setenv("PGPORT", "5555")
    monkeypatch.setenv("PGDATABASE", "other")
    monkeypatch.setenv("PGUSER", "reader")
    monkeypatch.setenv("PGPASSWORD", "hunter2")

    assert db_config.connect_kwargs() == {
        "host": "db.internal",
        "port": 5555,
        "dbname": "other",
        "user": "reader",
        "password": "hunter2",
    }


def test_an_empty_password_is_left_out(monkeypatch):
    """An empty string is not a password; omitting it lets libpq use ~/.pgpass."""
    monkeypatch.setenv("PGPASSWORD", "")

    assert "password" not in db_config.connect_kwargs()


def test_the_port_is_an_integer(monkeypatch):
    """psycopg wants a number, and the environment only ever holds strings."""
    monkeypatch.setenv("PGPORT", "6000")

    assert db_config.connect_kwargs()["port"] == 6000


# ----------------------------------------------------------------------
# DATABASE_URL
# ----------------------------------------------------------------------
def test_database_url_supplies_the_whole_connection(monkeypatch):
    """One variable is enough to describe a connection."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host.test:5433/mydb")

    assert db_config.connect_kwargs() == {
        "host": "host.test",
        "port": 5433,
        "dbname": "mydb",
        "user": "u",
        "password": "p",
    }


def test_database_url_wins_over_the_pg_variables(monkeypatch):
    """The override the tests depend on: DATABASE_URL is applied last."""
    monkeypatch.setenv("PGDATABASE", "production")
    monkeypatch.setenv("PGHOST", "production.internal")
    monkeypatch.setenv("DATABASE_URL", "postgresql://tester@localhost:5432/scratch")

    kwargs = db_config.connect_kwargs()

    assert kwargs["dbname"] == "scratch"
    assert kwargs["host"] == "localhost"


def test_a_partial_url_is_completed_by_the_pg_variables(monkeypatch):
    """What the URL leaves out, the other variables fill in."""
    monkeypatch.setenv("PGUSER", "reader")
    monkeypatch.setenv("PGPASSWORD", "hunter2")
    monkeypatch.setenv("DATABASE_URL", "postgresql://host.test/mydb")

    kwargs = db_config.connect_kwargs()

    assert kwargs["user"] == "reader"
    assert kwargs["password"] == "hunter2"
    assert kwargs["host"] == "host.test"
    assert kwargs["port"] == 5432


@pytest.mark.parametrize(
    "scheme", ["postgresql", "postgres", "postgresql+psycopg", "POSTGRESQL"]
)
def test_every_postgres_scheme_is_accepted(scheme):
    """SQLAlchemy writes the driver into the scheme; libpq tools do not."""
    parsed = db_config.parse_url("{0}://u@h:5432/d".format(scheme))

    assert parsed["host"] == "h"


@pytest.mark.parametrize("url", ["mysql://u@h/d", "sqlite:///file.db", "http://h/d"])
def test_a_non_postgres_url_is_refused(url):
    """Saying so here beats relaying libpq's error message later."""
    with pytest.raises(ValueError, match="not a PostgreSQL URL"):
        db_config.parse_url(url)


def test_percent_encoded_credentials_are_decoded():
    """A password containing ``@`` or ``/`` survives the round trip."""
    parsed = db_config.parse_url("postgresql://u%40h:p%2Fw@host:5432/db")

    assert parsed["user"] == "u@h"
    assert parsed["password"] == "p/w"


def test_extra_url_parameters_pass_through():
    """``sslmode`` and friends are libpq keywords already."""
    parsed = db_config.parse_url("postgresql://u@h:5432/d?sslmode=require&connect_timeout=3")

    assert parsed["sslmode"] == "require"
    assert parsed["connect_timeout"] == "3"


def test_a_url_with_no_database_omits_the_name():
    """An omitted part is absent, not present-and-empty."""
    assert "dbname" not in db_config.parse_url("postgresql://u@h:5432/")


# ----------------------------------------------------------------------
# Rendering the connection back out
# ----------------------------------------------------------------------
def test_database_url_renders_a_usable_url(monkeypatch):
    """The URL form, for tools that want one."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host.test:5433/mydb")

    assert db_config.database_url() == "postgresql://u:p@host.test:5433/mydb"


def test_database_url_can_name_a_driver(monkeypatch):
    """SQLAlchemy needs the driver in the scheme to pick psycopg 3."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host.test:5433/mydb")

    assert db_config.database_url("psycopg").startswith("postgresql+psycopg://")


def test_database_url_escapes_awkward_credentials(monkeypatch):
    """A password written raw in PGPASSWORD is encoded on the way into a URL."""
    monkeypatch.setenv("PGUSER", "u@h")
    monkeypatch.setenv("PGPASSWORD", "p/w#x")

    url = db_config.database_url()

    assert "u%40h:p%2Fw%23x@" in url
    assert db_config.parse_url(url)["password"] == "p/w#x"


def test_database_url_omits_an_absent_password():
    """No password configured, no password in the URL."""
    assert db_config.database_url() == "postgresql://postgres@localhost:5432/gradcafe"


def test_describe_never_includes_the_password(monkeypatch):
    """This string goes on a webpage and into every error message."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:supersecret@host.test:5433/mydb")

    described = db_config.describe()

    assert described == "u@host.test:5433/mydb"
    assert "supersecret" not in described


# ----------------------------------------------------------------------
# The .env reader
# ----------------------------------------------------------------------
def test_dotenv_sets_variables_from_a_file(tmp_path):
    """The way a development machine is configured without committing a secret."""
    env = tmp_path / ".env"
    env.write_text('PGHOST=from-file\nPGUSER="quoted"\n', encoding="utf-8")

    _real_load_dotenv(env)

    assert os.environ["PGHOST"] == "from-file"
    assert os.environ["PGUSER"] == "quoted"


def test_dotenv_ignores_comments_and_junk(tmp_path):
    """Blank lines, comments and lines with no ``=`` are skipped."""
    env = tmp_path / ".env"
    env.write_text(
        "\n# a comment\nnot-an-assignment\n  \nPGDATABASE=from-file\n", encoding="utf-8"
    )

    _real_load_dotenv(env)

    assert os.environ["PGDATABASE"] == "from-file"


def test_dotenv_never_overrides_the_real_environment(tmp_path, monkeypatch):
    """A variable already set wins, which is what lets a test override it."""
    monkeypatch.setenv("PGHOST", "from-environment")
    env = tmp_path / ".env"
    env.write_text("PGHOST=from-file\n", encoding="utf-8")

    _real_load_dotenv(env)

    assert os.environ["PGHOST"] == "from-environment"


def test_dotenv_tolerates_a_missing_file(tmp_path):
    """No ``.env`` is the normal case in CI, and is not an error."""
    _real_load_dotenv(tmp_path / "nothing-here")  # must not raise
