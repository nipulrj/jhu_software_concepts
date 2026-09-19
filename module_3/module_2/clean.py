"""Normalise scraped Grad Cafe rows into the structured ``applicant_data.json``.

``scrape.py`` deliberately stores text exactly as the site rendered it.  This
module turns that raw text into consistent, typed fields:

* dates become ISO ``YYYY-MM-DD`` strings,
* GPA and GRE badges become numbers,
* the decision badge splits into a status and a decision date,
* every missing value is represented the same way (``None``).

The original scraped strings are kept under the ``raw`` key of each record so a
grader can trace any cleaned value back to what the page actually said.

The ``program`` field is written as ``"<program>, <university>"`` to match the
input format expected by ``llm_hosting/app.py``, which appends its own
``llm-generated-program`` and ``llm-generated-university`` fields downstream.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from scrape import RAW_DATA_PATH, load_data, save_data

MODULE_DIR = Path(__file__).resolve().parent
CLEANED_DATA_PATH = MODULE_DIR / "applicant_data.json"

# "Sep 11, 2026" as rendered in the "Added on" column.
_DATE_ADDED_RE = re.compile(r"^([A-Za-z]{3,9})\s+(\d{1,2}),\s*(\d{4})$")
# "Accepted on Sep 09" / "Wait listed on Mar 25" / bare "Accepted".
_STATUS_RE = re.compile(r"^(?P<status>.+?)(?:\s+on\s+(?P<month>[A-Za-z]{3,9})\s+(?P<day>\d{1,2}))?$")
_TERM_RE = re.compile(r"^(?P<season>Fall|Spring|Summer|Winter)\s+(?P<year>\d{4})$", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")

# Decision labels the site uses, normalised to canonical spellings.
_STATUS_CANON = {
    "accepted": "Accepted",
    "rejected": "Rejected",
    "wait listed": "Wait listed",
    "waitlisted": "Wait listed",
    "interview": "Interview",
    "other": "Other",
}

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _strip_markup(text: Any) -> str:
    """Remove any stray tags and decode HTML entities, then collapse whitespace."""
    if text is None:
        return ""
    unescaped = html.unescape(str(text))
    # Entities can survive one pass when a page double-escapes them.
    unescaped = html.unescape(unescaped)
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", unescaped)).strip()


def _clean_text(text: Any) -> Optional[str]:
    """Return cleaned text, or ``None`` when nothing is left."""
    cleaned = _strip_markup(text)
    return cleaned or None


def _parse_month(name: str) -> Optional[int]:
    """Map a month name or abbreviation to its number."""
    return _MONTHS.get(name.strip().lower()[:3])


def _parse_date_added(raw: Any) -> Optional[dt.date]:
    """Parse the "Added on" column, e.g. ``"Sep 11, 2026"``."""
    match = _DATE_ADDED_RE.match(_strip_markup(raw))
    if not match:
        return None
    month = _parse_month(match.group(1))
    if month is None:
        return None
    try:
        return dt.date(int(match.group(3)), month, int(match.group(2)))
    except ValueError:
        return None


def _infer_decision_date(
    month: int, day: int, date_added: Optional[dt.date]
) -> Optional[dt.date]:
    """Attach a year to a decision date, which the listing renders without one.

    The badge only says e.g. "Accepted on Sep 09".  Results are posted within
    days of the decision, so the year is taken from the date the entry was
    added; if that would put the decision in the future, the previous year is
    used instead (an entry added in early January reporting a December
    decision).  The un-inferred label is preserved in ``raw`` either way.
    """
    if date_added is None:
        return None
    for year in (date_added.year, date_added.year - 1):
        try:
            candidate = dt.date(year, month, day)
        except ValueError:
            continue  # e.g. Feb 29 in a non-leap year
        if candidate <= date_added:
            return candidate
    return None


def _parse_status(
    raw: Any, date_added: Optional[dt.date], exact_date: Any = None
) -> Dict[str, Any]:
    """Split a decision badge into a status plus an optional decision date.

    ``exact_date`` is the full notification date the scraper lifted from the
    page's embedded data payload.  When present it is authoritative; the
    year-inference path only runs if the site did not supply one.
    """
    text = _strip_markup(raw)
    result: Dict[str, Any] = {"status": None, "decision_date": None}
    if not text:
        return result

    match = _STATUS_RE.match(text)
    if not match:
        result["status"] = text
        return result

    label = match.group("status").strip()
    result["status"] = _STATUS_CANON.get(label.lower(), label)

    exact = _strip_markup(exact_date)
    if exact:
        try:
            result["decision_date"] = dt.date.fromisoformat(exact[:10]).isoformat()
            return result
        except ValueError:
            pass  # fall through to inference

    month_name, day = match.group("month"), match.group("day")
    if month_name and day:
        month = _parse_month(month_name)
        if month is not None:
            decision_date = _infer_decision_date(month, int(day), date_added)
            if decision_date is not None:
                result["decision_date"] = decision_date.isoformat()
    return result


def _parse_number(raw: Any) -> Optional[float]:
    """Parse a GPA or GRE AW badge value into a float, or ``None`` if absent."""
    text = _strip_markup(raw)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_score(raw: Any) -> Optional[int]:
    """Parse a scaled GRE section score, which is always a whole number."""
    value = _parse_number(raw)
    return int(value) if value is not None else None


def _parse_term(raw: Any) -> Dict[str, Any]:
    """Split ``"Fall 2024"`` into its season and year."""
    text = _strip_markup(raw)
    match = _TERM_RE.match(text)
    if not match:
        return {"term": text or None, "start_season": None, "start_year": None}
    return {
        "term": text,
        "start_season": match.group("season").capitalize(),
        "start_year": int(match.group("year")),
    }


def _combine_program_field(program: Optional[str], university: Optional[str]) -> str:
    """Build the ``"<program>, <university>"`` string the LLM stage consumes."""
    parts = [part for part in (program, university) if part]
    return ", ".join(parts)


def _clean_entry(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalise one scraped row into the output schema."""
    program = _clean_text(row.get("raw_program"))
    university = _clean_text(row.get("raw_university"))
    if not program and not university:
        return None  # nothing identifiable; drop rather than emit a blank record

    date_added = _parse_date_added(row.get("raw_date_added"))
    status = _parse_status(
        row.get("raw_status"), date_added, row.get("raw_decision_date")
    )
    term = _parse_term(row.get("raw_term"))

    applicant_type = _clean_text(row.get("raw_applicant_type"))
    if applicant_type:
        applicant_type = applicant_type.capitalize()

    decision_date = status["decision_date"]
    status_label = status["status"]

    return {
        # Combined field consumed by llm_hosting/app.py.
        "program": _combine_program_field(program, university),
        "program_name": program,
        "university": university,
        "degree": _clean_text(row.get("raw_degree")),
        "comments": _clean_text(row.get("raw_comments")),
        "date_added": date_added.isoformat() if date_added else None,
        "url": _clean_text(row.get("url")),
        "applicant_status": status_label,
        "decision_date": decision_date,
        # Per-outcome dates, so downstream analysis can filter without re-parsing.
        "acceptance_date": decision_date if status_label == "Accepted" else None,
        "rejection_date": decision_date if status_label == "Rejected" else None,
        "waitlist_date": decision_date if status_label == "Wait listed" else None,
        "interview_date": decision_date if status_label == "Interview" else None,
        "term": term["term"],
        "start_season": term["start_season"],
        "start_year": term["start_year"],
        "applicant_type": applicant_type,
        "gpa": _parse_number(row.get("raw_gpa")),
        "gre_quant": _parse_score(row.get("raw_gre")),
        "gre_verbal": _parse_score(row.get("raw_gre_v")),
        "gre_aw": _parse_number(row.get("raw_gre_aw")),
        "entry_id": row.get("entry_id"),
        # Verbatim scraped text, kept for traceability and reproducibility.
        "raw": {
            key: row.get(key)
            for key in (
                "raw_program",
                "raw_university",
                "raw_degree",
                "raw_date_added",
                "raw_status",
                "raw_decision_date",
                "raw_term",
                "raw_applicant_type",
                "raw_gpa",
                "raw_gre",
                "raw_gre_v",
                "raw_gre_aw",
                "raw_comments",
            )
        },
    }


def clean_data(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Clean every scraped row, dropping duplicates and unusable records."""
    cleaned: List[Dict[str, Any]] = []
    seen_ids: set = set()

    for row in rows:
        entry = _clean_entry(row)
        if entry is None:
            continue
        entry_id = entry.get("entry_id")
        if entry_id is not None:
            if entry_id in seen_ids:
                continue
            seen_ids.add(entry_id)
        cleaned.append(entry)

    return cleaned


def summarise(rows: List[Dict[str, Any]]) -> str:
    """Build a short field-coverage report for the console."""
    if not rows:
        return "no records"

    total = len(rows)
    lines = [f"{total:,} cleaned records", "", f"{'field':<20} populated"]
    for field in (
        "program_name", "university", "degree", "comments", "date_added", "url",
        "applicant_status", "decision_date", "term", "applicant_type",
        "gpa", "gre_quant", "gre_verbal", "gre_aw",
    ):
        populated = sum(1 for row in rows if row.get(field) is not None)
        lines.append(f"{field:<20} {populated:>7,} ({populated / total:5.1%})")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Clean scraped Grad Cafe rows into applicant_data.json."
    )
    parser.add_argument(
        "--in",
        dest="input_path",
        type=Path,
        default=RAW_DATA_PATH,
        help="raw scraped JSON produced by scrape.py (default: %(default)s)",
    )
    parser.add_argument(
        "--out",
        dest="output_path",
        type=Path,
        default=CLEANED_DATA_PATH,
        help="where to write the cleaned JSON (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    raw_rows = load_data(args.input_path)
    if not raw_rows:
        print(f"No rows found in {args.input_path}. Run scrape.py first.", file=sys.stderr)
        return 1

    cleaned_rows = clean_data(raw_rows)
    save_data(cleaned_rows, args.output_path)

    print(f"Read {len(raw_rows):,} raw rows from {args.input_path}", file=sys.stderr)
    print(summarise(cleaned_rows), file=sys.stderr)
    print(f"Wrote {len(cleaned_rows):,} records -> {args.output_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
