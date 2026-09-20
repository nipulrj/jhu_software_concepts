"""The ORM mapping, the Engine cache, and the mapping's own check script.

Marked ``db``.  The mapping itself is exercised against a real database by
:mod:`test_db_insert`; what is left here is the machinery around it -- the
lazily built Engine that lets a fixture redirect the application, and the
``python models.py`` self-check.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

import models

pytestmark = pytest.mark.db


# ----------------------------------------------------------------------
# The Engine cache
# ----------------------------------------------------------------------
def test_the_engine_is_built_once_per_url(monkeypatch):
    """Two calls with the same configuration share one connection pool."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host.test:5432/one")
    models.reset_engine()

    assert models.get_engine() is models.get_engine()


def test_changing_the_url_gives_a_different_engine(monkeypatch):
    """The property the database fixtures rest on: redirect, and the ORM follows.

    Module 3 built the Engine at import time, so this was not possible -- the
    process was bound to whichever database was configured when the first
    import ran.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host.test:5432/one")
    models.reset_engine()
    first = models.get_engine()

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host.test:5432/two")
    second = models.get_engine()

    assert first is not second
    assert second.url.database == "two"


def test_reset_engine_disposes_and_forgets(monkeypatch):
    """Nothing keeps a pooled connection to the previous database alive."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host.test:5432/one")
    models.reset_engine()
    engine = models.get_engine()
    disposed = {"called": False}
    monkeypatch.setattr(engine, "dispose", lambda: disposed.update(called=True))

    models.reset_engine()

    assert disposed["called"] is True
    assert models.get_engine() is not engine


def test_reset_engine_on_an_empty_cache_is_harmless():
    """Calling it twice is not an error."""
    models.reset_engine()
    models.reset_engine()


def test_the_url_carries_the_psycopg_driver(monkeypatch):
    """``postgresql+psycopg`` selects psycopg 3, matching the rest of the app."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host.test:5432/one")

    assert models.database_url().drivername == "postgresql+psycopg"


def test_the_url_escapes_an_awkward_password(monkeypatch):
    """``URL.create`` rather than string formatting, so ``@`` cannot split it."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p%40w%2Fx@host.test:5432/one")

    assert models.database_url().password == "p@w/x"


# ----------------------------------------------------------------------
# Sessions
# ----------------------------------------------------------------------
def test_session_scope_commits_on_success(seeded_database, database):
    """The ordinary path: work, then commit."""
    with models.session_scope() as session:
        applicant = session.get(models.Applicant, 900001)
        applicant.comments = "edited inside the scope"

    with models.session_scope() as session:
        assert session.get(models.Applicant, 900001).comments == (
            "edited inside the scope"
        )


def test_session_scope_rolls_back_on_error(seeded_database, database):
    """A scope that raises leaves the database as it found it."""
    original = "Funded offer, very happy."

    with pytest.raises(RuntimeError, match="deliberate"):
        with models.session_scope() as session:
            session.get(models.Applicant, 900001).comments = "should not survive"
            raise RuntimeError("deliberate")

    with models.session_scope() as session:
        assert session.get(models.Applicant, 900001).comments == original


def test_get_session_returns_a_usable_session(seeded_database, database):
    """The plain factory, for a caller that closes the Session itself."""
    session = models.get_session()
    try:
        assert session.get(models.Applicant, 900004).degree == "Masters"
    finally:
        session.close()


# ----------------------------------------------------------------------
# python models.py
# ----------------------------------------------------------------------
def test_main_reports_the_mapping_against_a_live_database(
    seeded_database, database, capsys
):
    """The self-check prints the row count and the newest row."""
    assert models.main() == 0

    printed = capsys.readouterr().out
    assert "applicants rows: 12" in printed
    assert "newest row: <Applicant p_id=900012" in printed


def test_main_reports_an_empty_table(empty_database, database, capsys):
    """No rows is a valid answer, not a crash."""
    assert models.main() == 0
    assert "applicants rows: 0" in capsys.readouterr().out


def test_main_explains_a_database_it_cannot_reach(monkeypatch, capsys):
    """A server that is not there produces advice and a non-zero exit code."""

    def refuse():
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(models, "get_session", refuse)

    assert models.main() == 1
    assert "Could not query" in capsys.readouterr().err
