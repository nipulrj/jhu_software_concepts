"""Answer the Module 3 analysis questions with raw SQL, executed via psycopg.

Every answer below is computed by PostgreSQL.  Python's only job is to hand the
statement over and format the number that comes back -- there is no filtering,
counting or averaging done in Python, so each query in ``query_results.pdf`` is
genuinely the thing that produced the result next to it.

    python query_data.py            # print all eleven answers
    python query_data.py --sql      # print each answer with its SQL

``orm_queries.py`` repeats these same analyses through SQLAlchemy and should
agree with this file exactly; ``python orm_queries.py --compare`` checks that.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import psycopg

import db_config

# Each question below carries its own complete, standalone statement rather than
# assembling one from shared fragments, so that what appears in
# ``query_results.pdf`` is exactly what ran.  Text matching is case-insensitive
# throughout, as the assignment asks: the cleaned data uses a small controlled
# vocabulary ("Fall 2026", "Accepted", "PhD", "American"), but the site's own
# casing is inconsistent and newly pulled rows go through the same matching.


# ----------------------------------------------------------------------
# Output formatting
#
# The assignment fixes these: counts as whole numbers, percentages and every
# average to two decimal places.  Console output, query_results.pdf and the
# Flask page all format through these three helpers, so they cannot drift apart.
# ----------------------------------------------------------------------
def fmt_count(value: Optional[Any]) -> str:
    """A whole number, with thousands separators."""
    if value is None:
        return "n/a"
    return "{0:,}".format(int(value))


def fmt_pct(value: Optional[Any]) -> str:
    """A percentage to two decimal places."""
    if value is None:
        return "n/a"
    return "{0:.2f}%".format(float(value))


def fmt_avg(value: Optional[Any]) -> str:
    """An average to two decimal places."""
    if value is None:
        return "n/a"
    return "{0:.2f}".format(float(value))


def fmt_signed(value: Optional[Any]) -> str:
    """A signed whole number, for the Question 8 vs 9 difference."""
    if value is None:
        return "n/a"
    return "{0:+,}".format(int(value))


def use_utf8_console() -> None:
    """Print UTF-8 whatever code page the Windows console is set to.

    University names in this data carry en-dashes and accents -- "University of
    Wisconsin-Madison" is stored with U+2013, and there is a "University of
    Hawai'i at Manoa".  A legacy Windows console defaults to cp1252 and turns
    those into replacement characters, which is a display bug rather than a data
    one but makes the console screenshots look wrong.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):  # a redirected or closed stream
            pass


# ----------------------------------------------------------------------
# Caveats
#
# These describe the data, not the query, so the ORM file reuses them verbatim
# rather than keeping a second copy that could drift.  They are shown beside the
# answer on the console, in the PDF and on the webpage, because in both cases the
# number is literally correct and still easy to misread.
# ----------------------------------------------------------------------
QUESTION_3_CAVEAT = (
    "These are the averages of exactly what applicants reported, which is "
    "what the question asks for -- but two of them are not usable as test "
    "scores. The GRE reports Verbal and Quantitative on 130-170 each, a "
    "combined Verbal+Quantitative total on 260-340, and Analytical Writing "
    "on 0-6. The Quantitative column here is bimodal, and the second mode "
    "falls in exactly the 260-340 band: those applicants entered their "
    "combined total into the box the site labels only 'GRE'. The two "
    "supporting checks above confirm that rather than assume it -- almost all "
    "of the impossible values land inside the official total range, and "
    "subtracting the verbal score the same rows report leaves a mean back "
    "inside 130-170. Analytical Writing is inflated the same way by "
    "placeholder values of 99.99 on a scale that stops at 6. The table above "
    "gives each average restricted to the values its scale permits; "
    "limitations.pdf discusses what follows from it."
)

QUESTION_9_CAVEAT = (
    "The two counts are equal here, and not by coincidence: they select "
    "the identical set of rows, not merely the same number of them. Grad "
    "Cafe stores each result's school as a foreign key into its own "
    "controlled vocabulary rather than as applicant free text, so these "
    "four well-known universities already arrive with one full, correctly "
    "spelled name ('Massachusetts Institute of Technology (MIT)', "
    "'Stanford University'). Standardizing a name that is already "
    "canonical cannot change which rows match. The standardizer is not "
    "idle overall -- it rewrites the university on 4,768 of the 50,000 "
    "rows, folding 'University of Wisconsin - Madison' and 'University of "
    "California (UCLA)' onto single canonical spellings -- but none of "
    "that work falls inside this particular filter. A difference would "
    "appear for schools the site stores inconsistently, or for a "
    "department name like 'CS' that only the LLM column spells out."
)

QUESTION_10_CAVEAT = (
    "An acceptance rate computed from Grad Cafe is not an admissions rate. It "
    "is the share of people who chose to post about one school and reported "
    "being admitted, and both halves of that are selected: someone holding an "
    "offer has more reason to come back and post than someone rejected "
    "everywhere. The ordering here is still informative because every school in "
    "the table is subject to the same posting behaviour, but the levels are not "
    "comparable to a published admissions rate."
)

QUESTION_11_CAVEAT = (
    "The disclosure gap has an innocent explanation that the acceptance gap "
    "does not. Grad Cafe offers a single GPA box with no scale, and much of the "
    "world does not grade on a 4.0 -- Module 2 found GPAs of 8.25 and 9.10 in "
    "this data, which are 10-point CGPAs. An applicant whose GPA does not fit "
    "the box has a good reason to leave it empty, so the lower international "
    "disclosure rate is at least partly a measurement artefact rather than "
    "reticence. That cuts both ways: it also means the international GPA "
    "average is drawn from a self-selected subset of international applicants, "
    "which is exactly the kind of hidden filtering limitations.pdf is about."
)


@dataclass
class QuestionResult:
    """One answered question, ready for the console, the PDF or the webpage."""

    number: int
    question: str
    answer_lines: List[str]
    sql: str
    explanation: str
    table: Optional[Dict[str, Any]] = None
    original: bool = False  # True for the two questions of my own
    caveat: Optional[str] = None  # shown beside the answer where it would mislead
    # Extra findings that support the answer without being part of it. Question 3
    # uses this to show what its own averages are worth without burying the four
    # numbers the question actually asked for.
    supporting: Optional[List[str]] = None

    @property
    def answer(self) -> str:
        return "\n".join(self.answer_lines)


# ----------------------------------------------------------------------
# Query execution
# ----------------------------------------------------------------------
def _scalar(connection: psycopg.Connection, sql: str) -> Any:
    """Run a statement that returns exactly one value."""
    with connection.cursor() as cursor:
        cursor.execute(sql)
        row = cursor.fetchone()
    return row[0] if row else None


def _row(connection: psycopg.Connection, sql: str) -> Sequence[Any]:
    """Run a statement that returns exactly one row."""
    with connection.cursor() as cursor:
        cursor.execute(sql)
        row = cursor.fetchone()
    return row if row else ()


def _rows(connection: psycopg.Connection, sql: str) -> List[Sequence[Any]]:
    """Run a statement that returns many rows."""
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return list(cursor.fetchall())


# ----------------------------------------------------------------------
# The eleven questions
# ----------------------------------------------------------------------
def question_1(connection: psycopg.Connection) -> QuestionResult:
    sql = """
SELECT COUNT(*) AS fall_2026_entries
FROM applicants
WHERE lower(trim(term)) = 'fall 2026';
"""
    value = _scalar(connection, sql)
    return QuestionResult(
        number=1,
        question="How many entries in the database are from applicants who applied for Fall 2026?",
        answer_lines=["Fall 2026 applicant count: {0}".format(fmt_count(value))],
        sql=sql.strip(),
        explanation=(
            "Counts every row whose term is Fall 2026. COUNT(*) is used rather "
            "than COUNT(term) because the filter has already guaranteed the "
            "column is non-NULL, and lower(trim(...)) makes the match "
            "insensitive to capitalisation and stray whitespace."
        ),
    )


def question_2(connection: psycopg.Connection) -> QuestionResult:
    sql = """
SELECT ROUND(
           100.0 * COUNT(*) FILTER (WHERE lower(trim(us_or_international)) = 'international')
           / NULLIF(COUNT(*), 0),
           2
       ) AS percent_international
FROM applicants
WHERE us_or_international IS NOT NULL
  AND trim(us_or_international) <> '';
"""
    value = _scalar(connection, sql)
    return QuestionResult(
        number=2,
        question=(
            "Among entries that provide a nationality classification, what "
            "percentage are international students?"
        ),
        answer_lines=["Percent international: {0}".format(fmt_pct(value))],
        sql=sql.strip(),
        explanation=(
            "The WHERE clause sets the denominator to entries carrying a usable "
            "classification, so missing values are excluded rather than counted "
            "as domestic. FILTER supplies the numerator from that same scan. "
            "'American' and 'Other' therefore sit in the denominator but not the "
            "numerator, which is what the question asks. NULLIF guards against "
            "division by zero on an empty table."
        ),
    )


def question_3(connection: psycopg.Connection) -> QuestionResult:
    sql = """
SELECT ROUND(AVG(gpa)::numeric, 2)    AS avg_gpa,
       ROUND(AVG(gre)::numeric, 2)    AS avg_gre_quant,
       ROUND(AVG(gre_v)::numeric, 2)  AS avg_gre_verbal,
       ROUND(AVG(gre_aw)::numeric, 2) AS avg_gre_aw
FROM applicants;
"""

    # Supporting analysis rather than part of the answer: how much of each
    # average above is an artefact of values that are impossible on their own
    # scale.  It lives with Question 3 because it is what tells you what
    # Question 3's four numbers are actually worth.
    validity_sql = """
SELECT 'GPA (0-4.0)'                          AS metric,
       COUNT(gpa)                             AS reported,
       COUNT(*) FILTER (WHERE gpa > 4.0)      AS out_of_range,
       ROUND(AVG(gpa)::numeric, 2)            AS average_all,
       ROUND(AVG(gpa) FILTER (WHERE gpa <= 4.0)::numeric, 2) AS average_in_range
FROM applicants
UNION ALL
SELECT 'GRE Quantitative (130-170)',
       COUNT(gre),
       COUNT(*) FILTER (WHERE gre < 130 OR gre > 170),
       ROUND(AVG(gre)::numeric, 2),
       ROUND(AVG(gre) FILTER (WHERE gre BETWEEN 130 AND 170)::numeric, 2)
FROM applicants
UNION ALL
SELECT 'GRE Verbal (130-170)',
       COUNT(gre_v),
       COUNT(*) FILTER (WHERE gre_v < 130 OR gre_v > 170),
       ROUND(AVG(gre_v)::numeric, 2),
       ROUND(AVG(gre_v) FILTER (WHERE gre_v BETWEEN 130 AND 170)::numeric, 2)
FROM applicants
UNION ALL
SELECT 'GRE Analytical Writing (0-6)',
       COUNT(gre_aw),
       COUNT(*) FILTER (WHERE gre_aw < 0 OR gre_aw > 6),
       ROUND(AVG(gre_aw)::numeric, 2),
       ROUND(AVG(gre_aw) FILTER (WHERE gre_aw BETWEEN 0 AND 6)::numeric, 2)
FROM applicants;
"""

    # The impossible GRE Quantitative values are not random noise, and saying so
    # needs evidence.  The GRE reports Verbal and Quantitative on 130-170 each
    # and a combined total on 260-340, so a total typed into the Quantitative box
    # should land in that second band -- and subtracting the verbal score the
    # same row reports should leave a believable section score.
    diagnosis_sql = """
SELECT COUNT(*) FILTER (WHERE gre IS NOT NULL AND (gre < 130 OR gre > 170)) AS impossible,
       COUNT(*) FILTER (WHERE gre BETWEEN 260 AND 340)                      AS in_total_range,
       COUNT(*) FILTER (WHERE gre BETWEEN 260 AND 340 AND gre_v IS NOT NULL) AS with_verbal,
       ROUND(AVG(gre - gre_v) FILTER (
                 WHERE gre BETWEEN 260 AND 340 AND gre_v IS NOT NULL)::numeric, 2)
                                                                            AS implied_quant
FROM applicants;
"""

    gpa, gre, gre_v, gre_aw = _row(connection, sql)
    validity_rows = _rows(connection, validity_sql)
    impossible, in_total_range, with_verbal, implied_quant = _row(
        connection, diagnosis_sql
    )

    table_rows = []
    for metric, reported, out_of_range, average_all, average_in_range in validity_rows:
        shift = None
        if average_all is not None and average_in_range is not None:
            shift = float(average_all) - float(average_in_range)
        table_rows.append(
            [
                metric,
                fmt_count(reported),
                fmt_count(out_of_range),
                fmt_avg(average_all),
                fmt_avg(average_in_range),
                "n/a" if shift is None else "{0:+.2f}".format(shift),
            ]
        )

    supporting = [
        "How much of each average is an artefact of impossible values:",
    ]
    if impossible and in_total_range:
        supporting.append(
            "{0} of the {1} impossible GRE Quantitative values ({2}) fall in 260-340, "
            "the official combined Verbal+Quantitative range".format(
                fmt_count(in_total_range),
                fmt_count(impossible),
                fmt_pct(100.0 * in_total_range / impossible),
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
        sql=(
            sql.strip()
            + "\n\n-- supporting: what those averages are worth\n"
            + validity_sql.strip()
            + "\n\n-- supporting: what the impossible values actually are\n"
            + diagnosis_sql.strip()
        ),
        explanation=(
            "SQL's AVG skips NULLs, so each average is taken over exactly the "
            "applicants who reported that one metric. Computing all four in a "
            "single statement keeps them independent -- an applicant with a GPA "
            "but no GRE still contributes to the GPA average, which is what the "
            "question requires. The two supporting statements are not part of the "
            "answer: the first re-computes each average with the impossible "
            "values removed so the distortion is visible, and the second tests "
            "what those values are rather than merely counting them. Both of the "
            "second one's predictions hold, which is why the caveat below states "
            "a diagnosis rather than a guess."
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


def question_4(connection: psycopg.Connection) -> QuestionResult:
    sql = """
SELECT ROUND(AVG(gpa)::numeric, 2) AS avg_gpa_american_fall_2026
FROM applicants
WHERE lower(trim(term)) = 'fall 2026'
  AND lower(trim(us_or_international)) = 'american'
  AND gpa IS NOT NULL;
"""
    value = _scalar(connection, sql)
    return QuestionResult(
        number=4,
        question="What is the average GPA of American applicants who applied for Fall 2026?",
        answer_lines=["Average GPA American: {0}".format(fmt_avg(value))],
        sql=sql.strip(),
        explanation=(
            "Three conditions applied together: the Fall 2026 term, an American "
            "classification, and a reported GPA. The explicit 'gpa IS NOT NULL' "
            "is redundant because AVG ignores NULLs anyway, but it states the "
            "intent of the question in the query itself."
        ),
    )


def question_5(connection: psycopg.Connection) -> QuestionResult:
    sql = """
SELECT ROUND(
           100.0 * COUNT(*) FILTER (WHERE status ILIKE 'accept%')
           / NULLIF(COUNT(*), 0),
           2
       ) AS fall_2025_acceptance_percent
FROM applicants
WHERE lower(trim(term)) = 'fall 2025';
"""
    value = _scalar(connection, sql)
    return QuestionResult(
        number=5,
        question="What percentage of Fall 2025 entries are acceptances?",
        answer_lines=["Fall 2025 acceptance percentage: {0}".format(fmt_pct(value))],
        sql=sql.strip(),
        explanation=(
            "The denominator is every Fall 2025 entry, as specified -- including "
            "rejections, waitlists and interviews -- while FILTER counts only "
            "those whose cleaned status begins with 'Accept'. One pass over the "
            "table produces both halves of the fraction."
        ),
    )


def question_6(connection: psycopg.Connection) -> QuestionResult:
    sql = """
SELECT ROUND(AVG(gpa)::numeric, 2) AS avg_gpa_accepted_fall_2026
FROM applicants
WHERE lower(trim(term)) = 'fall 2026'
  AND status ILIKE 'accept%'
  AND gpa IS NOT NULL;
"""
    value = _scalar(connection, sql)
    return QuestionResult(
        number=6,
        question="What is the average GPA of accepted applicants who applied for Fall 2026?",
        answer_lines=["Average GPA Acceptance: {0}".format(fmt_avg(value))],
        sql=sql.strip(),
        explanation=(
            "The same shape as Question 4, with the acceptance status swapped in "
            "for the nationality filter. Comparing the two answers against "
            "Question 3's overall GPA shows how little the reported GPA "
            "separates accepted applicants from the field as a whole."
        ),
    )


def question_7(connection: psycopg.Connection) -> QuestionResult:
    sql = """
SELECT COUNT(*) AS jhu_masters_cs_entries
FROM applicants
WHERE (program ILIKE '%johns hopkins%' OR program ~* '\\mjhu\\M')
  AND program ILIKE '%computer science%'
  AND (degree ILIKE 'master%'
       OR lower(trim(degree)) IN ('ms', 'm.s.', 'msc', 'm.sc.', 'meng', 'm.eng.'));
"""
    value = _scalar(connection, sql)
    return QuestionResult(
        number=7,
        question=(
            "How many entries are from applicants who applied to Johns Hopkins "
            "University for a master's degree in Computer Science?"
        ),
        answer_lines=["JHU Masters Computer Science count: {0}".format(fmt_count(value))],
        sql=sql.strip(),
        explanation=(
            "Uses the original downloaded fields, as the question requires. "
            "'program' holds '<program>, <university>', so one ILIKE matches the "
            "university half and another the department half. 'JHU' is matched "
            "with the word boundaries \\m and \\M rather than '%jhu%', which "
            "would also fire inside unrelated words. The degree test accepts the "
            "cleaned 'Masters' label plus the common abbreviations, but not MFA, "
            "MBA or JD, which are different credentials."
        ),
    )


# Question 9 needs this count as well as its own, so the statement lives here
# rather than being written out twice where the two could drift apart.
QUESTION_8_SQL = """
SELECT COUNT(*) AS cs_phd_acceptances_fall_2026
FROM applicants
WHERE lower(trim(term)) = 'fall 2026'
  AND status ILIKE 'accept%'
  AND degree ~* '^ph\\.?\\s*d'
  AND program ILIKE '%computer science%'
  AND (
        program ILIKE '%georgetown%'
        OR program ILIKE '%massachusetts institute of technology%'
        OR program ~* '\\mmit\\M'
        OR program ILIKE '%stanford%'
        OR program ILIKE '%carnegie mellon%'
        OR program ~* '\\mcmu\\M'
    );
"""


def question_8(connection: psycopg.Connection) -> QuestionResult:
    sql = QUESTION_8_SQL
    value = _scalar(connection, sql)
    return QuestionResult(
        number=8,
        question=(
            "How many Fall 2026 entries are acceptances from applicants applying "
            "for a PhD in Computer Science at Georgetown, MIT, Stanford or "
            "Carnegie Mellon? (original downloaded fields)"
        ),
        answer_lines=["Original-field count: {0}".format(fmt_count(value))],
        sql=sql.strip(),
        explanation=(
            "All five restrictions are applied at once in a single WHERE clause: "
            "term, status, degree, department and university. The university test "
            "is the bracketed OR group, so it narrows the row set rather than "
            "widening it -- without the parentheses the OR would defeat every "
            "AND above it and return far too many rows."
        ),
    )


def question_9(connection: psycopg.Connection) -> QuestionResult:
    sql = """
SELECT COUNT(*) AS cs_phd_acceptances_fall_2026_llm
FROM applicants
WHERE lower(trim(term)) = 'fall 2026'
  AND status ILIKE 'accept%'
  AND degree ~* '^ph\\.?\\s*d'
  AND llm_generated_program ILIKE '%computer science%'
  AND (
        llm_generated_university ILIKE '%georgetown%'
        OR llm_generated_university ILIKE '%massachusetts institute of technology%'
        OR llm_generated_university ~* '\\mmit\\M'
        OR llm_generated_university ILIKE '%stanford%'
        OR llm_generated_university ILIKE '%carnegie mellon%'
        OR llm_generated_university ~* '\\mcmu\\M'
    );
"""
    llm_value = _scalar(connection, sql)
    original_value = _scalar(connection, QUESTION_8_SQL)

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
        sql=sql.strip(),
        explanation=(
            "Identical to Question 8 except that the department and university "
            "come from the standardized columns, while term, degree and status "
            "still come from the original downloaded fields as instructed."
        ),
        caveat=QUESTION_9_CAVEAT,
    )


def question_10(connection: psycopg.Connection) -> QuestionResult:
    """Original question 1 of 2."""
    sql = """
SELECT university,
       entries,
       acceptances,
       ROUND(100.0 * acceptances / NULLIF(entries, 0), 2) AS acceptance_percent,
       accepted_with_gpa,
       ROUND(avg_gpa_accepted::numeric, 2)                AS avg_gpa_accepted
FROM (
    SELECT llm_generated_university                        AS university,
           COUNT(*)                                        AS entries,
           COUNT(*) FILTER (WHERE status ILIKE 'accept%')   AS acceptances,
           COUNT(gpa) FILTER (WHERE status ILIKE 'accept%') AS accepted_with_gpa,
           AVG(gpa) FILTER (WHERE status ILIKE 'accept%')   AS avg_gpa_accepted
    FROM applicants
    WHERE llm_generated_university IS NOT NULL
    GROUP BY llm_generated_university
    ORDER BY entries DESC
    LIMIT 20
) AS most_applied_to
ORDER BY acceptance_percent ASC;
"""
    rows = _rows(connection, sql)

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
        sql=sql.strip(),
        explanation=(
            "The inner query groups every entry by standardized university and "
            "keeps the twenty with the most submissions, so each rate rests on a "
            "real sample rather than on a school with three entries. For each it "
            "computes the acceptance rate and, separately, the average GPA of "
            "just the accepted applicants -- two FILTER clauses over the same "
            "scan. The outer query ranks by acceptance rate ascending, so the "
            "hardest school comes first. Grouping on the standardized column "
            "rather than the raw one matters here: the raw field spells the same "
            "school several ways, which would split one university across several "
            "groups and push all of them out of the top twenty."
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


def question_11(connection: psycopg.Connection) -> QuestionResult:
    """Original question 2 of 2."""
    sql = """
SELECT initcap(trim(us_or_international))                      AS cohort,
       COUNT(*)                                                AS entries,
       COUNT(*) FILTER (WHERE status ILIKE 'accept%')            AS acceptances,
       ROUND(100.0 * COUNT(*) FILTER (WHERE status ILIKE 'accept%')
             / NULLIF(COUNT(*), 0), 2)                          AS acceptance_percent,
       ROUND(100.0 * COUNT(gpa) / NULLIF(COUNT(*), 0), 2)       AS gpa_disclosure_percent,
       ROUND(AVG(gpa)::numeric, 2)                              AS avg_gpa
FROM applicants
WHERE us_or_international IS NOT NULL
  AND trim(us_or_international) <> ''
GROUP BY 1
ORDER BY acceptance_percent DESC;
"""
    rows = _rows(connection, sql)
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
        sql=sql.strip(),
        explanation=(
            "One pass grouped by nationality, computing three things per cohort: "
            "the acceptance rate, the share of entries that disclose a GPA at "
            "all, and the average of the GPAs that are disclosed. Entries with no "
            "nationality are excluded, as in Question 2, so 'did not say' is not "
            "silently counted as domestic. The disclosure percentage is the "
            "reason the question is worth asking: an average can only describe "
            "the people who answered, so knowing how many declined to answer is "
            "part of knowing what the average means."
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


def parse_selection(spec: Optional[str], available: int = len(QUESTIONS)) -> List[int]:
    """Turn ``"1-6"`` or ``"1,4,5"`` into a sorted list of question numbers.

    Handy for re-running a single question while working on it, and for printing
    the answers in screen-sized batches -- the full run is longer than a console
    window, so the screenshots in ``screenshots/`` are taken in two halves.
    """
    if not spec:
        return list(range(1, available + 1))

    wanted: set = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, _, end = part.partition("-")
            wanted.update(range(int(start), int(end) + 1))
        else:
            wanted.add(int(part))

    selected = sorted(number for number in wanted if 1 <= number <= available)
    if not selected:
        raise ValueError(
            "No questions matched {0!r}; valid numbers are 1-{1}.".format(spec, available)
        )
    return selected


def run_all(
    connection: psycopg.Connection, numbers: Optional[Sequence[int]] = None
) -> List[QuestionResult]:
    """Answer every question against an open connection, or just ``numbers``."""
    chosen = QUESTIONS if numbers is None else [QUESTIONS[n - 1] for n in numbers]
    return [question(connection) for question in chosen]


def answer_all(numbers: Optional[Sequence[int]] = None) -> List[QuestionResult]:
    """Open a connection, answer the questions, close it again."""
    with psycopg.connect(**db_config.connect_kwargs()) as connection:
        return run_all(connection, numbers)


# ----------------------------------------------------------------------
# Console output
# ----------------------------------------------------------------------
def _render_table(table: Dict[str, Any], indent: str = "    ") -> List[str]:
    """Format a result table as fixed-width text."""
    columns = table["columns"]
    rows = table["rows"]
    widths = [len(str(column)) for column in columns]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))

    def line(cells: Sequence[Any]) -> str:
        parts = []
        for index, cell in enumerate(cells):
            # First column left-aligned (names), the rest right-aligned (numbers).
            align = "<" if index == 0 else ">"
            parts.append("{0:{1}{2}}".format(str(cell), align, widths[index]))
        return indent + "  ".join(parts)

    out = [line(columns), indent + "  ".join("-" * width for width in widths)]
    out.extend(line(row) for row in rows)
    return out


def print_results(results: Sequence[QuestionResult], show_sql: bool = False) -> None:
    """Print every answer in the assignment's required format."""
    for result in results:
        label = "Question {0}".format(result.number)
        if result.original:
            label += " (my own)"
        print("{0}: {1}".format(label, result.question))
        for line in result.answer_lines:
            print("  {0}".format(line))
        if result.table:
            print()
            for line in _render_table(result.table):
                print(line)
        if result.supporting:
            print()
            for entry in result.supporting:
                wrapped = textwrap.wrap(entry, width=74)
                for index, line in enumerate(wrapped):
                    print("    {0} {1}".format("+" if index == 0 else " ", line))
        if result.caveat:
            print()
            for line in textwrap.wrap(result.caveat, width=76):
                print("    ! {0}".format(line))
        if show_sql:
            print()
            for line in result.sql.splitlines():
                print("    | {0}".format(line))
        print()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Answer the Module 3 analysis questions with raw SQL."
    )
    parser.add_argument(
        "--sql",
        action="store_true",
        help="print the SQL statement beneath each answer",
    )
    parser.add_argument(
        "--questions",
        metavar="SPEC",
        help="answer only these, e.g. 1-6 or 1,4,5 (default: all eleven)",
    )
    args = parser.parse_args(argv)

    use_utf8_console()

    try:
        numbers = parse_selection(args.questions)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        results = answer_all(numbers)
    except psycopg.OperationalError as exc:
        print(
            "Could not connect to PostgreSQL at {where}.\n  {exc}\n"
            "Is the server running, and does module_3/.env hold the right "
            "credentials?".format(where=db_config.describe(), exc=exc),
            file=sys.stderr,
        )
        return 1
    except psycopg.errors.UndefinedTable:
        print(
            "The applicants table does not exist yet. Run:\n"
            "    python load_data.py",
            file=sys.stderr,
        )
        return 1

    print("Grad Cafe analysis -- raw SQL via psycopg")
    print("Database: {0}\n".format(db_config.describe()))
    print_results(results, show_sql=args.sql)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
