"""Answer the same analysis questions through the SQLAlchemy ORM.

The assignment requires Questions 1, 4, 5, 8, 9 and one of my own to be repeated
here.  All eleven are implemented instead, for one practical reason: the Flask
page must read the database through the ``Applicant`` model, and answering only
six here would have forced the other five to be written a second time inside the
web app.  ``app.py`` therefore imports straight from this module.

No raw SQL appears anywhere in this file -- no ``text()``, no psycopg cursor.
Every answer is built from ``select()``, ``func`` and the ``Applicant`` columns,
and SQLAlchemy emits the statement.

    python orm_queries.py             # print all eleven answers
    python orm_queries.py --sql       # also print the SQL SQLAlchemy generated
    python orm_queries.py --compare   # check these answers against query_data.py
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, List, Optional, Sequence

from sqlalchemy import Numeric, and_, case, cast, desc, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import db_config
from models import Applicant, SessionLocal
from query_data import (
    QuestionResult,
    fmt_avg,
    fmt_count,
    fmt_pct,
    fmt_signed,
    print_results,
)

# ----------------------------------------------------------------------
# Reusable ORM predicates
#
# Unlike the raw-SQL file -- where each statement is written out in full so the
# PDF shows exactly what ran -- these are shared, because SQLAlchemy composes
# them into the final statement rather than pasting strings together.  That
# composability is one of the real advantages of the ORM, so the file is written
# the way one actually would.
# ----------------------------------------------------------------------
IS_FALL_2026 = func.lower(func.trim(Applicant.term)) == "fall 2026"
IS_FALL_2025 = func.lower(func.trim(Applicant.term)) == "fall 2025"

IS_ACCEPTED = Applicant.status.ilike("accept%")

IS_AMERICAN = func.lower(func.trim(Applicant.us_or_international)) == "american"
IS_INTERNATIONAL = func.lower(func.trim(Applicant.us_or_international)) == "international"

HAS_NATIONALITY = and_(
    Applicant.us_or_international.is_not(None),
    func.trim(Applicant.us_or_international) != "",
)

IS_PHD = Applicant.degree.op("~*")(r"^ph\.?\s*d")

IS_MASTERS = or_(
    Applicant.degree.ilike("master%"),
    func.lower(func.trim(Applicant.degree)).in_(
        ["ms", "m.s.", "msc", "m.sc.", "meng", "m.eng."]
    ),
)

IS_JHU = or_(
    Applicant.program.ilike("%johns hopkins%"),
    Applicant.program.op("~*")(r"\mjhu\M"),
)

IS_CS_ORIGINAL = Applicant.program.ilike("%computer science%")
IS_CS_LLM = Applicant.llm_generated_program.ilike("%computer science%")

FOUR_UNIVERSITIES_ORIGINAL = or_(
    Applicant.program.ilike("%georgetown%"),
    Applicant.program.ilike("%massachusetts institute of technology%"),
    Applicant.program.op("~*")(r"\mmit\M"),
    Applicant.program.ilike("%stanford%"),
    Applicant.program.ilike("%carnegie mellon%"),
    Applicant.program.op("~*")(r"\mcmu\M"),
)

FOUR_UNIVERSITIES_LLM = or_(
    Applicant.llm_generated_university.ilike("%georgetown%"),
    Applicant.llm_generated_university.ilike("%massachusetts institute of technology%"),
    Applicant.llm_generated_university.op("~*")(r"\mmit\M"),
    Applicant.llm_generated_university.ilike("%stanford%"),
    Applicant.llm_generated_university.ilike("%carnegie mellon%"),
    Applicant.llm_generated_university.op("~*")(r"\mcmu\M"),
)


def _sql(statement: Any) -> str:
    """Render the SQL SQLAlchemy will send, for display in --sql mode."""
    return str(statement.compile(compile_kwargs={"literal_binds": True}))


def _percent(numerator: Any, denominator: Any) -> Any:
    """A rounded percentage expression, guarded against an empty denominator."""
    return func.round(
        cast(100.0 * numerator / func.nullif(denominator, 0), Numeric), 2
    )


def _avg2(column: Any) -> Any:
    """An average rounded to the two decimal places the assignment requires.

    The cast to Numeric is what makes ROUND(..., 2) available: PostgreSQL only
    offers the two-argument round() for numeric, not for double precision.
    """
    return func.round(cast(func.avg(column), Numeric), 2)


# ``count(*) FILTER (WHERE ...)`` has no direct ORM spelling, but the portable
# equivalent -- summing a CASE -- does, and SQLAlchemy renders it for any
# backend.  This keeps the percentages readable without dropping into raw SQL.
def _count_where(condition: Any) -> Any:
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


# ----------------------------------------------------------------------
# The eleven questions
# ----------------------------------------------------------------------
def question_1(session: Session) -> QuestionResult:
    """Required by Part 6."""
    statement = select(func.count()).select_from(Applicant).where(IS_FALL_2026)
    value = session.scalar(statement)
    return QuestionResult(
        number=1,
        question="How many entries in the database are from applicants who applied for Fall 2026?",
        answer_lines=["Fall 2026 applicant count: {0}".format(fmt_count(value))],
        sql=_sql(statement),
        explanation=(
            "select(func.count()).select_from(Applicant) is the ORM spelling of "
            "SELECT COUNT(*) FROM applicants; the term filter is attached with "
            ".where(). func.lower and func.trim map onto the same SQL functions "
            "the handwritten query uses, so the two produce identical SQL."
        ),
    )


def question_2(session: Session) -> QuestionResult:
    statement = select(
        _percent(_count_where(IS_INTERNATIONAL), func.count())
    ).select_from(Applicant).where(HAS_NATIONALITY)
    value = session.scalar(statement)
    return QuestionResult(
        number=2,
        question=(
            "Among entries that provide a nationality classification, what "
            "percentage are international students?"
        ),
        answer_lines=["Percent international: {0}".format(fmt_pct(value))],
        sql=_sql(statement),
        explanation=(
            "The .where() clause restricts the denominator to rows with a usable "
            "classification, and the numerator sums a CASE expression over that "
            "same set -- the portable ORM equivalent of COUNT(*) FILTER."
        ),
    )


def question_3(session: Session) -> QuestionResult:
    statement = select(
        _avg2(Applicant.gpa),
        _avg2(Applicant.gre),
        _avg2(Applicant.gre_v),
        _avg2(Applicant.gre_aw),
    ).select_from(Applicant)
    gpa, gre, gre_v, gre_aw = session.execute(statement).one()
    return QuestionResult(
        number=3,
        question=(
            "What are the average GPA, GRE Quantitative, GRE Verbal and GRE "
            "Analytical Writing scores of applicants who provide each metric?"
        ),
        answer_lines=[
            "Average GPA: {0}".format(fmt_avg(gpa)),
            "Average GRE Quantitative: {0}".format(fmt_avg(gre)),
            "Average GRE Verbal: {0}".format(fmt_avg(gre_v)),
            "Average GRE Analytical Writing: {0}".format(fmt_avg(gre_aw)),
        ],
        sql=_sql(statement),
        explanation=(
            "func.avg maps to SQL's AVG, which ignores NULLs, so each metric is "
            "averaged over exactly the applicants who reported it and no "
            "applicant needs all four values to contribute to one of them."
        ),
    )


def question_4(session: Session) -> QuestionResult:
    """Required by Part 6."""
    statement = (
        select(_avg2(Applicant.gpa))
        .select_from(Applicant)
        .where(and_(IS_FALL_2026, IS_AMERICAN, Applicant.gpa.is_not(None)))
    )
    value = session.scalar(statement)
    return QuestionResult(
        number=4,
        question="What is the average GPA of American applicants who applied for Fall 2026?",
        answer_lines=["Average GPA American: {0}".format(fmt_avg(value))],
        sql=_sql(statement),
        explanation=(
            "and_() combines the three conditions the question names. Written as "
            "reusable Python objects, IS_FALL_2026 and IS_AMERICAN are the same "
            "expressions Questions 1 and 2 use, so the term and nationality "
            "matching cannot drift between questions."
        ),
    )


def question_5(session: Session) -> QuestionResult:
    """Required by Part 6."""
    statement = (
        select(_percent(_count_where(IS_ACCEPTED), func.count()))
        .select_from(Applicant)
        .where(IS_FALL_2025)
    )
    value = session.scalar(statement)
    return QuestionResult(
        number=5,
        question="What percentage of Fall 2025 entries are acceptances?",
        answer_lines=["Fall 2025 acceptance percentage: {0}".format(fmt_pct(value))],
        sql=_sql(statement),
        explanation=(
            "The denominator is every Fall 2025 row and the numerator counts "
            "only the acceptances among them, both from a single pass. "
            "func.nullif keeps the division safe if the term is ever absent."
        ),
    )


def question_6(session: Session) -> QuestionResult:
    statement = (
        select(_avg2(Applicant.gpa))
        .select_from(Applicant)
        .where(and_(IS_FALL_2026, IS_ACCEPTED, Applicant.gpa.is_not(None)))
    )
    value = session.scalar(statement)
    return QuestionResult(
        number=6,
        question="What is the average GPA of accepted applicants who applied for Fall 2026?",
        answer_lines=["Average GPA Acceptance: {0}".format(fmt_avg(value))],
        sql=_sql(statement),
        explanation=(
            "Question 4 with the acceptance predicate substituted for the "
            "nationality one -- the clearest demonstration of why the predicates "
            "above are defined once and reused."
        ),
    )


def question_7(session: Session) -> QuestionResult:
    statement = (
        select(func.count())
        .select_from(Applicant)
        .where(and_(IS_JHU, IS_CS_ORIGINAL, IS_MASTERS))
    )
    value = session.scalar(statement)
    return QuestionResult(
        number=7,
        question=(
            "How many entries are from applicants who applied to Johns Hopkins "
            "University for a master's degree in Computer Science?"
        ),
        answer_lines=["JHU Masters Computer Science count: {0}".format(fmt_count(value))],
        sql=_sql(statement),
        explanation=(
            "Uses the original downloaded 'program' and 'degree' columns. "
            ".op('~*') reaches PostgreSQL's case-insensitive regex operator "
            "through the ORM, which is how 'JHU' is matched on word boundaries "
            "rather than as a bare substring."
        ),
    )


def question_8(session: Session) -> QuestionResult:
    """Required by Part 6."""
    statement = (
        select(func.count())
        .select_from(Applicant)
        .where(
            and_(
                IS_FALL_2026,
                IS_ACCEPTED,
                IS_PHD,
                IS_CS_ORIGINAL,
                FOUR_UNIVERSITIES_ORIGINAL,
            )
        )
    )
    value = session.scalar(statement)
    return QuestionResult(
        number=8,
        question=(
            "How many Fall 2026 entries are acceptances from applicants applying "
            "for a PhD in Computer Science at Georgetown, MIT, Stanford or "
            "Carnegie Mellon? (original downloaded fields)"
        ),
        answer_lines=["Original-field count: {0}".format(fmt_count(value))],
        sql=_sql(statement),
        explanation=(
            "and_() applies all five restrictions simultaneously while the "
            "university test stays an or_() nested inside it. The ORM enforces "
            "that grouping structurally -- an or_() is one object -- which is "
            "the mistake that is easiest to make and hardest to see in the "
            "handwritten version of this query."
        ),
    )


def question_9(session: Session) -> QuestionResult:
    """Required by Part 6."""
    statement = (
        select(func.count())
        .select_from(Applicant)
        .where(and_(IS_FALL_2026, IS_ACCEPTED, IS_PHD, IS_CS_LLM, FOUR_UNIVERSITIES_LLM))
    )
    llm_value = session.scalar(statement)
    original_value = session.scalar(
        select(func.count())
        .select_from(Applicant)
        .where(
            and_(
                IS_FALL_2026,
                IS_ACCEPTED,
                IS_PHD,
                IS_CS_ORIGINAL,
                FOUR_UNIVERSITIES_ORIGINAL,
            )
        )
    )

    difference = None
    if llm_value is not None and original_value is not None:
        difference = int(llm_value) - int(original_value)

    return QuestionResult(
        number=9,
        question=(
            "Repeat Question 8 using the LLM-generated program and university "
            "fields, and compare the two counts."
        ),
        answer_lines=[
            "Original-field count: {0}".format(fmt_count(original_value)),
            "LLM-field count: {0}".format(fmt_count(llm_value)),
            "Difference: {0}".format(fmt_signed(difference)),
        ],
        sql=_sql(statement),
        explanation=(
            "Only the department and university predicates change: term, degree "
            "and status still read the original downloaded columns, as the "
            "question specifies. Swapping two names in the and_() is the whole "
            "edit, which makes the comparison a genuinely controlled one."
        ),
    )


def question_10(session: Session) -> QuestionResult:
    """Original question 1 of 2 -- the one repeated here for Part 6."""
    busiest = (
        select(
            Applicant.llm_generated_university.label("university"),
            func.count().label("entries"),
            _count_where(IS_ACCEPTED).label("acceptances"),
        )
        .where(
            and_(IS_FALL_2026, Applicant.llm_generated_university.is_not(None))
        )
        .group_by(Applicant.llm_generated_university)
        .order_by(desc("entries"))
        .limit(10)
        .subquery()
    )

    statement = select(
        busiest.c.university,
        busiest.c.entries,
        busiest.c.acceptances,
        _percent(busiest.c.acceptances, busiest.c.entries).label("acceptance_percent"),
    ).order_by(desc("acceptance_percent"))

    rows = session.execute(statement).all()

    answer_lines = []
    if rows:
        best, lowest = rows[0], rows[-1]
        answer_lines.append(
            "Highest acceptance rate among the ten busiest Fall 2026 universities: "
            "{0} at {1} ({2} of {3} entries)".format(
                best[0], fmt_pct(best[3]), fmt_count(best[2]), fmt_count(best[1])
            )
        )
        answer_lines.append(
            "Lowest: {0} at {1} ({2} of {3} entries)".format(
                lowest[0], fmt_pct(lowest[3]), fmt_count(lowest[2]), fmt_count(lowest[1])
            )
        )
    else:
        answer_lines.append("No Fall 2026 entries with a standardized university.")

    return QuestionResult(
        number=10,
        question=(
            "Of the ten universities with the most Fall 2026 entries, which "
            "reports the highest acceptance rate, and how wide is the spread?"
        ),
        answer_lines=answer_lines,
        sql=_sql(statement),
        explanation=(
            "The grouped query is built as a .subquery() and selected from, which "
            "is how the ORM expresses the derived table the handwritten version "
            "writes as a bracketed FROM clause. Grouping on the standardized "
            "university keeps one school in one group rather than splitting it "
            "across its several raw spellings."
        ),
        table={
            "columns": ["University", "Entries", "Acceptances", "Acceptance rate"],
            "rows": [
                [row[0], fmt_count(row[1]), fmt_count(row[2]), fmt_pct(row[3])]
                for row in rows
            ],
        },
        original=True,
    )


def question_11(session: Session) -> QuestionResult:
    """Original question 2 of 2."""
    metrics = (
        ("GPA (0-4.0)", Applicant.gpa, Applicant.gpa <= 4.0, Applicant.gpa > 4.0),
        (
            "GRE Quantitative (130-170)",
            Applicant.gre,
            Applicant.gre.between(130, 170),
            or_(Applicant.gre < 130, Applicant.gre > 170),
        ),
        (
            "GRE Verbal (130-170)",
            Applicant.gre_v,
            Applicant.gre_v.between(130, 170),
            or_(Applicant.gre_v < 130, Applicant.gre_v > 170),
        ),
        (
            "GRE Analytical Writing (0-6)",
            Applicant.gre_aw,
            Applicant.gre_aw.between(0, 6),
            or_(Applicant.gre_aw < 0, Applicant.gre_aw > 6),
        ),
    )

    statement = select(
        *[
            expression
            for _, column, in_range, out_of_range in metrics
            for expression in (
                func.count(column),
                _count_where(out_of_range),
                _avg2(column),
                _avg2(case((in_range, column), else_=None)),
            )
        ]
    ).select_from(Applicant)

    values = session.execute(statement).one()

    table_rows: List[List[str]] = []
    total_out_of_range = 0
    worst_metric: Optional[str] = None
    worst_shift = 0.0

    for index, (label, _column, _in_range, _out_of_range) in enumerate(metrics):
        reported, out_of_range, average_all, average_in_range = values[
            index * 4 : index * 4 + 4
        ]
        total_out_of_range += int(out_of_range or 0)

        shift = None
        if average_all is not None and average_in_range is not None:
            shift = float(average_all) - float(average_in_range)
            if abs(shift) > abs(worst_shift):
                worst_shift, worst_metric = shift, label

        table_rows.append(
            [
                label,
                fmt_count(reported),
                fmt_count(out_of_range),
                fmt_avg(average_all),
                fmt_avg(average_in_range),
                "n/a" if shift is None else "{0:+.2f}".format(shift),
            ]
        )

    answer_lines = [
        "Impossible values reported across the four metrics: {0}".format(
            fmt_count(total_out_of_range)
        )
    ]
    if worst_metric is not None:
        answer_lines.append(
            "Largest distortion: {0}, whose headline average is {1} too high".format(
                worst_metric, fmt_avg(abs(worst_shift))
            )
        )

    return QuestionResult(
        number=11,
        question=(
            "How many self-reported metrics are impossible for their own scale, "
            "and how far do they move the averages reported in Question 3?"
        ),
        answer_lines=answer_lines,
        sql=_sql(statement),
        explanation=(
            "Four aggregates per metric, built by a Python comprehension over a "
            "list of (column, valid-range) pairs and selected in one pass. Where "
            "the handwritten version repeats a near-identical SELECT four times "
            "in a UNION ALL, here the repetition is a loop -- adding a fifth "
            "metric would be one more tuple."
        ),
        table={
            "columns": [
                "Metric",
                "Reported",
                "Out of range",
                "Average (all)",
                "Average (in range)",
                "Distortion",
            ],
            "rows": table_rows,
        },
        original=True,
    )


# The six the assignment requires are 1, 4, 5, 8, 9 and one of my own (10);
# the rest are here so the Flask page can read everything through the ORM.
QUESTIONS = (
    question_1,
    question_2,
    question_3,
    question_4,
    question_5,
    question_6,
    question_7,
    question_8,
    question_9,
    question_10,
    question_11,
)

REQUIRED_BY_PART_6 = (1, 4, 5, 8, 9, 10)


def run_all(session: Session) -> List[QuestionResult]:
    """Answer every question against an open Session."""
    return [question(session) for question in QUESTIONS]


def answer_all() -> List[QuestionResult]:
    """Open a Session, answer every question, close it again."""
    with SessionLocal() as session:
        return run_all(session)


def _compare_with_raw_sql() -> int:
    """Check every ORM answer against the handwritten SQL answer."""
    import query_data

    orm_results = {result.number: result for result in answer_all()}
    sql_results = {result.number: result for result in query_data.answer_all()}

    failures = 0
    for number in sorted(sql_results):
        orm_answer = orm_results[number].answer
        sql_answer = sql_results[number].answer
        agree = orm_answer == sql_answer
        print("Question {0:>2}: {1}".format(number, "match" if agree else "MISMATCH"))
        if not agree:
            failures += 1
            for line in sql_answer.splitlines():
                print("    sql | {0}".format(line))
            for line in orm_answer.splitlines():
                print("    orm | {0}".format(line))

    print()
    if failures:
        print("{0} question(s) disagree.".format(failures))
    else:
        print("All {0} questions agree.".format(len(sql_results)))
    return 1 if failures else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Answer the Module 3 analysis questions with SQLAlchemy."
    )
    parser.add_argument(
        "--sql",
        action="store_true",
        help="print the SQL SQLAlchemy generated beneath each answer",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="check these answers against the raw SQL in query_data.py",
    )
    args = parser.parse_args(argv)

    try:
        if args.compare:
            return _compare_with_raw_sql()
        results = answer_all()
    except SQLAlchemyError as exc:
        print(
            "Could not query {where}:\n  {exc}\n"
            "Is PostgreSQL running, and has load_data.py been run?".format(
                where=db_config.describe(), exc=exc
            ),
            file=sys.stderr,
        )
        return 1

    print("Grad Cafe analysis -- SQLAlchemy ORM")
    print("Database: {0}\n".format(db_config.describe()))
    print_results(results, show_sql=args.sql)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
