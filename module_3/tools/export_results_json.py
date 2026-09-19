"""Dump a live run of the analysis to JSON, for the Word build.

``build_query_results_docx.js`` turns this into ``query_results.docx``, which is
then reviewed in Word and exported to ``query_results.pdf`` -- the same route
``limitations.pdf`` takes.  Splitting it in two keeps the querying in Python,
where the queries already live, and the document building in the library that
writes Word files properly.

    python tools/export_results_json.py            # to stdout
    python tools/export_results_json.py --out r.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import db_config  # noqa: E402
import load_data  # noqa: E402
import query_data  # noqa: E402


def collect() -> dict:
    """Answer every question and shape it for the document builder."""
    results = query_data.answer_all()

    import psycopg

    with psycopg.connect(**db_config.connect_kwargs()) as connection:
        total_rows = load_data.row_count(connection)

    return {
        "author": "Nipul Jayasekera",
        "jhed": "njayase1",
        "course": "EN.605.256 Modern Software Concepts in Python",
        "module": "Module 3",
        "total_rows": query_data.fmt_count(total_rows),
        "questions": [
            {
                "number": result.number,
                "original": result.original,
                "question": result.question,
                "answer_lines": result.answer_lines,
                "sql": result.sql,
                "explanation": result.explanation,
                "table": result.table,
                "supporting": result.supporting,
                "caveat": result.caveat,
            }
            for result in results
        ],
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dump the analysis results as JSON for the Word build."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write here instead of stdout",
    )
    args = parser.parse_args(argv)

    try:
        payload = collect()
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        print(
            "Could not read the database: {0}\n"
            "Start PostgreSQL and run load_data.py first.".format(exc),
            file=sys.stderr,
        )
        return 1

    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print("wrote {0}".format(args.out), file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
