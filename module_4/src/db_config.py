"""Database connection settings, read from the environment.

No credential is ever written into this repository.  The connection is
described entirely by environment variables, which may optionally be supplied
by a local ``.env`` file that ``.gitignore`` keeps out of version control
(``.env.example`` shows the shape without the secret).

Two forms are understood, checked in this order:

1. ``DATABASE_URL`` -- one full libpq URL, e.g.
   ``postgresql://postgres:secret@localhost:5432/gradcafe``.  This is the form
   the test suite and the CI workflow set, and where it says something it wins.
2. The standard libpq variables ``PGHOST``, ``PGPORT``, ``PGDATABASE``,
   ``PGUSER`` and ``PGPASSWORD``.

``psycopg`` (:mod:`load_data`, :mod:`query_data`) and SQLAlchemy
(:mod:`models`) both build their connections from this one module, so the
raw-SQL half and the ORM half of the application are guaranteed to be talking
to the same database -- and pointing a test at a scratch database is a matter
of setting one variable rather than patching two adapters.

Nothing here is cached.  Every call re-reads the environment, which is what
lets a test set ``DATABASE_URL`` for the duration of one test and have the
whole application follow it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, quote, unquote, urlsplit

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
ENV_PATH = PROJECT_ROOT / ".env"

#: URL schemes understood as "a PostgreSQL connection".  SQLAlchemy writes the
#: driver into the scheme (``postgresql+psycopg``); libpq tools use the bare
#: two.  All three describe the same server, so all three are accepted.
URL_SCHEMES = ("postgresql", "postgres")

# Used only when the environment says nothing.  A default database *name* is
# harmless; a default password would not be, so there is none.
DEFAULTS = {
    "PGHOST": "localhost",
    "PGPORT": "5432",
    "PGDATABASE": "gradcafe",
    "PGUSER": "postgres",
}


def load_dotenv(path: Path = ENV_PATH) -> None:
    """Copy ``KEY=value`` lines from ``path`` into :data:`os.environ`.

    Hand-rolled rather than pulling in ``python-dotenv``: the format needed here
    is a dozen lines, and one fewer dependency is one fewer install step for
    anyone reproducing this.  Variables already set in the real environment win,
    so ``DATABASE_URL=... python -m pytest`` overrides the file -- and a test
    that sets the variable is never quietly overruled by a developer's ``.env``.

    :param path: the file to read.  A missing file is not an error, so the
        application runs unchanged on a machine that configures the environment
        some other way.  CI does exactly that.
    """
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def parse_url(url: str) -> Dict[str, Any]:
    """Split a libpq URL into keyword arguments for ``psycopg.connect()``.

    :param url: a URL such as
        ``postgresql://user:pass@host:5432/dbname?sslmode=require``.  A
        ``+driver`` suffix on the scheme (``postgresql+psycopg``) is accepted
        and ignored, so one string can serve psycopg and SQLAlchemy alike.
    :returns: the connection as keyword arguments.  A part the URL omits is
        absent from the result rather than present-and-empty, which is what
        lets :func:`connect_kwargs` fill it in from the ``PG*`` variables.
    :raises ValueError: if the scheme is not a PostgreSQL one.  Saying so here
        beats handing a MySQL URL to libpq and relaying its error message.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.partition("+")[0].lower()
    if scheme not in URL_SCHEMES:
        raise ValueError(
            "{url!r} is not a PostgreSQL URL: expected a scheme of {schemes}, "
            "got {scheme!r}.".format(
                url=url, schemes=" or ".join(URL_SCHEMES), scheme=parts.scheme
            )
        )

    kwargs: Dict[str, Any] = {}
    if parts.hostname:
        kwargs["host"] = parts.hostname
    if parts.port:
        kwargs["port"] = int(parts.port)

    # urlsplit leaves credentials percent-encoded; libpq wants them raw, and a
    # password containing "@" or "/" has to be encoded in the URL to survive it.
    if parts.username:
        kwargs["user"] = unquote(parts.username)
    if parts.password:
        kwargs["password"] = unquote(parts.password)

    dbname = parts.path.lstrip("/")
    if dbname:
        kwargs["dbname"] = unquote(dbname)

    # Anything else the URL carries -- sslmode, connect_timeout, application_name
    # -- is already a libpq keyword, so it passes straight through.
    kwargs.update(parse_qsl(parts.query))

    return kwargs


def connect_kwargs() -> Dict[str, Any]:
    """Build the keyword arguments for ``psycopg.connect()``.

    Keyword arguments rather than a URL string, so a password containing ``@``,
    ``/`` or ``#`` needs no percent-encoding and cannot silently corrupt the
    connection string.

    ``DATABASE_URL`` is applied last and therefore wins wherever it says
    something; the ``PG*`` variables fill in whatever it left out.  That
    ordering is what "tests may override configuration" needs: setting one
    variable redirects every connection the application makes.
    """
    load_dotenv()

    kwargs: Dict[str, Any] = {
        "host": os.environ.get("PGHOST", DEFAULTS["PGHOST"]),
        "port": int(os.environ.get("PGPORT", DEFAULTS["PGPORT"])),
        "dbname": os.environ.get("PGDATABASE", DEFAULTS["PGDATABASE"]),
        "user": os.environ.get("PGUSER", DEFAULTS["PGUSER"]),
    }

    # Left out entirely when unset, so libpq can still fall back to ~/.pgpass
    # or a trust/peer authentication rule rather than sending an empty string.
    password = os.environ.get("PGPASSWORD")
    if password:
        kwargs["password"] = password

    url = os.environ.get("DATABASE_URL")
    if url:
        kwargs.update(parse_url(url))

    return kwargs


def database_url(driver: Optional[str] = None) -> str:
    """Return the connection as a URL, for tools that want one.

    :param driver: a SQLAlchemy driver name to write into the scheme, e.g.
        ``"psycopg"`` for ``postgresql+psycopg://``.  ``None`` gives the plain
        ``postgresql://`` form that libpq and ``psql`` accept.

    The URL is rebuilt from :func:`connect_kwargs` rather than echoing
    ``DATABASE_URL`` back verbatim, so the two forms cannot disagree: whatever
    the ``PG*`` variables contributed is in here too, and the percent-encoding
    is correct even for a password that was written raw.
    """
    kwargs = connect_kwargs()
    scheme = "postgresql" if driver is None else "postgresql+" + driver

    credentials = quote(str(kwargs["user"]), safe="")
    if kwargs.get("password"):
        credentials += ":" + quote(str(kwargs["password"]), safe="")

    return "{scheme}://{credentials}@{host}:{port}/{dbname}".format(
        scheme=scheme,
        credentials=credentials,
        host=kwargs["host"],
        port=kwargs["port"],
        dbname=kwargs["dbname"],
    )


def describe() -> str:
    """A one-line summary of where we are connecting, with no password in it.

    Shown in the page masthead and in every connection error, so that "could
    not connect" says *which* server it could not reach -- without putting a
    credential on a webpage.
    """
    kwargs = connect_kwargs()
    return "{user}@{host}:{port}/{dbname}".format(**kwargs)
