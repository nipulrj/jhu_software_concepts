"""The same analyses through SQLAlchemy, and the script that compares the two.

Marked ``analysis`` and ``db``.  :mod:`test_integration_end_to_end` already
requires the ORM and the hand-written SQL to agree question by question; what is
left here is the ORM module's own command line -- including ``--compare``, which
is the tool that proves the agreement outside the test suite.
"""

from __future__ import annotations

import tokenize

import pytest
from sqlalchemy.exc import OperationalError

import orm_queries
import query_data
from doubles import PERCENT_PATTERN, TWO_DECIMAL_PERCENT

pytestmark = [pytest.mark.analysis, pytest.mark.db]


# ----------------------------------------------------------------------
# The questions
# ----------------------------------------------------------------------
def test_every_question_answers_against_seeded_rows(seeded_database, database):
    """All eleven run through the ORM, and each says something."""
    results = orm_queries.answer_all()

    assert [result.number for result in results] == list(range(1, 12))
    for result in results:
        assert result.answer_lines
        assert result.sql.strip(), "the generated statement should be shown"


def test_every_question_answers_against_an_empty_table(empty_database, database):
    """The ORM survives an empty table as the raw SQL does."""
    results = orm_queries.answer_all()

    assert "Fall 2026 applicant count: 0" in results[0].answer


def test_the_six_the_assignment_requires_are_marked(seeded_database, database):
    """Questions 1, 4, 5, 8, 9 and one of my own."""
    assert orm_queries.REQUIRED_BY_PART_6 == (1, 4, 5, 8, 9, 10)

    results = orm_queries.answer_all(list(orm_queries.REQUIRED_BY_PART_6))
    assert [result.number for result in results] == [1, 4, 5, 8, 9, 10]


def test_no_raw_sql_is_written_in_this_module():
    """The rule the module is built on, asserted rather than trusted.

    Every answer is composed from ``select()`` and the mapped columns; a
    ``text()`` or a psycopg cursor creeping in would defeat the point of having
    two independent implementations to compare.  Docstrings and comments are
    tokenized away first, so the module is free to *talk* about raw SQL.
    """
    with open(orm_queries.__file__, "r", encoding="utf-8") as handle:
        names = {
            token.string
            for token in tokenize.generate_tokens(handle.readline)
            if token.type == tokenize.NAME
        }

    assert "text" not in names
    assert "cursor" not in names
    assert "psycopg" not in names


def test_every_rendered_percentage_has_two_decimals(seeded_database, database):
    """The formatting helpers are shared, so the ORM's output obeys the rule too."""
    text = "\n".join(result.answer for result in orm_queries.answer_all())
    percentages = PERCENT_PATTERN.findall(text)

    assert percentages
    assert all(TWO_DECIMAL_PERCENT.fullmatch(p) for p in percentages)


def test_question_ten_has_nothing_to_rank_on_an_empty_table(empty_database, database):
    """The same "nothing to say" branch as the raw-SQL version."""
    result = orm_queries.answer_all([10])[0]

    assert result.answer_lines == ["No entries with a standardized university."]


def test_question_eleven_needs_both_cohorts(empty_database, database):
    result = orm_queries.answer_all([11])[0]

    assert result.answer_lines == ["Not enough nationality data to compare cohorts."]


def test_question_three_diagnoses_impossible_scores(seeded_database, database):
    """The supporting findings fire on the ORM side as well."""
    result = orm_queries.answer_all([3])[0]

    supporting = " ".join(result.supporting)
    assert "260-340" in supporting
    assert "back inside the valid 130-170 band" in supporting


def test_question_three_says_nothing_extra_when_there_is_nothing_to_explain(
    empty_database, database
):
    result = orm_queries.answer_all([3])[0]

    assert result.supporting == [
        "How much of each average is an artifact of impossible values:"
    ]


# ----------------------------------------------------------------------
# --compare
# ----------------------------------------------------------------------
def test_compare_reports_agreement(seeded_database, database, capsys):
    """The two implementations agree on real rows, question by question."""
    assert orm_queries._compare_with_raw_sql() == 0

    printed = capsys.readouterr().out
    assert printed.count("match") == 11
    assert "All 11 questions agree." in printed


def test_compare_reports_a_disagreement(seeded_database, database, monkeypatch, capsys):
    """A disagreement is named, with both answers printed side by side.

    Forced by making the ORM answer something else, so the failure path is
    exercised rather than hoped for.
    """
    real = orm_queries.answer_all

    def wrong(numbers=None):
        results = real(numbers)
        results[0].answer_lines = ["Fall 2026 applicant count: 999"]
        return results

    monkeypatch.setattr(orm_queries, "answer_all", wrong)

    assert orm_queries._compare_with_raw_sql() == 1

    printed = capsys.readouterr().out
    assert "Question  1: MISMATCH" in printed
    assert "sql | Fall 2026 applicant count: 11" in printed
    assert "orm | Fall 2026 applicant count: 999" in printed
    assert "1 question(s) disagree." in printed


# ----------------------------------------------------------------------
# python orm_queries.py
# ----------------------------------------------------------------------
def test_main_prints_every_answer(seeded_database, database, capsys):
    assert orm_queries.main([]) == 0

    printed = capsys.readouterr().out
    assert "Grad Cafe analysis -- SQLAlchemy ORM" in printed
    assert "Question 11" in printed


def test_main_can_answer_a_subset_with_the_generated_sql(
    seeded_database, database, capsys
):
    """``--sql`` shows what SQLAlchemy emitted, which is the point of the file."""
    assert orm_queries.main(["--questions", "1", "--sql"]) == 0

    printed = capsys.readouterr().out
    assert "Question 1:" in printed
    assert "Question 2:" not in printed
    assert "| SELECT" in printed


def test_main_can_run_the_comparison(seeded_database, database, capsys):
    assert orm_queries.main(["--compare"]) == 0
    assert "All 11 questions agree." in capsys.readouterr().out


def test_main_rejects_a_selection_matching_nothing(capsys):
    assert orm_queries.main(["--questions", "99"]) == 1
    assert "No questions matched" in capsys.readouterr().err


def test_main_explains_a_database_it_cannot_reach(monkeypatch, capsys):
    """Advice rather than a SQLAlchemy traceback, and exit code 1."""

    def refuse(_numbers=None):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(orm_queries, "answer_all", refuse)

    assert orm_queries.main([]) == 1
    assert "Is PostgreSQL running" in capsys.readouterr().err


def test_the_two_modules_share_their_formatting(seeded_database, database):
    """One set of helpers, so the console and the page cannot drift apart."""
    assert orm_queries.fmt_pct is query_data.fmt_pct
    assert orm_queries.fmt_avg is query_data.fmt_avg
    assert orm_queries.fmt_count is query_data.fmt_count
