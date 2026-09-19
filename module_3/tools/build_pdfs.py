"""Build ``query_results.pdf`` and ``limitations.pdf``.

``query_results.pdf`` is generated from a live run of ``query_data.py`` rather
than typed out by hand, so the result printed beside each query is necessarily
the one that query produced.  Re-run this after a Pull Data and the numbers
follow.

``limitations.pdf`` is written prose, but the figures quoted in it are pulled
from the same run, so the essay cannot end up citing stale numbers either.

    python tools/build_pdfs.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_JUSTIFY  # noqa: E402
from reportlab.lib.pagesizes import LETTER  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import inch  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

import db_config  # noqa: E402
import load_data  # noqa: E402
import query_data  # noqa: E402

AUTHOR = "Nipul Jayasekera"
JHED = "njayase1"
COURSE = "EN.605.256 Modern Software Concepts in Python"

INK = colors.HexColor("#1b1f24")
SOFT = colors.HexColor("#4a5462")
ACCENT = colors.HexColor("#1d4ed8")
RULE = colors.HexColor("#dfe3e8")
CODE_BG = colors.HexColor("#f4f6f8")
CAVEAT_BG = colors.HexColor("#fffbeb")
CAVEAT_RULE = colors.HexColor("#fcd9a4")


def _styles() -> Dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontSize=19, leading=23,
            textColor=INK, spaceAfter=4,
        ),
        "byline": ParagraphStyle(
            "byline", parent=base["Normal"], fontSize=9.5, leading=13,
            textColor=SOFT, spaceAfter=16,
        ),
        "intro": ParagraphStyle(
            "intro", parent=base["Normal"], fontSize=10, leading=15,
            textColor=SOFT, spaceAfter=16, alignment=TA_JUSTIFY,
        ),
        "qheading": ParagraphStyle(
            "qheading", parent=base["Heading2"], fontSize=11.5, leading=15,
            textColor=INK, spaceBefore=14, spaceAfter=6,
        ),
        "label": ParagraphStyle(
            "label", parent=base["Normal"], fontSize=7.8, leading=11,
            textColor=ACCENT, spaceBefore=8, spaceAfter=3,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontSize=9.7, leading=14.5,
            textColor=INK, alignment=TA_JUSTIFY, spaceAfter=6,
        ),
        "answer": ParagraphStyle(
            "answer", parent=base["Normal"], fontName="Courier-Bold",
            fontSize=9.2, leading=13, textColor=INK,
        ),
        "code": ParagraphStyle(
            "code", parent=base["Normal"], fontName="Courier",
            fontSize=8.1, leading=11.2, textColor=INK,
        ),
        "caveat": ParagraphStyle(
            "caveat", parent=base["Normal"], fontSize=9, leading=13.2,
            textColor=colors.HexColor("#7c3a06"), alignment=TA_JUSTIFY,
        ),
        # Sized so the two required paragraphs sit on a single page rather than
        # spilling a few lines onto a second one. Re-check this if the essay
        # grows: it was retuned once when the GRE evidence was added to it.
        "essay": ParagraphStyle(
            "essay", parent=base["Normal"], fontSize=9.6, leading=14.1,
            textColor=INK, alignment=TA_JUSTIFY, spaceAfter=10,
        ),
    }


def _escape(text: str) -> str:
    """Escape the three characters reportlab's mini-markup treats specially."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _boxed(flowable: Any, background: Any, rule: Any) -> Table:
    """Wrap a flowable in a single-cell table, for a tinted background."""
    table = Table([[flowable]], colWidths=[6.5 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("BOX", (0, 0), (-1, -1), 0.6, rule),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def _code_block(sql: str, style: ParagraphStyle) -> Table:
    """Render SQL preserving its indentation and line breaks."""
    lines = []
    for raw_line in sql.splitlines():
        # &nbsp; because reportlab collapses runs of ordinary spaces.
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        lines.append("&nbsp;" * indent + _escape(raw_line.strip()))
    return _boxed(Paragraph("<br/>".join(lines), style), CODE_BG, RULE)


def _result_table(table: Dict[str, Any]) -> Table:
    """Render a result table."""
    header = [Paragraph("<b>{0}</b>".format(_escape(str(c))), _small()) for c in table["columns"]]
    rows = [header]
    for row in table["rows"]:
        rows.append([Paragraph(_escape(str(cell)), _small()) for cell in row])

    widths = [2.35 * inch] + [
        (6.5 - 2.35) / max(len(table["columns"]) - 1, 1) * inch
    ] * (len(table["columns"]) - 1)

    rendered = Table(rows, colWidths=widths, repeatRows=1)
    rendered.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, SOFT),
                ("LINEBELOW", (0, 1), (-1, -2), 0.3, RULE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return rendered


_SMALL: Optional[ParagraphStyle] = None


def _small() -> ParagraphStyle:
    global _SMALL
    if _SMALL is None:
        _SMALL = ParagraphStyle(
            "small", parent=getSampleStyleSheet()["Normal"],
            fontSize=8.3, leading=11, textColor=INK,
        )
    return _SMALL


def _footer(canvas: Any, doc: Any) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(SOFT)
    canvas.drawString(1 * inch, 0.6 * inch, "{0} ({1})".format(AUTHOR, JHED))
    canvas.drawRightString(7.5 * inch, 0.6 * inch, "Page {0}".format(doc.page))
    canvas.restoreState()


def _gre_diagnosis() -> dict:
    """Live figures for the combined-total diagnosis quoted in limitations.pdf.

    Queried rather than written down, so a Pull Data that changes the data
    cannot leave the essay asserting a number that is no longer true.
    """
    import psycopg

    with psycopg.connect(**db_config.connect_kwargs()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) FILTER (WHERE gre IS NOT NULL
                                          AND (gre < 130 OR gre > 170)),
                       COUNT(*) FILTER (WHERE gre BETWEEN 260 AND 340),
                       COUNT(*) FILTER (WHERE gre BETWEEN 260 AND 340
                                          AND gre_v IS NOT NULL),
                       ROUND(AVG(gre - gre_v) FILTER (
                                 WHERE gre BETWEEN 260 AND 340
                                   AND gre_v IS NOT NULL)::numeric, 2)
                FROM applicants
                """
            )
            impossible, in_total, with_verbal, implied = cursor.fetchone()

    return {
        "in_total": query_data.fmt_count(in_total),
        # Not "share" -- the paragraph already uses that name for the share of
        # reported values that are impossible, which is a different fraction.
        "total_share": (
            query_data.fmt_pct(100.0 * in_total / impossible) if impossible else "n/a"
        ),
        "with_verbal": query_data.fmt_count(with_verbal),
        "implied": query_data.fmt_avg(implied),
    }


def _total_rows() -> str:
    """How many rows the table holds right now, for the opening paragraph."""
    import psycopg

    with psycopg.connect(**db_config.connect_kwargs()) as connection:
        return query_data.fmt_count(load_data.row_count(connection))


def build_query_results(results: Sequence[query_data.QuestionResult], path: Path) -> None:
    """Write query_results.pdf from a live run of the analysis."""
    styles = _styles()
    total = _total_rows()
    story: List[Any] = [
        Paragraph("Grad Cafe SQL Analysis", styles["title"]),
        Paragraph(
            "{author} ({jhed}) &middot; {course} &middot; Module 3".format(
                author=AUTHOR, jhed=JHED, course=COURSE
            ),
            styles["byline"],
        ),
        Paragraph(
            "Eleven questions answered against a PostgreSQL table of {total} Grad "
            "Cafe admissions results. Every query below was executed through "
            "psycopg by <font name='Courier'>query_data.py</font>, and this "
            "document is generated from that run &mdash; the result printed "
            "beside each query is the one that query produced, not a figure "
            "copied across by hand. Counts are whole numbers; percentages and "
            "averages are given to two decimal places.".format(total=total),
            styles["intro"],
        ),
    ]

    for result in results:
        heading = "Question {0}".format(result.number)
        if result.original:
            heading += " &mdash; my own question"
        answer = "<br/>".join(_escape(line) for line in result.answer_lines)

        # Each group below is kept whole, but the groups may break from one
        # another. Holding a whole question together would push a ten-row table
        # plus a twenty-line query onto the next page and leave half of this one
        # empty; letting every flowable break independently strands a section
        # label at the foot of a page with its content overleaf. Grouping is the
        # middle course: a label never separates from what it labels.
        story.append(
            KeepTogether(
                [
                    Paragraph(heading, styles["qheading"]),
                    Paragraph("<b>{0}</b>".format(_escape(result.question)), styles["body"]),
                    Paragraph("RESULT", styles["label"]),
                    _boxed(Paragraph(answer, styles["answer"]), CODE_BG, RULE),
                ]
            )
        )

        if result.table:
            story.append(Spacer(1, 8))
            story.append(_result_table(result.table))

        if result.supporting:
            story.append(
                KeepTogether(
                    [Paragraph("SUPPORTING ANALYSIS", styles["label"])]
                    + [
                        Paragraph("&bull;&nbsp; " + _escape(entry), styles["body"])
                        for entry in result.supporting
                    ]
                )
            )

        story.append(
            KeepTogether(
                [
                    Paragraph("SQL", styles["label"]),
                    _code_block(result.sql, styles["code"]),
                ]
            )
        )

        story.append(
            KeepTogether(
                [
                    Paragraph("WHAT IT DOES", styles["label"]),
                    Paragraph(_escape(result.explanation), styles["body"]),
                ]
            )
        )

        if result.caveat:
            story.append(
                KeepTogether(
                    [
                        Paragraph("READ BEFORE QUOTING THIS NUMBER", styles["label"]),
                        _boxed(
                            Paragraph(_escape(result.caveat), styles["caveat"]),
                            CAVEAT_BG,
                            CAVEAT_RULE,
                        ),
                    ]
                )
            )

    document = SimpleDocTemplate(
        str(path), pagesize=LETTER,
        leftMargin=1 * inch, rightMargin=1 * inch,
        topMargin=0.9 * inch, bottomMargin=0.9 * inch,
        title="Grad Cafe SQL Analysis", author=AUTHOR,
    )
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)


def build_limitations(results: Sequence[query_data.QuestionResult], path: Path) -> None:
    """Write limitations.pdf, quoting figures from the same live run."""
    styles = _styles()
    by_number = {result.number: result for result in results}

    # Pull the figures out of Question 3's scale-validity table so the essay
    # cannot cite stale numbers: row order is GPA, GRE Q, GRE V, GRE AW.
    metrics = {row[0].split(" (")[0]: row for row in by_number[3].table["rows"]}
    gre_q = metrics["GRE Quantitative"]
    gre_aw = metrics["GRE Analytical Writing"]

    paragraphs = [
        (
            "Grad Cafe is not a sample of graduate applicants; it is a sample of "
            "graduate applicants who chose to post. Nothing about the site "
            "recruits participants, verifies them, or follows up with the ones "
            "who never returned, so every figure in this analysis describes the "
            "population of people who decided their outcome was worth typing into "
            "a web form. That population is assembled by self-selection at two "
            "separate stages, and both push in the same direction. An applicant "
            "must first decide to post at all &mdash; a decision that anecdotally "
            "correlates with having news worth sharing, whether triumphant or "
            "crushing, and with belonging to the online communities where the "
            "site circulates, which skews heavily toward computer science, "
            "engineering, the sciences and North American research universities. "
            "They must then decide, field by field, how much to disclose. The "
            "second decision is visible directly in my own data: {gpa_reported} "
            "entries report a GPA but only {gre_reported} report any GRE "
            "Quantitative score, so the GRE averages in Question 3 rest on under "
            "eight per cent of the table. Missingness of that magnitude is not "
            "noise that averages out. If applicants who scored poorly are likelier "
            "to leave the box empty &mdash; which is the ordinary human "
            "expectation, and which nothing in the data can rule out &mdash; then "
            "the reported mean is an estimate of the mean among people willing to "
            "publish their score, and is biased upward as an estimate of anything "
            "wider. The same logic applies to the acceptance rate in Question 5: "
            "an applicant who is rejected everywhere has less reason to come back "
            "and update a profile than one with an offer to celebrate, so a "
            "{accept_pct} acceptance rate cannot be read as the probability that a "
            "given application succeeds."
        ).format(
            gpa_reported=metrics["GPA"][1],
            gre_reported=gre_q[1],
            accept_pct=by_number[5].answer_lines[0].split(": ")[-1],
        ),
        (
            "The second limitation is that the data is not merely incomplete but "
            "partly wrong, in ways arithmetic will happily propagate. Grad Cafe "
            "validates nothing an applicant types, and the supporting analysis "
            "under Question 3 counts the consequences: "
            "{out_of_range} of the {reported} reported GRE "
            "Quantitative values &mdash; {share} of them &mdash; are impossible on "
            "a scale that runs 130 to 170. They are not random noise. The GRE "
            "reports Verbal and Quantitative on 130-170 each and a <i>combined</i> "
            "total on 260-340, and {in_total} of these impossible values &mdash; "
            "{total_share} of them &mdash; fall in exactly that second band. That reading "
            "survives a second test: {with_verbal} of them also report a verbal "
            "score separately, and subtracting it leaves a mean of {implied}, back "
            "inside the legal range and within a point of the average of the "
            "values that were never out of range at all. A row reading gre = 323 "
            "beside gre_v = 161 is not a typo; it is an applicant answering a "
            "differently-worded question than the one the field label implies. "
            "The effect on the headline number is not marginal. Question 3 "
            "reports an average GRE Quantitative of "
            "{avg_all}, which no human being has ever scored; restricted to values "
            "the scale permits, the same column averages {avg_in_range}. "
            "Analytical Writing behaves the same way, inflated from {aw_in_range} "
            "to {avg_aw} by placeholder entries of 99.99 on a six-point scale. "
            "This is the distinction the assignment asks me to draw, and it is "
            "sharper here than I expected: the database mathematically contains an "
            "average of {avg_all}, and that number is correctly computed, "
            "correctly rounded, and reproducible by anyone who runs the query "
            "&mdash; and it is still not a fact about GRE scores. It is a fact "
            "about a text box. The practical lesson is that a query can only be as "
            "meaningful as the agreement between what a field is called and what "
            "people put in it, and that on anonymous self-submitted data that "
            "agreement has to be tested rather than assumed. I have therefore "
            "reported the literal answers the questions ask for and attached the "
            "measurement of their distortion beside them, rather than quietly "
            "substituting cleaned figures and presenting them as though the "
            "underlying data were sound."
        ).format(
            out_of_range=gre_q[2],
            reported=gre_q[1],
            **_gre_diagnosis(),
            share="{0:.0f} per cent".format(
                100 * int(gre_q[2].replace(",", "")) / int(gre_q[1].replace(",", ""))
            ),
            avg_all=gre_q[3],
            avg_in_range=gre_q[4],
            avg_aw=gre_aw[3],
            aw_in_range=gre_aw[4],
        ),
    ]

    story: List[Any] = [
        Paragraph("What This Data Can and Cannot Tell Us", styles["title"]),
        Paragraph(
            "{author} ({jhed}) &middot; {course} &middot; Module 3".format(
                author=AUTHOR, jhed=JHED, course=COURSE
            ),
            styles["byline"],
        ),
    ]
    for text in paragraphs:
        story.append(Paragraph(text, styles["essay"]))

    document = SimpleDocTemplate(
        str(path), pagesize=LETTER,
        leftMargin=0.92 * inch, rightMargin=0.92 * inch,
        topMargin=0.8 * inch, bottomMargin=0.75 * inch,
        title="What This Data Can and Cannot Tell Us", author=AUTHOR,
    )
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)


def main() -> int:
    try:
        results = query_data.answer_all()
    except Exception as exc:
        print(
            "Could not read the database: {0}\n"
            "Start PostgreSQL and run load_data.py first.".format(exc),
            file=sys.stderr,
        )
        return 1

    query_pdf = PROJECT_ROOT / "query_results.pdf"
    limitations_pdf = PROJECT_ROOT / "limitations.pdf"

    build_query_results(results, query_pdf)
    print("wrote {0}".format(query_pdf.name))

    build_limitations(results, limitations_pdf)
    print("wrote {0}".format(limitations_pdf.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
