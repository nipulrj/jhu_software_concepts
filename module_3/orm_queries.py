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
    QUESTION_3_CAVEAT,
    QUESTION_9_CAVEAT,
    QUESTION_10_CAVEAT,
    QUESTION_11_CAVEAT,
    QuestionResult,
    parse_selection,
    fmt_avg,
    fmt_count,
    fmt_pct,
    fmt_signed,
    print_results,
    use_utf8_console,
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

    # Supporting analysis rather than part of the answer: how much of each
    # average is an artifact of values that are impossible on their own scale.
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

    validity = session.execute(
        select(
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
    ).one()

    # The impossible GRE Quantitative values are not random noise, and saying so
    # needs evidence.  Verbal and Quantitative are each reported on 130-170 and
    # the combined total on 260-340, so a total typed into the Quantitative box
    # should land in that second band -- and subtracting the verbal score the
    # same row reports should leave a believable section score.
    in_total_range = Applicant.gre.between(260, 340)
    impossible_gre = and_(
        Applicant.gre.is_not(None),
        or_(Applicant.gre < 130, Applicant.gre > 170),
    )
    has_verbal = and_(in_total_range, Applicant.gre_v.is_not(None))

    impossible, in_range_total, with_verbal, implied_quant = session.execute(
        select(
            _count_where(impossible_gre),
            _count_where(in_total_range),
            _count_where(has_verbal),
            _avg2(case((has_verbal, Applicant.gre - Applicant.gre_v), else_=None)),
        ).select_from(Applicant)
    ).one()

    table_rows: List[List[str]] = []
    for index, (label, _column, _in_range, _out_of_range) in enumerate(metrics):
        reported, out_of_range, average_all, average_in_range = validity[
            index * 4 : index * 4 + 4
        ]
        shift = None
        if average_all is not None and average_in_range is not None:
            shift = float(average_all) - float(average_in_range)
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

    supporting = [
        "How much of each average is an artifact of impossible values:",
    ]
    if impossible and in_range_total:
        supporting.append(
            "{0} of the {1} impossible GRE Quantitative values ({2}) fall in 260-340, "
            "the official combined Verbal+Quantitative range".format(
                fmt_count(in_range_total),
                fmt_count(impossible),
                fmt_pct(100.0 * in_range_total / impossible),
            )
        )
    if with_verbal and implied_quant is not None:
        supporting.append(
            "Subtracting the verbal score from the {0} of those that report one "
            "leaves a mean of {1} -- back inside the valid 130-170 band".format(
                fmt_count(with_verbal), fmt_avg(implied_quant)
            )
        )

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
            "applicant needs all four values to contribute to one of them. Two "
            "supporting statements follow, built from the same expressions: one "
            "re-computes each average with the impossible values removed, the "
            "other tests what those values are. Where the handwritten version "
            "repeats a near-identical SELECT four times in a UNION ALL, here the "
            "repetition is a comprehension over a list of (column, valid-range) "
            "pairs -- a fifth metric would be one more tuple."
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
        supporting=supporting,
        caveat=QUESTION_3_CAVEAT,
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
        caveat=QUESTION_9_CAVEAT,
    )


def question_10(session: Session) -> QuestionResult:
    """Original question 1 of 2 -- the one repeated here for Part 6."""
    most_applied_to = (
        select(
            Applicant.llm_generated_university.label("university"),
            func.count().label("entries"),
            _count_where(IS_ACCEPTED).label("acceptances"),
            _count_where(
                and_(IS_ACCEPTED, Applicant.gpa.is_not(None))
            ).label("accepted_with_gpa"),
            func.avg(
                case((IS_ACCEPTED, Applicant.gpa), else_=None)
            ).label("avg_gpa_accepted"),
        )
        .where(Applicant.llm_generated_university.is_not(None))
        .group_by(Applicant.llm_generated_university)
        .order_by(desc("entries"))
        .limit(20)
        .subquery()
    )

    acceptance_percent = _percent(
        most_applied_to.c.acceptances, most_applied_to.c.entries
    ).label("acceptance_percent")

    statement = select(
        most_applied_to.c.university,
        most_applied_to.c.entries,
        most_applied_to.c.acceptances,
        acceptance_percent,
        most_applied_to.c.accepted_with_gpa,
        func.round(cast(most_applied_to.c.avg_gpa_accepted, Numeric), 2).label(
            "avg_gpa_accepted"
        ),
    ).order_by("acceptance_percent")

    rows = session.execute(statement).all()

    answer_lines = []
    if rows:
        hardest, easiest = rows[0], rows[-1]
        answer_lines.append(
            "Hardest of the twenty most-applied-to universities: {0} at {1} "
            "({2} of {3} entries), average GPA of those accepted {4}".format(
                hardest[0], fmt_pct(hardest[3]), fmt_count(hardest[2]),
                fmt_count(hardest[1]), fmt_avg(hardest[5]),
            )
        )
        answer_lines.append(
            "Easiest: {0} at {1} ({2} of {3} entries), average GPA of those "
            "accepted {4}".format(
                easiest[0], fmt_pct(easiest[3]), fmt_count(easiest[2]),
                fmt_count(easiest[1]), fmt_avg(easiest[5]),
            )
        )
        gpas = [float(row[5]) for row in rows if row[5] is not None]
        rates = [float(row[3]) for row in rows if row[3] is not None]
        if gpas and rates:
            answer_lines.append(
                "Acceptance rate across the twenty spans {0} to {1}, but the "
                "average GPA of those accepted spans only {2} to {3} -- "
                "selectivity barely shows up in the GPA of who gets in".format(
                    fmt_pct(min(rates)), fmt_pct(max(rates)),
                    fmt_avg(min(gpas)), fmt_avg(max(gpas)),
                )
            )
    else:
        answer_lines.append("No entries with a standardized university.")

    return QuestionResult(
        number=10,
        question=(
            "Of the twenty universities that applicants apply to most, which is "
            "hardest to get into, and does a lower acceptance rate come with a "
            "stronger GPA among those who are accepted?"
        ),
        answer_lines=answer_lines,
        sql=_sql(statement),
        explanation=(
            "The grouped query is built as a .subquery() and selected from, which "
            "is how the ORM expresses the derived table the handwritten version "
            "writes as a bracketed FROM clause. Two conditional aggregates run "
            "over the same scan -- one counting acceptances, one averaging GPA "
            "across only the accepted -- built from the same IS_ACCEPTED "
            "predicate the other questions use. Grouping on the standardized "
            "university keeps one school in one group rather than splitting it "
            "across its several raw spellings."
        ),
        table={
            "columns": [
                "University",
                "Entries",
                "Acceptances",
                "Acceptance rate",
                "Accepted w/ GPA",
                "Avg GPA (accepted)",
            ],
            "rows": [
                [
                    row[0],
                    fmt_count(row[1]),
                    fmt_count(row[2]),
                    fmt_pct(row[3]),
                    fmt_count(row[4]),
                    fmt_avg(row[5]),
                ]
                for row in rows
            ],
        },
        caveat=QUESTION_10_CAVEAT,
        original=True,
    )


def question_11(session: Session) -> QuestionResult:
    """Original question 2 of 2."""
    cohort = func.initcap(func.trim(Applicant.us_or_international)).label("cohort")
    acceptance_percent = _percent(_count_where(IS_ACCEPTED), func.count()).label(
        "acceptance_percent"
    )

    statement = (
        select(
            cohort,
            func.count().label("entries"),
            _count_where(IS_ACCEPTED).label("acceptances"),
            acceptance_percent,
            _percent(func.count(Applicant.gpa), func.count()).label("gpa_disclosure"),
            _avg2(Applicant.gpa).label("avg_gpa"),
        )
        .select_from(Applicant)
        .where(HAS_NATIONALITY)
        .group_by(cohort)
        .order_by(desc("acceptance_percent"))
    )

    rows = session.execute(statement).all()
    by_cohort = {row[0]: row for row in rows}
    american = by_cohort.get("American")
    international = by_cohort.get("International")

    answer_lines = []
    if american and international:
        answer_lines.append(
            "Acceptance rate: {0} American vs {1} international -- a gap of "
            "{2:.2f} points".format(
                fmt_pct(american[3]),
                fmt_pct(international[3]),
                float(american[3]) - float(international[3]),
            )
        )
        answer_lines.append(
            "But the sharper gap is in what they disclose: {0} of American "
            "entries report a GPA against {1} of international ones".format(
                fmt_pct(american[4]), fmt_pct(international[4])
            )
        )
        answer_lines.append(
            "Yet the GPAs they do report are near identical -- {0} American, "
            "{1} international".format(
                fmt_avg(american[5]), fmt_avg(international[5])
            )
        )
    else:
        answer_lines.append("Not enough nationality data to compare cohorts.")

    return QuestionResult(
        number=11,
        question=(
            "Do international applicants fare differently from American ones -- "
            "and are they equally willing to say what their GPA was?"
        ),
        answer_lines=answer_lines,
        sql=_sql(statement),
        explanation=(
            "One grouped pass over the mapped column, reusing the HAS_NATIONALITY "
            "predicate Question 2 defines so 'did not say' is excluded the same "
            "way in both. Three measures per cohort: the acceptance rate, the "
            "share of entries that disclose a GPA at all -- func.count() over the "
            "column counts only non-NULLs, which is exactly the disclosure rate "
            "-- and the average of the GPAs disclosed."
        ),
        table={
            "columns": [
                "Cohort",
                "Entries",
                "Acceptances",
                "Acceptance rate",
                "Report a GPA",
                "Avg GPA",
            ],
            "rows": [
                [
                    row[0],
                    fmt_count(row[1]),
                    fmt_count(row[2]),
                    fmt_pct(row[3]),
                    fmt_pct(row[4]),
                    fmt_avg(row[5]),
                ]
                for row in rows
            ],
        },
        caveat=QUESTION_11_CAVEAT,
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


def run_all(
    session: Session, numbers: Optional[Sequence[int]] = None
) -> List[QuestionResult]:
    """Answer every question against an open Session, or just ``numbers``."""
    chosen = QUESTIONS if numbers is None else [QUESTIONS[n - 1] for n in numbers]
    return [question(session) for question in chosen]


def answer_all(numbers: Optional[Sequence[int]] = None) -> List[QuestionResult]:
    """Open a Session, answer the questions, close it again."""
    with SessionLocal() as session:
        return run_all(session, numbers)


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
    parser.add_argument(
        "--questions",
        metavar="SPEC",
        help="answer only these, e.g. 1-6 or 1,4,5 (default: all eleven)",
    )
    args = parser.parse_args(argv)

    use_utf8_console()

    try:
        numbers = parse_selection(args.questions, len(QUESTIONS))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        if args.compare:
            return _compare_with_raw_sql()
        results = answer_all(numbers)
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
