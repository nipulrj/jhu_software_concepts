"""Turning raw scraped text into typed records.

Marked ``integration``: the cleaning stage of the ETL flow the pull runs.

Every function here is pure -- text in, a value out -- so the awkward cases are
cheap to test directly: a decision badge with no year on it, a leap day in a
non-leap year, a double-escaped HTML entity, a row with nothing identifiable in
it at all.  Those are the cases a real scrape produces and a fixture rarely does.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

import clean

pytestmark = pytest.mark.integration


# ----------------------------------------------------------------------
# Text
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Computer Science", "Computer Science"),
        ("  spaced   out  ", "spaced out"),
        ("<span>Accepted</span>", "Accepted"),
        ("Hawai&#39;i", "Hawai'i"),
        ("Wisconsin&amp;amp;Madison", "Wisconsin&Madison"),
        ("line\nbreak", "line break"),
        (None, ""),
        ("", ""),
    ],
)
def test_markup_is_stripped_and_entities_decoded(raw, expected):
    """Entities are unescaped twice: some pages double-escape them."""
    assert clean._strip_markup(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "<span> </span>"])
def test_text_that_is_nothing_becomes_none(raw):
    """"Nothing left after cleaning" is ``None``, never an empty string."""
    assert clean._clean_text(raw) is None


# ----------------------------------------------------------------------
# Dates
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "name, expected", [("Sep", 9), ("September", 9), ("  jan  ", 1), ("DEC", 12)]
)
def test_month_names_are_recognized(name, expected):
    assert clean._parse_month(name) == expected


@pytest.mark.parametrize("name", ["Smarch", "", "13"])
def test_an_unrecognized_month_is_none(name):
    assert clean._parse_month(name) is None


def test_the_added_on_column_is_parsed():
    assert clean._parse_date_added("Sep 11, 2026") == dt.date(2026, 9, 11)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "not a date",
        "Smarch 11, 2026",   # a month that does not exist
        "Feb 30, 2026",      # a day that does not exist
    ],
)
def test_an_unparseable_date_is_none(raw):
    """Every rejection path: no match, no month, no such day."""
    assert clean._parse_date_added(raw) is None


def test_a_decision_date_takes_its_year_from_the_entry():
    """The badge says "Accepted on Sep 09" and never says which year."""
    added = dt.date(2026, 9, 11)

    assert clean._infer_decision_date(9, 9, added) == dt.date(2026, 9, 9)


def test_a_decision_in_the_future_is_dated_to_the_previous_year():
    """An entry added in early January reporting a December decision."""
    added = dt.date(2026, 1, 5)

    assert clean._infer_decision_date(12, 20, added) == dt.date(2025, 12, 20)


def test_a_leap_day_skips_a_year_that_has_none():
    """2025 has no Feb 29, so the candidate year is skipped rather than fatal."""
    assert clean._infer_decision_date(2, 29, dt.date(2025, 3, 10)) == dt.date(
        2024, 2, 29
    )


def test_a_date_that_fits_no_candidate_year_is_none():
    """Neither 2026 nor 2025 has a Feb 29, so there is no date to give."""
    assert clean._infer_decision_date(2, 29, dt.date(2026, 3, 10)) is None


def test_a_decision_date_needs_an_entry_date():
    """With nothing to infer the year from, there is no date."""
    assert clean._infer_decision_date(9, 9, None) is None


# ----------------------------------------------------------------------
# Decision badges
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw, status",
    [
        ("Accepted", "Accepted"),
        ("accepted on Sep 09", "Accepted"),
        ("Rejected", "Rejected"),
        ("Wait listed on Mar 25", "Wait listed"),
        ("Waitlisted", "Wait listed"),
        ("Interview", "Interview"),
        ("Other", "Other"),
        ("Something Unusual", "Something Unusual"),
    ],
)
def test_decision_labels_are_canonicalized(raw, status):
    """The site spells "Wait listed" two ways; the database should not."""
    assert clean._parse_status(raw, dt.date(2026, 9, 11))["status"] == status


def test_an_empty_badge_yields_nothing():
    assert clean._parse_status("", None) == {"status": None, "decision_date": None}


def test_an_exact_date_from_the_page_is_authoritative():
    """Where the site's own data payload gave a full date, no inference runs."""
    parsed = clean._parse_status(
        "Accepted on Sep 09", dt.date(2026, 9, 11), "2026-09-09T12:00:00Z"
    )

    assert parsed["decision_date"] == "2026-09-09"


def test_an_unparseable_exact_date_falls_back_to_inference():
    """A malformed payload date is ignored rather than trusted."""
    parsed = clean._parse_status(
        "Accepted on Sep 09", dt.date(2026, 9, 11), "not-a-date"
    )

    assert parsed["decision_date"] == "2026-09-09"


def test_a_badge_with_an_unrecognized_month_has_no_decision_date():
    parsed = clean._parse_status("Accepted on Smarch 09", dt.date(2026, 9, 11))

    assert parsed["status"] == "Accepted"
    assert parsed["decision_date"] is None


def test_a_badge_with_no_date_has_no_decision_date():
    parsed = clean._parse_status("Accepted", dt.date(2026, 9, 11))

    assert parsed["decision_date"] is None


def test_a_badge_whose_date_cannot_be_placed_has_none():
    """No entry date, so the year cannot be inferred."""
    assert clean._parse_status("Accepted on Sep 09", None)["decision_date"] is None


# ----------------------------------------------------------------------
# Numbers
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected", [("3.57", 3.57), ("  4.0 ", 4.0), ("", None), (None, None), ("x", None)]
)
def test_badge_numbers_are_parsed(raw, expected):
    assert clean._parse_number(raw) == expected


@pytest.mark.parametrize("raw, expected", [("158", 158), ("158.0", 158), (None, None)])
def test_scaled_scores_are_whole_numbers(raw, expected):
    """GRE section scores are integers on a 130-170 scale."""
    assert clean._parse_score(raw) == expected


# ----------------------------------------------------------------------
# Terms
# ----------------------------------------------------------------------
def test_a_term_is_split_into_season_and_year():
    assert clean._parse_term("Fall 2026") == {
        "term": "Fall 2026",
        "start_season": "Fall",
        "start_year": 2026,
    }


def test_a_lowercase_term_is_capitalized():
    assert clean._parse_term("fall 2026")["start_season"] == "Fall"


def test_an_unrecognized_term_is_kept_verbatim():
    """Better an unparsed term than a discarded one."""
    assert clean._parse_term("Sometime soon") == {
        "term": "Sometime soon",
        "start_season": None,
        "start_year": None,
    }


def test_an_empty_term_is_none():
    assert clean._parse_term("")["term"] is None


# ----------------------------------------------------------------------
# The combined program field
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "program, university, expected",
    [
        ("Computer Science", "Johns Hopkins University",
         "Computer Science, Johns Hopkins University"),
        ("Computer Science", None, "Computer Science"),
        (None, "Johns Hopkins University", "Johns Hopkins University"),
        (None, None, ""),
    ],
)
def test_the_program_field_is_combined(program, university, expected):
    """The ``"<program>, <university>"`` string the LLM stage consumes."""
    assert clean._combine_program_field(program, university) == expected


# ----------------------------------------------------------------------
# Whole records
# ----------------------------------------------------------------------
def test_a_row_is_cleaned_into_the_output_schema(sample_raw_rows):
    entry = clean._clean_entry(sample_raw_rows[0])

    assert entry["program"] == (
        "Computer Science, Massachusetts Institute of Technology (MIT)"
    )
    assert entry["applicant_status"] == "Accepted"
    assert entry["applicant_type"] == "American"
    assert entry["term"] == "Fall 2026"
    assert entry["gpa"] == 3.90
    assert entry["gre_quant"] == 168
    assert entry["entry_id"] == 900001


def test_the_raw_text_is_kept_for_traceability(sample_raw_rows):
    """Every cleaned value can be traced back to what the page said."""
    entry = clean._clean_entry(sample_raw_rows[0])

    assert entry["raw"]["raw_gpa"] == "3.90"
    assert entry["raw"]["raw_status"] == "Accepted on Mar 03"


def test_per_outcome_dates_are_filed_by_status(sample_raw_rows):
    """Accepted fills ``acceptance_date`` and leaves the other three empty."""
    entry = clean._clean_entry(sample_raw_rows[0])

    assert entry["acceptance_date"] == entry["decision_date"]
    assert entry["rejection_date"] is None
    assert entry["waitlist_date"] is None
    assert entry["interview_date"] is None


@pytest.mark.parametrize(
    "status, field",
    [
        ("Rejected on Mar 15", "rejection_date"),
        ("Wait listed on Mar 22", "waitlist_date"),
        ("Interview on Feb 28", "interview_date"),
    ],
)
def test_each_outcome_gets_its_own_date_field(sample_raw_rows, status, field):
    row = dict(sample_raw_rows[0], raw_status=status)

    entry = clean._clean_entry(row)

    assert entry[field] is not None


def test_a_row_with_nothing_identifiable_is_dropped():
    """No program and no university is not a record, it is noise."""
    assert clean._clean_entry({"raw_program": "", "raw_university": None}) is None


def test_a_row_with_only_a_university_is_kept():
    """Half a name is still identifiable."""
    entry = clean._clean_entry({"raw_university": "Yale University"})

    assert entry["program"] == "Yale University"


def test_an_applicant_type_is_capitalized():
    entry = clean._clean_entry(
        {"raw_program": "CS", "raw_applicant_type": "international"}
    )

    assert entry["applicant_type"] == "International"


def test_clean_data_drops_repeated_entry_ids(sample_raw_rows):
    """One scrape can pass the same result twice when pages overlap."""
    cleaned = clean.clean_data(sample_raw_rows + sample_raw_rows[:3])

    assert len(cleaned) == len(sample_raw_rows)


def test_clean_data_keeps_records_with_no_id():
    """A record with no result id is still cleaned; it just cannot de-duplicate."""
    rows = [{"raw_program": "CS"}, {"raw_program": "Maths"}]

    assert len(clean.clean_data(rows)) == 2


def test_clean_data_skips_unusable_rows(sample_raw_rows):
    cleaned = clean.clean_data(sample_raw_rows + [{"raw_program": ""}])

    assert len(cleaned) == len(sample_raw_rows)


# ----------------------------------------------------------------------
# The coverage report
# ----------------------------------------------------------------------
def test_the_summary_reports_field_coverage(sample_records):
    report = clean.summarise(sample_records)

    assert "12 cleaned records" in report
    assert "gpa" in report
    assert "%" in report


def test_the_summary_of_nothing_says_so():
    assert clean.summarise([]) == "no records"


# ----------------------------------------------------------------------
# python clean.py
# ----------------------------------------------------------------------
def test_main_cleans_a_file(tmp_path, sample_raw_rows, capsys):
    """The command line: raw JSON in, cleaned JSON out."""
    raw = tmp_path / "raw.json"
    out = tmp_path / "cleaned.json"
    raw.write_text(json.dumps(sample_raw_rows), encoding="utf-8")

    assert clean.main(["--in", str(raw), "--out", str(out)]) == 0

    written = json.loads(out.read_text(encoding="utf-8"))
    assert len(written) == len(sample_raw_rows)
    assert "Read 12 raw rows" in capsys.readouterr().err


def test_main_reports_an_empty_input(tmp_path, capsys):
    """Nothing to clean is exit code 1 and advice, not a crash."""
    raw = tmp_path / "raw.json"
    raw.write_text("[]", encoding="utf-8")

    assert clean.main(["--in", str(raw), "--out", str(tmp_path / "out.json")]) == 1
    assert "Run scrape.py first" in capsys.readouterr().err
