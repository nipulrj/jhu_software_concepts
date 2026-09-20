"""The raw-SQL analyses: selecting questions, rendering them, running them.

Marked ``analysis`` and ``db``.  The eleven statements are exercised against
real rows -- an empty table as well as a seeded one, because "nothing yet" is a
case every percentage has to survive without dividing by zero -- and the console
side of the module is covered here too, since that is how the answers are read
outside the browser.
"""

from __future__ import annotations

import sys

import psycopg
import pytest

import conftest
import query_data
from doubles import PERCENT_PATTERN, TWO_DECIMAL_PERCENT

pytestmark = [pytest.mark.analysis, pytest.mark.db]


# ----------------------------------------------------------------------
# Choosing which questions to answer
# ----------------------------------------------------------------------
def test_no_selection_means_every_question():
    assert query_data.parse_selection(None) == list(range(1, 12))
    assert query_data.parse_selection("") == list(range(1, 12))


@pytest.mark.parametrize(
    "spec, expected",
    [
        ("1", [1]),
        ("1,4,5", [1, 4, 5]),
        ("1-6", [1, 2, 3, 4, 5, 6]),
        ("7-11", [7, 8, 9, 10, 11]),
        ("5,1,5", [1, 5]),
        (" 1 , 3 ", [1, 3]),
        ("1-3,9", [1, 2, 3, 9]),
        ("1,,3", [1, 3]),
    ],
)
def test_a_selection_is_parsed_and_sorted(spec, expected):
    """Ranges, lists and both together; repeats collapse and order is restored."""
    assert query_data.parse_selection(spec) == expected


def test_numbers_outside_the_range_are_dropped():
    """Asking for question 99 gets the ones that exist, not an error."""
    assert query_data.parse_selection("1,99") == [1]


def test_a_selection_matching_nothing_is_an_error():
    """Silence would be worse: the caller asked for something specific."""
    with pytest.raises(ValueError, match="No questions matched"):
        query_data.parse_selection("99")


# ----------------------------------------------------------------------
# The questions themselves
# ----------------------------------------------------------------------
def test_every_question_answers_against_seeded_rows(seeded_database):
    """All eleven run, and each produces at least one answer line."""
    results = query_data.run_all(seeded_database)

    assert [result.number for result in results] == list(range(1, 12))
    for result in results:
        assert result.answer_lines, "question {0} answered nothing".format(result.number)
        assert result.sql.strip()
        assert result.explanation.strip()


def test_every_question_answers_against_an_empty_table(empty_database):
    """Nothing yet is an answer, not a division by zero."""
    results = query_data.run_all(empty_database)

    assert len(results) == 11
    assert "Fall 2026 applicant count: 0" in results[0].answer


def test_question_one_counts_fall_2026(seeded_database):
    """Spot-checked against the fixture rather than a remembered number."""
    from doubles import SAMPLE_FALL_2026

    answer = query_data.question_1(seeded_database).answer

    assert "Fall 2026 applicant count: {0}".format(SAMPLE_FALL_2026) in answer


def test_question_two_excludes_entries_with_no_nationality(seeded_database):
    """The denominator is entries carrying a usable classification."""
    rows = query_data.fetch_applicants(seeded_database)
    classified = [row for row in rows if row["us_or_international"]]
    international = [
        row for row in classified if row["us_or_international"] == "International"
    ]

    answer = query_data.question_2(seeded_database).answer

    assert query_data.fmt_pct(100.0 * len(international) / len(classified)) in answer
    assert len(classified) < len(rows), "the fixture should include an unclassified row"


def test_question_three_diagnoses_impossible_scores(seeded_database):
    """The supporting findings fire when there are out-of-range values to explain."""
    result = query_data.question_3(seeded_database)

    assert result.table is not None
    assert result.caveat is not None
    supporting = " ".join(result.supporting)
    assert "260-340" in supporting
    assert "back inside the valid 130-170 band" in supporting


def test_question_three_says_nothing_extra_when_there_is_nothing_to_explain(
    empty_database,
):
    """With no impossible values, the supporting findings are simply absent."""
    result = query_data.question_3(empty_database)

    assert result.supporting == [
        "How much of each average is an artifact of impossible values:"
    ]


def test_question_nine_compares_the_two_field_sets(seeded_database):
    """The original-field and LLM-field counts, and the signed difference."""
    answer = query_data.question_9(seeded_database).answer

    assert "Original-field count:" in answer
    assert "LLM-field count:" in answer
    assert "Difference: +" in answer


def test_question_nine_reports_no_difference_as_a_signed_zero(seeded_database):
    """``+0`` reads as "measured and equal", where ``0`` could mean "not run"."""
    assert "Difference: +0" in query_data.question_9(seeded_database).answer


def test_question_ten_ranks_the_universities(seeded_database):
    """Hardest and easiest, with a table behind them."""
    result = query_data.question_10(seeded_database)

    assert result.original is True
    assert "Hardest of the twenty most-applied-to universities" in result.answer
    assert result.table["rows"]


def test_question_ten_has_nothing_to_rank_on_an_empty_table(empty_database):
    """No standardized universities, so it says so rather than inventing a rank."""
    result = query_data.question_10(empty_database)

    assert result.answer_lines == ["No entries with a standardized university."]


def test_question_eleven_compares_the_two_cohorts(seeded_database):
    """Acceptance, disclosure and the GPAs actually reported."""
    result = query_data.question_11(seeded_database)

    assert result.original is True
    assert "Acceptance rate:" in result.answer
    assert "report a GPA against" in result.answer


def test_question_eleven_needs_both_cohorts(empty_database):
    """One cohort, or none, is not a comparison."""
    result = query_data.question_11(empty_database)

    assert result.answer_lines == ["Not enough nationality data to compare cohorts."]


def test_a_subset_of_questions_can_be_answered(seeded_database):
    """``--questions 1,4,5`` runs three statements, not eleven."""
    results = query_data.run_all(seeded_database, [1, 4, 5])

    assert [result.number for result in results] == [1, 4, 5]


def test_answer_all_opens_its_own_connection(seeded_database, database):
    """The entry point the console and the comparison use."""
    results = query_data.answer_all([1])

    assert results[0].number == 1


def test_every_rendered_percentage_has_two_decimals(seeded_database):
    """The rule again, this time over the console text of every answer."""
    text = "\n".join(
        result.answer for result in query_data.run_all(seeded_database)
    )
    percentages = PERCENT_PATTERN.findall(text)

    assert percentages
    assert all(TWO_DECIMAL_PERCENT.fullmatch(p) for p in percentages)


# ----------------------------------------------------------------------
# Console output
# ----------------------------------------------------------------------
def test_a_table_is_rendered_as_fixed_width_text():
    """Names left-aligned, numbers right-aligned, under a rule."""
    table = {
        "columns": ["Metric", "Reported"],
        "rows": [["GPA (0-4.0)", "20,527"], ["GRE", "3,915"]],
    }

    lines = query_data._render_table(table)

    assert lines[0].strip().startswith("Metric")
    assert set(lines[1].strip()) == {"-", " "}
    assert lines[2].strip().startswith("GPA (0-4.0)")
    assert len(set(len(line) for line in lines)) == 1, "columns must line up"


def test_printing_results_shows_every_part(capsys):
    """Answer, table, supporting findings and caveat all reach the console."""
    query_data.print_results(conftest.CANNED_RESULTS)

    printed = capsys.readouterr().out
    assert "Question 1: How many entries are from Fall 2026 applicants?" in printed
    assert "Fall 2026 applicant count: 1,234" in printed
    assert "GPA (0-4.0)" in printed
    assert "+ Reported by 41.07% of entries." in printed
    assert "! Two of these are not usable as test scores." in printed


def test_printing_results_labels_my_own_questions(capsys):
    """The two original questions are marked as such."""
    query_data.print_results(conftest.CANNED_RESULTS)

    assert "Question 10 (my own):" in capsys.readouterr().out


def test_printing_results_can_include_the_sql(capsys):
    """``--sql`` puts the statement that produced each answer beneath it."""
    query_data.print_results(conftest.CANNED_RESULTS[:1], show_sql=True)

    assert "| SELECT 1" in capsys.readouterr().out


def test_the_console_is_switched_to_utf8(monkeypatch):
    """University names carry en-dashes; a cp1252 console mangles them."""
    reconfigured = []

    class Stream:
        def reconfigure(self, encoding):
            reconfigured.append(encoding)

    monkeypatch.setattr(sys, "stdout", Stream())
    monkeypatch.setattr(sys, "stderr", Stream())

    query_data.use_utf8_console()

    assert reconfigured == ["utf-8", "utf-8"]


def test_a_stream_that_cannot_be_reconfigured_is_left_alone(monkeypatch):
    """A redirected or closed stream is skipped rather than fatal."""

    class Redirected:
        def reconfigure(self, encoding):
            raise ValueError("I/O operation on closed file")

    class Plain:
        pass

    monkeypatch.setattr(sys, "stdout", Redirected())
    monkeypatch.setattr(sys, "stderr", Plain())

    query_data.use_utf8_console()  # must not raise


# ----------------------------------------------------------------------
# python query_data.py
# ----------------------------------------------------------------------
def test_main_prints_every_answer(seeded_database, database, capsys):
    assert query_data.main([]) == 0

    printed = capsys.readouterr().out
    assert "Grad Cafe analysis -- raw SQL via psycopg" in printed
    assert "Question 11" in printed


def test_main_can_answer_a_subset_with_sql(seeded_database, database, capsys):
    assert query_data.main(["--questions", "1", "--sql"]) == 0

    printed = capsys.readouterr().out
    assert "Question 1:" in printed
    assert "Question 2:" not in printed
    assert "| SELECT COUNT(*)" in printed


def test_main_rejects_a_selection_matching_nothing(capsys):
    assert query_data.main(["--questions", "99"]) == 1
    assert "No questions matched" in capsys.readouterr().err


def test_main_explains_a_database_it_cannot_reach(monkeypatch, capsys):
    """Advice rather than a psycopg traceback, and exit code 1."""

    def refuse(_numbers=None):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(query_data, "answer_all", refuse)

    assert query_data.main([]) == 1
    assert "Could not connect to PostgreSQL" in capsys.readouterr().err


def test_main_explains_a_missing_table(monkeypatch, capsys):
    """The commonest first-run mistake gets the command that fixes it."""

    def missing(_numbers=None):
        raise psycopg.errors.UndefinedTable("relation applicants does not exist")

    monkeypatch.setattr(query_data, "answer_all", missing)

    assert query_data.main([]) == 1
    assert "python load_data.py" in capsys.readouterr().err
