"""The loader's own logic: reading files, shaping rows, and the command line.

Marked ``db``.  What a load *does to PostgreSQL* is covered by
:mod:`test_db_insert`; this file covers everything the loader decides before it
opens a connection -- how a missing field becomes ``None``, how a record with no
result id is dropped, what a malformed file produces -- plus the command-line
wrapper, which is how the full dataset is loaded by hand.
"""

from __future__ import annotations

import json

import psycopg
import pytest

import load_data

pytestmark = pytest.mark.db


# ----------------------------------------------------------------------
# Normalizing one field
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "value, expected",
    [
        ("Computer Science", "Computer Science"),
        ("  padded  ", "padded"),
        ("", None),
        ("   ", None),
        (None, None),
        (42, "42"),
    ],
)
def test_text_fields_are_normalized(value, expected):
    """Blank and whitespace-only strings fold to ``None``.

    Storing ``""`` would quietly break Question 2, whose denominator is
    "entries carrying a usable nationality value" -- an empty string is not one,
    but it is not NULL either.
    """
    assert load_data._text(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (3.9, 3.9),
        (168, 168.0),
        ("3.75", 3.75),
        ("  4.0  ", 4.0),
        ("", None),
        ("not a number", None),
        (None, None),
        (True, None),
        (False, None),
    ],
)
def test_numeric_fields_are_normalized(value, expected):
    """A bool is never a score, whatever Python thinks of ``isinstance(True, int)``."""
    assert load_data._number(value) == expected


def test_out_of_range_values_are_preserved():
    """Nothing is clamped or corrected here.

    A GRE AW of 99.99 and a GPA of 8.25 are stored exactly as reported, because
    Question 3 measures what they do to the averages rather than hiding them.
    """
    assert load_data._number("99.99") == 99.99
    assert load_data._number("8.25") == 8.25


# ----------------------------------------------------------------------
# Shaping a record into a row
# ----------------------------------------------------------------------
def test_a_record_maps_onto_the_column_order(sample_records):
    """The tuple the insert statement receives lines up with the schema."""
    row = load_data._row_from_record(sample_records[0])

    assert len(row) == len(load_data.COLUMNS)
    assert dict(zip(load_data.COLUMNS, row))["p_id"] == 900001
    assert dict(zip(load_data.COLUMNS, row))["status"] == "Accepted"


@pytest.mark.parametrize("entry_id", [None, "not-a-number", [1, 2]])
def test_a_record_without_a_usable_id_is_dropped(entry_id):
    """No result id means no way to de-duplicate it on a later run."""
    assert load_data._row_from_record({"entry_id": entry_id}) is None


def test_a_string_id_is_accepted():
    """The scraper stores the id as text on some pages; it is still an id."""
    row = load_data._row_from_record({"entry_id": "900001"})

    assert row[0] == 900001


def test_build_rows_counts_what_it_skipped(sample_records):
    """The summary distinguishes "unusable" from "already seen"."""
    rows, skipped = load_data.build_rows(sample_records + [{"no": "id"}])

    assert len(rows) == len(sample_records)
    assert skipped == 1


def test_build_rows_keeps_the_last_of_a_repeated_id():
    """A file stitched from two scrapes can hold the same entry twice."""
    records = [
        {"entry_id": 1, "applicant_status": "Rejected"},
        {"entry_id": 1, "applicant_status": "Accepted"},
    ]

    rows, _ = load_data.build_rows(records)

    assert len(rows) == 1
    assert dict(zip(load_data.COLUMNS, rows[0]))["status"] == "Accepted"


def test_build_rows_of_nothing_is_empty():
    assert load_data.build_rows([]) == ([], 0)


def test_rows_are_inserted_in_batches(monkeypatch):
    """Fifty thousand rows go over in batches rather than one statement."""
    batches = list(load_data._batched(list(range(2500)), 1000))

    assert [len(batch) for batch in batches] == [1000, 1000, 500]


# ----------------------------------------------------------------------
# Reading a file
# ----------------------------------------------------------------------
def test_read_records_reads_a_json_array(tmp_path, sample_records):
    """The ordinary case: the pipeline's own output."""
    path = tmp_path / "cleaned.json"
    path.write_text(json.dumps(sample_records), encoding="utf-8")

    assert len(load_data.read_records(path)) == len(sample_records)


def test_read_records_tolerates_a_rows_wrapper(tmp_path):
    """``{"rows": [...]}`` is accepted as well as a bare array."""
    path = tmp_path / "wrapped.json"
    path.write_text(json.dumps({"rows": [{"entry_id": 1}]}), encoding="utf-8")

    assert load_data.read_records(path) == [{"entry_id": 1}]


def test_read_records_drops_non_objects(tmp_path):
    """A stray string in the array is skipped, not fatal."""
    path = tmp_path / "mixed.json"
    path.write_text(json.dumps([{"entry_id": 1}, "junk", None]), encoding="utf-8")

    assert load_data.read_records(path) == [{"entry_id": 1}]


def test_read_records_explains_a_missing_file(tmp_path):
    """The error says what to run, rather than raising FileNotFoundError."""
    with pytest.raises(load_data.LoaderError, match="does not exist"):
        load_data.read_records(tmp_path / "absent.json")


def test_read_records_explains_invalid_json(tmp_path):
    """A truncated file names itself and the parse error."""
    path = tmp_path / "broken.json"
    path.write_text("[{", encoding="utf-8")

    with pytest.raises(load_data.LoaderError, match="is not valid JSON"):
        load_data.read_records(path)


def test_read_records_refuses_a_file_that_is_not_a_list(tmp_path):
    """A JSON scalar is not a data file, whatever else it might be."""
    path = tmp_path / "scalar.json"
    path.write_text(json.dumps("just a string"), encoding="utf-8")

    with pytest.raises(load_data.LoaderError, match="JSON array"):
        load_data.read_records(path)


def test_read_records_of_an_object_without_rows_is_empty(tmp_path):
    """An object with no ``rows`` key holds no records -- which is not an error."""
    path = tmp_path / "object.json"
    path.write_text(json.dumps({"unexpected": True}), encoding="utf-8")

    assert load_data.read_records(path) == []


# ----------------------------------------------------------------------
# Loading, verbosely
# ----------------------------------------------------------------------
def test_a_verbose_load_reports_what_it_did(
    empty_database, sample_records, capsys
):
    """The command line's running commentary, which the web app turns off."""
    load_data.load_into_database(records=sample_records, verbose=True)

    printed = capsys.readouterr().err
    assert "Read 12 records from memory" in printed
    assert "applicants now holds 12 rows" in printed


def test_a_verbose_load_reports_skipped_and_collapsed_records(
    empty_database, sample_records, capsys
):
    """Records dropped and records collapsed are counted separately."""
    records = sample_records + sample_records[:2] + [{"no": "id"}]

    load_data.load_into_database(records=records, verbose=True)

    printed = capsys.readouterr().err
    assert "skipped 1 without a usable entry id" in printed
    assert "collapsed 2 repeated entry ids" in printed


def test_loading_from_a_file_names_the_file(
    empty_database, tmp_path, sample_records, capsys
):
    """``path=`` is still the ordinary way to load the committed dataset."""
    path = tmp_path / "cleaned.json"
    path.write_text(json.dumps(sample_records), encoding="utf-8")

    load_data.load_into_database(path=path, verbose=True)

    assert "Read 12 records from cleaned.json" in capsys.readouterr().err


def test_recreate_drops_the_existing_table(
    seeded_database, sample_records
):
    """``--recreate`` starts from nothing, so every row counts as inserted."""
    summary = load_data.load_into_database(
        records=sample_records[:3], recreate=True, verbose=False
    )

    assert summary["inserted"] == 3
    assert load_data.row_count(seeded_database) == 3


def test_row_count_of_a_missing_table_is_an_error(db_connection):
    """Counting a table that is not there is a database error, not zero."""
    with db_connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS applicants")

    with pytest.raises(psycopg.errors.UndefinedTable):
        load_data.row_count(db_connection)


# ----------------------------------------------------------------------
# python load_data.py
# ----------------------------------------------------------------------
def test_main_loads_the_named_file(
    empty_database, database, tmp_path, sample_records
):
    """The command line, which is how the full dataset is loaded by hand."""
    path = tmp_path / "cleaned.json"
    path.write_text(json.dumps(sample_records), encoding="utf-8")

    assert load_data.main(["--file", str(path)]) == 0
    assert load_data.row_count(empty_database) == len(sample_records)


def test_main_can_recreate_the_table(
    seeded_database, database, tmp_path, sample_records
):
    """``--recreate`` is reachable from the command line."""
    path = tmp_path / "cleaned.json"
    path.write_text(json.dumps(sample_records[:2]), encoding="utf-8")

    assert load_data.main(["--file", str(path), "--recreate"]) == 0
    assert load_data.row_count(seeded_database) == 2


def test_main_reports_a_bad_file_and_exits_non_zero(tmp_path, capsys):
    """A problem the user can act on becomes a message and exit code 1."""
    assert load_data.main(["--file", str(tmp_path / "absent.json")]) == 1
    assert "does not exist" in capsys.readouterr().err
