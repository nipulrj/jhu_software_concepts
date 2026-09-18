"""Database connection settings, read from the environment.

No credential is ever written into this repository.  The connection is
described entirely by environment variables, which may optionally be supplied
by a local ``.env`` file that ``.gitignore`` keeps out of version control
(``.env.example`` shows the shape without the secret).

Two forms are understood, checked in this order:

1. ``DATABASE_URL`` -- one full libpq URL, e.g.
   ``postgresql://postgres:secret@localhost:5432/gradcafe``.
2. The standard libpq variables ``PGHOST``, ``PGPORT``, ``PGDATABASE``,
   ``PGUSER`` and ``PGPASSWORD``.

``psycopg`` (``load_data.py``, ``query_data.py``) and SQLAlchemy (``models.py``)
both build their connections from this one module, so the raw-SQL half and the
ORM half of the assignment are guaranteed to be talking to the same database.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATH = PROJECT_ROOT / ".env"

# Used only when the environment says nothing.  A default database *name* is
# harmless; a default password would not be, so there is none.
DEFAULTS = {
    "PGHOST": "localhost",
    "PGPORT": "5432",
    "PGDATABASE": "gradcafe",
    "PGUSER": "postgres",
}


def load_dotenv(path: Path = ENV_PATH) -> None:
    """Copy ``KEY=value`` lines from ``path`` into ``os.environ``.

    Hand-rolled rather than pulling in ``python-dotenv``: the format needed here
    is a dozen lines, and one fewer dependency is one fewer install step for
    anyone reproducing this.  Variables already set in the real environment win,
    so ``PGPASSWORD=... python load_data.py`` overrides the file.
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


def connect_kwargs() -> Dict[str, Any]:
    """Build the keyword arguments for ``psycopg.connect()``.

    Keyword arguments rather than a URL string, so a password containing ``@``,
    ``/`` or ``#`` needs no percent-encoding and cannot silently corrupt the
    connection string.
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

    return kwargs


def database_url() -> str:
    """Return the connection as a URL, for tools that want one."""
    load_dotenv()

    url = os.environ.get("DATABASE_URL")
    if url:
        return url

    from urllib.parse import quote

    kwargs = connect_kwargs()
    credentials = quote(str(kwargs["user"]), safe="")
    if kwargs.get("password"):
        credentials += ":" + quote(str(kwargs["password"]), safe="")

    return (
        f"postgresql://{credentials}@{kwargs['host']}:{kwargs['port']}/"
        f"{kwargs['dbname']}"
    )


def describe() -> str:
    """A one-line summary of where we are connecting, with no password in it."""
    kwargs = connect_kwargs()
    return (
        f"{kwargs['user']}@{kwargs['host']}:{kwargs['port']}/{kwargs['dbname']}"
    )
