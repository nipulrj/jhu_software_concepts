"""Load cleaned Grad Cafe records into PostgreSQL.

Writes the output of the ETL pipeline -- either a JSON file on disk or a list of
records already in memory -- into the single ``applicants`` table this
application specifies.

Run it as often as you like.  Grad Cafe gives every result a permanent numeric
id, which the scraper keeps as ``entry_id`` and this loader stores as the primary
key ``p_id``.  Inserts therefore carry ``ON CONFLICT (p_id)``, so a second run
refreshes the rows it already has instead of duplicating them.  That is the
uniqueness policy the whole system rests on: it is what makes the "Pull Data"
button safe to press twice, and it is what ``tests/test_db_insert.py`` checks.

:func:`load_into_database` takes ``records=`` as well as ``path=``, so a caller
holding rows in memory -- the pull pipeline, or a test with a fake scraper --
never has to write a temporary file just to load them.

    python load_data.py --file cleaned.json   # load a cleaned file
    python load_data.py --recreate            # drop and rebuild the table first
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import psycopg

import db_config

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent

# Where a "Pull Data" run leaves its output. Kept outside src/ -- it is generated
# data, not source -- and out of version control; see .gitignore.
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DATA_PATH = DATA_DIR / "pull" / "llm_extend_applicant_data.json"

TABLE_NAME = "applicants"

# The schema the assignment specifies, in order.  ``p_id`` is the Grad Cafe
# result id rather than a generated sequence, which is what lets a re-run
# recognize a record it has already stored.
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS applicants (
    p_id                     integer PRIMARY KEY,
    program                  text,
    comments                 text,
    date_added               date,
    url                      text,
    status                   text,
    term                     text,
    us_or_international      text,
    gpa                      double precision,
    gre                      double precision,
    gre_v                    double precision,
    gre_aw                   double precision,
    degree                   text,
    llm_generated_program    text,
    llm_generated_university text
)
"""

# Every query in query_data.py and orm_queries.py filters on some combination of
# term, status and degree, over tens of thousands of rows.  These keep the
# webpage responsive without changing any answer.
CREATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS applicants_term_idx ON applicants (lower(term))",
    "CREATE INDEX IF NOT EXISTS applicants_status_idx ON applicants (lower(status))",
    "CREATE INDEX IF NOT EXISTS applicants_degree_idx ON applicants (lower(degree))",
)

COLUMNS: Tuple[str, ...] = (
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

# A re-run must never replace a value we already have with a NULL: a later scrape
# of the same entry can be missing a field the first one captured (the LLM
# columns especially, if the standardizer was skipped).  COALESCE keeps the known
# value in that case, while still accepting a genuine change -- an applicant who
# edits "Rejected" to "Wait listed" does update the row.
_UPDATE_ASSIGNMENTS = ", ".join(
    "{column} = COALESCE(EXCLUDED.{column}, {table}.{column})".format(
        column=column, table=TABLE_NAME
    )
    for column in COLUMNS
    if column != "p_id"
)

INSERT_SQL = (
    "INSERT INTO {table} ({columns}) VALUES ({placeholders}) "
    "ON CONFLICT (p_id) DO UPDATE SET {assignments}"
).format(
    table=TABLE_NAME,
    columns=", ".join(COLUMNS),
    placeholders=", ".join(["%s"] * len(COLUMNS)),
    assignments=_UPDATE_ASSIGNMENTS,
)

BATCH_SIZE = 1_000


class LoaderError(RuntimeError):
    """Raised for problems the user can act on, rather than a stack trace."""


# ----------------------------------------------------------------------
# Reading and shaping the JSON
# ----------------------------------------------------------------------
def _text(value: Any) -> Optional[str]:
    """Normalize a text field to a non-empty string or ``None``.

    The Module 2 cleaner already writes ``null`` rather than an empty string, but
    this loader also has to survive a hand-edited file, so blank and
    whitespace-only strings fold to ``None`` too.  Storing an empty string would
    quietly break Question 2, whose denominator is "entries with a usable
    nationality value".
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> Optional[float]:
    """Normalize a numeric field to a float or ``None``.

    Values the applicant typed are kept exactly as reported -- nothing is clamped
    or corrected here.  Some of them are out of range for their scale (a GRE AW
    of 99.99, a GPA on a 10-point scale); preserving them was the Module 2
    decision, and Question 11 measures what they do to the averages rather than
    hiding them.
    """
    if value is None:
        return None
    if isinstance(value, bool):  # bool subclasses int; never a real score
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _row_from_record(record: Dict[str, Any]) -> Optional[Tuple[Any, ...]]:
    """Map one cleaned JSON record onto the ``applicants`` column order.

    Returns ``None`` for a record with no usable primary key, since a Grad Cafe
    entry without its result id cannot be de-duplicated on a later run.
    """
    p_id = record.get("entry_id")
    if p_id is None:
        return None
    try:
        p_id = int(p_id)
    except (TypeError, ValueError):
        return None

    return (
        p_id,
        # "<program>, <university>" -- the combined field, as the schema asks.
        _text(record.get("program")),
        _text(record.get("comments")),
        _text(record.get("date_added")),  # ISO string; PostgreSQL casts to date
        _text(record.get("url")),
        _text(record.get("applicant_status")),
        _text(record.get("term")),
        _text(record.get("applicant_type")),
        _number(record.get("gpa")),
        _number(record.get("gre_quant")),
        _number(record.get("gre_verbal")),
        _number(record.get("gre_aw")),
        _text(record.get("degree")),
        _text(record.get("llm-generated-program")),
        _text(record.get("llm-generated-university")),
    )


def read_records(path: Path = DEFAULT_DATA_PATH) -> List[Dict[str, Any]]:
    """Read a cleaned JSON file produced by the ETL pipeline.

    :param path: the file to read. It may hold a bare JSON array or an object
        with a ``"rows"`` key; anything in the array that is not an object is
        dropped rather than crashing the load.
    :raises LoaderError: if the file is missing or is not valid JSON, with a
        message saying what to run instead of a traceback.
    """
    path = Path(path)
    if not path.exists():
        raise LoaderError(
            "{path} does not exist. Run the pipeline first, from src/:\n"
            "    python scrape.py --target 50000\n"
            "    python clean.py\n"
            "    python ../llm_hosting/app.py --file applicant_data.json "
            "--out llm_extend_applicant_data.json --json-array".format(path=path)
        )

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise LoaderError("{path} is not valid JSON: {exc}".format(path=path, exc=exc)) from exc

    if isinstance(data, dict):  # tolerate {"rows": [...]}
        data = data.get("rows", [])
    if not isinstance(data, list):
        raise LoaderError("{path} should hold a JSON array of records.".format(path=path))

    return [record for record in data if isinstance(record, dict)]


def build_rows(records: Iterable[Dict[str, Any]]) -> Tuple[List[Tuple[Any, ...]], int]:
    """Convert records to insertable rows, de-duplicating on ``p_id``.

    One file can legitimately contain the same entry twice if it was stitched
    together from two scrapes.  ``ON CONFLICT`` cannot help there -- PostgreSQL
    refuses a statement that touches the same row twice -- so duplicates are
    collapsed here, keeping the last occurrence.
    """
    by_id: Dict[int, Tuple[Any, ...]] = {}
    skipped = 0

    for record in records:
        row = _row_from_record(record)
        if row is None:
            skipped += 1
            continue
        by_id[row[0]] = row

    return list(by_id.values()), skipped


# ----------------------------------------------------------------------
# Writing to PostgreSQL
# ----------------------------------------------------------------------
def _batched(
    rows: Sequence[Tuple[Any, ...]], size: int
) -> Iterator[Sequence[Tuple[Any, ...]]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def create_table(connection: psycopg.Connection, recreate: bool = False) -> None:
    """Create ``applicants`` and its indexes if they are not already there."""
    with connection.cursor() as cursor:
        if recreate:
            cursor.execute("DROP TABLE IF EXISTS {table}".format(table=TABLE_NAME))
        cursor.execute(CREATE_TABLE_SQL)
        for statement in CREATE_INDEX_SQL:
            cursor.execute(statement)


def insert_rows(connection: psycopg.Connection, rows: Sequence[Tuple[Any, ...]]) -> None:
    """Insert or refresh every row, in batches, inside one transaction."""
    with connection.cursor() as cursor:
        for batch in _batched(rows, BATCH_SIZE):
            cursor.executemany(INSERT_SQL, batch)


def row_count(connection: psycopg.Connection) -> int:
    """How many rows the table currently holds."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM {table}".format(table=TABLE_NAME))
        result = cursor.fetchone()
    return int(result[0]) if result else 0


def load_into_database(
    path: Path = DEFAULT_DATA_PATH,
    recreate: bool = False,
    verbose: bool = True,
    records: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, int]:
    """Load records into PostgreSQL and report what changed.

    :param path: a cleaned JSON file to read. Ignored when ``records`` is given.
    :param recreate: drop and rebuild the table before loading.
    :param verbose: print a running commentary to stderr. The web app and the
        tests both pass ``False``; the command line leaves it on.
    :param records: records already in memory, loaded instead of reading
        ``path``. This is the injection point the pull pipeline uses, and the
        reason a test with a fake scraper needs no temporary file.
    :returns: a summary -- ``read``, ``skipped``, ``written``, ``inserted``,
        ``updated``, ``total`` -- so the "Pull Data" route can say how many
        records were actually added rather than just "done".
    :raises LoaderError: if the file is unusable, or the server unreachable.
    """
    if records is None:
        source = Path(path).name
        records = read_records(path)
    else:
        source = "memory"
        records = list(records)
    rows, skipped = build_rows(records)

    if verbose:
        print("Read {0:,} records from {1}".format(len(records), source), file=sys.stderr)
        if skipped:
            print("  skipped {0:,} without a usable entry id".format(skipped), file=sys.stderr)
        duplicates = len(records) - skipped - len(rows)
        if duplicates:
            print("  collapsed {0:,} repeated entry ids".format(duplicates), file=sys.stderr)

    try:
        with psycopg.connect(**db_config.connect_kwargs()) as connection:
            create_table(connection, recreate=recreate)
            before = 0 if recreate else row_count(connection)
            insert_rows(connection, rows)
            after = row_count(connection)
            connection.commit()
    except psycopg.OperationalError as exc:
        raise LoaderError(
            "Could not connect to PostgreSQL at {where}.\n"
            "  {exc}\n"
            "Check that the server is running and that module_4/.env holds the "
            "right credentials (copy .env.example to start).".format(
                where=db_config.describe(), exc=exc
            )
        ) from exc

    summary = {
        "read": len(records),
        "skipped": skipped,
        "written": len(rows),
        "inserted": after - before,
        "updated": len(rows) - (after - before),
        "total": after,
    }

    if verbose:
        print(
            "Wrote {written:,} rows -> {where} "
            "({inserted:,} new, {updated:,} already present)".format(
                written=summary["written"],
                where=db_config.describe(),
                inserted=summary["inserted"],
                updated=summary["updated"],
            ),
            file=sys.stderr,
        )
        print(
            "{table} now holds {total:,} rows".format(
                table=TABLE_NAME, total=summary["total"]
            ),
            file=sys.stderr,
        )

    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Load cleaned Grad Cafe data into PostgreSQL."
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_DATA_PATH,
        help="cleaned JSON to load (default: %(default)s)",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="drop the applicants table and rebuild it before loading",
    )
    args = parser.parse_args(argv)

    try:
        load_into_database(path=args.file, recreate=args.recreate)
    except LoaderError as exc:
        print("\n{exc}".format(exc=exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    # Excluded from coverage rather than exercised: running this line means
    # re-executing the module under a second name, which would define a
    # second copy of everything in it. The main() it dispatches to is
    # called directly by the tests, which is where the behaviour lives.
    raise SystemExit(main())
