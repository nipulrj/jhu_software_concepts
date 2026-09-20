"""SQLAlchemy 2.x mapping of the ``applicants`` table, plus the Engine and Session.

This is the same table ``load_data.py`` writes with ``psycopg`` and
``query_data.py`` reads with raw SQL -- there is exactly one copy of the data.
The model is declared against the existing schema rather than owning it: the
loader is what creates the table, and ``Base.metadata`` here simply describes
what is already there so the ORM can read and write it.

``orm_queries.py`` and the Flask app in ``app.py`` both import from this module.

    python models.py        # check the mapping against the live database
"""

from __future__ import annotations

import datetime as dt
import sys
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import Date, Float, Integer, Text, URL, create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

import db_config


def database_url() -> URL:
    """Build the SQLAlchemy URL from the same environment ``psycopg`` uses.

    ``URL.create`` rather than a formatted string, so a password containing
    ``@``, ``/`` or ``#`` is escaped correctly instead of silently splitting the
    URL.  The ``postgresql+psycopg`` driver name selects psycopg 3, matching the
    adapter the rest of the module uses.
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
    adding another.

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


# ``create_engine`` does not open a connection, so importing this module is safe
# even when PostgreSQL is not running; the first query is what connects.
# ``pool_pre_ping`` matters for the Flask app, which holds pooled connections
# open across requests and would otherwise hand out a stale one after a restart.
engine = create_engine(database_url(), pool_pre_ping=True, future=True)

# The 2.x convention: one configured factory, called per unit of work.
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield a Session that is committed on success and rolled back on error."""
    session = SessionLocal()
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


if __name__ == "__main__":
    raise SystemExit(main())
