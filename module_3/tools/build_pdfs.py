"""Build ``query_results.pdf`` from a live run of the analysis.

The result printed beside each query is the one that query produced, rather than
a figure copied across by hand, so re-running this after a Pull Data keeps the
document and the database in step.

``limitations.pdf`` is deliberately NOT built here.  It is written prose, drafted
in Word and exported, so generating it would overwrite the authored version.  Its
figures are quoted from a run of ``query_data.py``; if the data changes enough to
move them, the essay needs re-reading rather than re-rendering.

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

    build_query_results(results, query_pdf)
    print("wrote {0}".format(query_pdf.name))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
