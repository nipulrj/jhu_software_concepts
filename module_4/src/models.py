"""SQLAlchemy 2.x mapping of the ``applicants`` table, plus the Engine and Session.

This is the same table :mod:`load_data` writes with ``psycopg`` and
:mod:`query_data` reads with raw SQL -- there is exactly one copy of the data.
The model is declared against the existing schema rather than owning it: the
loader is what creates the table, and ``Base.metadata`` here simply describes
what is already there so the ORM can read and write it.

:mod:`orm_queries` and :mod:`flask_app` both get their Sessions from here.

**The Engine is built on first use, not on import.**  Module 3 created it at
import time, which meant the process was bound to whatever ``DATABASE_URL``
said the moment the first module imported this one -- so a test could not point
the ORM at a scratch database without reloading the module.  :func:`get_engine`
instead caches one Engine per URL and :func:`reset_engine` throws the cache
away, which is all a fixture needs to redirect every query in the application.

    python models.py        # check the mapping against the live database
"""

from __future__ import annotations

import datetime as dt
import sys
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from sqlalchemy import URL, Date, Engine, Float, Integer, Text, create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

import db_config

#: The columns the assignment's schema requires, in order.  Everything that
#: reads or writes a whole applicant row -- the loader, the query helpers, the
#: schema tests -- names its fields from this one tuple, so a column added in
#: one place cannot go missing in another.
REQUIRED_FIELDS = (
    "p_id",
    "program",
    "comments",
    "date_added",
    "url",
    "status",
    "term",
    "us_or_international",
    "gpa",
    "gre",
    "gre_v",
    "gre_aw",
    "degree",
    "llm_generated_program",
    "llm_generated_university",
)


def database_url() -> URL:
    """Build the SQLAlchemy URL from the same environment ``psycopg`` uses.

    ``URL.create`` rather than a formatted string, so a password containing
    ``@``, ``/`` or ``#`` is escaped correctly instead of silently splitting the
    URL.  The ``postgresql+psycopg`` driver name selects psycopg 3, matching the
    adapter the rest of the application uses.
    """
    kwargs = db_config.connect_kwargs()
    return URL.create(
        drivername="postgresql+psycopg",
        username=kwargs.get("user"),
        password=kwargs.get("password"),
        host=kwargs.get("host"),
        port=kwargs.get("port"),
        database=kwargs.get("dbname"),
    )


class Base(DeclarativeBase):
    """Declarative base for every model in this module."""


class Applicant(Base):
    """One Grad Cafe admissions result.

    Column names and types mirror the assignment's schema exactly.  ``p_id`` is
    the Grad Cafe result id, which makes it a natural primary key: it is stable
    across scrapes, so re-loading the same entry updates its row rather than
    adding another.  That is the uniqueness policy the idempotency tests check.

    Everything except ``p_id`` is nullable, because Grad Cafe entries are
    self-submitted and most of them leave the optional fields blank -- roughly
    41% report a GPA and under 8% report any GRE score.
    """

    __tablename__ = "applicants"

    p_id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # University and department, as the site rendered it: "<program>, <university>".
    program: Mapped[Optional[str]] = mapped_column(Text)
    comments: Mapped[Optional[str]] = mapped_column(Text)
    date_added: Mapped[Optional[dt.date]] = mapped_column(Date)
    url: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[Optional[str]] = mapped_column(Text)
    term: Mapped[Optional[str]] = mapped_column(Text)
    us_or_international: Mapped[Optional[str]] = mapped_column(Text)

    # Self-reported metrics. Float, not Numeric: these are measurements rather
    # than money, and some are out of range for their own scale (see Question 11).
    gpa: Mapped[Optional[float]] = mapped_column(Float)
    gre: Mapped[Optional[float]] = mapped_column(Float)
    gre_v: Mapped[Optional[float]] = mapped_column(Float)
    gre_aw: Mapped[Optional[float]] = mapped_column(Float)

    degree: Mapped[Optional[str]] = mapped_column(Text)

    # Added by the Module 2 TinyLlama standardizer.
    llm_generated_program: Mapped[Optional[str]] = mapped_column(Text)
    llm_generated_university: Mapped[Optional[str]] = mapped_column(Text)

    def __repr__(self) -> str:
        return "<Applicant p_id={0} program={1!r} status={2!r}>".format(
            self.p_id, self.program, self.status
        )

    def as_dict(self) -> Dict[str, Any]:
        """This row as a plain dict keyed by :data:`REQUIRED_FIELDS`.

        What the Flask layer and the query helpers hand around, so nothing
        outside this module has to know it is holding a mapped instance whose
        Session may since have closed.
        """
        return {field: getattr(self, field) for field in REQUIRED_FIELDS}


# One Engine per distinct URL, built on demand.  ``create_engine`` does not open
# a connection, so the first *query* is what connects and importing this module
# stays safe with PostgreSQL stopped.  ``pool_pre_ping`` matters for the Flask
# app, which holds pooled connections open across requests and would otherwise
# hand out a stale one after a database restart.
_ENGINES: Dict[str, Engine] = {}


def get_engine() -> Engine:
    """The Engine for the current environment, created once per URL.

    Keyed on the rendered URL rather than on nothing at all, so a test that
    repoints ``DATABASE_URL`` gets its own Engine and connection pool instead of
    one still bound to the previous database.
    """
    url = database_url()
    key = url.render_as_string(hide_password=False)
    engine = _ENGINES.get(key)
    if engine is None:
        engine = create_engine(url, pool_pre_ping=True, future=True)
        _ENGINES[key] = engine
    return engine


def reset_engine() -> None:
    """Dispose of every cached Engine and forget it.

    Called by the test fixtures between databases, and worth having in
    production too: it is the only way to make the application let go of a
    connection pool without restarting the process.
    """
    while _ENGINES:
        _, engine = _ENGINES.popitem()
        engine.dispose()


def get_sessionmaker() -> sessionmaker:
    """A configured Session factory bound to the current Engine.

    ``expire_on_commit=False`` so a row read inside :func:`session_scope` is
    still readable after the commit that closed the Session -- the Flask layer
    reads answers out of one and renders them after it has gone.
    """
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Session:
    """One new Session, for a caller that will close it itself."""
    return get_sessionmaker()()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield a Session that is committed on success and rolled back on error."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> int:
    """Confirm the mapping lines up with the live table."""
    try:
        with session_scope() as session:
            total = session.scalar(select(func.count()).select_from(Applicant))
            newest = session.scalars(
                select(Applicant).order_by(Applicant.p_id.desc()).limit(1)
            ).first()
    except SQLAlchemyError as exc:
        print(
            "Could not query {where}:\n  {exc}".format(
                where=db_config.describe(), exc=exc
            ),
            file=sys.stderr,
        )
        return 1

    print("Connected to {where}".format(where=db_config.describe()))
    print("applicants rows: {0:,}".format(total or 0))
    if newest is not None:
        print("newest row: {0!r}".format(newest))
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    # Excluded from coverage rather than exercised: running this line means
    # re-executing the module under a second name, which would define a
    # second copy of everything in it. The main() it dispatches to is
    # called directly by the tests, which is where the behaviour lives.
    raise SystemExit(main())
