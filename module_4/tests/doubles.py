"""Test doubles and the sample data they serve.

Nothing here touches the network, the local LLM or the clock.  Each double
stands in for one injected collaborator of the application:

======================  ========================================================
:class:`FakeScraper`    ``scrape.GradCafeScraper`` -- returns rows from a list
:func:`fake_standardizer`  ``llm_hosting/app.py`` -- adds the two LLM columns
:class:`RecordingLoader`   ``load_data`` -- records what it was asked to write
:class:`FailingLoader`     a loader that raises, for the error-path tests
:class:`FakePullRunner`    what a click on Pull Data does
======================  ========================================================

The sample data is raw scraped rows, in the shape :mod:`scrape` produces, not
cleaned records.  That is deliberate: a pull driven by :class:`FakeScraper` then
runs the *real* cleaner and the *real* loader, so an end-to-end test exercises
everything except the HTTP request itself.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Callable, Dict, List, Optional


# ----------------------------------------------------------------------
# Sample data
# ----------------------------------------------------------------------
def _row(
    entry_id: int,
    program: str,
    university: str,
    degree: str,
    status: str,
    term: str = "Fall 2026",
    applicant_type: Optional[str] = "American",
    gpa: Optional[str] = None,
    gre: Optional[str] = None,
    gre_v: Optional[str] = None,
    gre_aw: Optional[str] = None,
    comments: Optional[str] = None,
    date_added: str = "Sep 11, 2026",
) -> Dict[str, Any]:
    """One raw scraped row, in the shape :func:`scrape.GradCafeScraper` stores."""
    return {
        "entry_id": entry_id,
        "url": "https://www.thegradcafe.com/result/{0}".format(entry_id),
        "raw_program": program,
        "raw_university": university,
        "raw_degree": degree,
        "raw_date_added": date_added,
        "raw_status": status,
        "raw_decision_date": None,
        "raw_term": term,
        "raw_applicant_type": applicant_type,
        "raw_gpa": gpa,
        "raw_gre": gre,
        "raw_gre_v": gre_v,
        "raw_gre_aw": gre_aw,
        "raw_comments": comments,
    }


#: Twelve raw rows, chosen so that all eleven analyses have something to say:
#: both nationality cohorts, both degree levels, four decision states, two
#: terms, the four universities Question 8 names, a Johns Hopkins master's for
#: Question 7, and -- for Question 3's validity checks -- one GRE score in the
#: combined 260-340 band and one GPA on a 10-point scale.
SAMPLE_RAW_ROWS: List[Dict[str, Any]] = [
    _row(
        900001, "Computer Science", "Massachusetts Institute of Technology (MIT)",
        "PhD", "Accepted on Mar 03", gpa="3.90", gre="168", gre_v="162",
        gre_aw="4.50", comments="Funded offer, very happy.",
    ),
    _row(
        900002, "Computer Science", "Stanford University", "PhD",
        "Accepted on Feb 20", applicant_type="International",
        # 300 is impossible as a Quantitative score but sits inside the official
        # combined Verbal+Quantitative range, which is what Question 3 diagnoses.
        gpa="3.80", gre="300", gre_v="160", gre_aw="4.00",
    ),
    _row(
        900003, "Computer Science", "Carnegie Mellon University (CMU)", "PhD",
        "Rejected on Mar 15", applicant_type="International",
        gre="165", gre_v="158", gre_aw="99.99",  # placeholder AW, scale stops at 6
    ),
    _row(
        900004, "Computer Science", "Johns Hopkins University", "Masters",
        "Accepted on Feb 02", gpa="3.70", gre="164", gre_v="155", gre_aw="4.00",
    ),
    _row(
        900005, "Computer Science", "Johns Hopkins University (JHU)", "MS",
        "Rejected on Mar 01", gpa="3.40",
    ),
    _row(
        900006, "Computer Science", "Georgetown University", "PhD",
        "Wait listed on Mar 22", applicant_type="International", gpa="3.60",
    ),
    _row(
        900007, "Mathematics", "Yale University", "PhD", "Accepted on Feb 14",
        term="Fall 2025", gpa="3.95", gre="167", gre_v="164", gre_aw="5.00",
        date_added="Sep 14, 2025",
    ),
    _row(
        900008, "Data Science", "New York University", "Masters",
        "Accepted on Apr 01", applicant_type="Other", gpa="3.55",
    ),
    _row(
        # No nationality reported: in Question 3's averages, out of Question 2's
        # denominator.
        900009, "Information Systems", "Northeastern University", "Masters",
        "Rejected on Mar 30", applicant_type=None, gpa="3.20",
    ),
    _row(
        900010, "Computer Science", "Massachusetts Institute of Technology (MIT)",
        "PhD", "Accepted on Mar 07", gpa="4.00", gre="170", gre_v="166",
        gre_aw="5.50",
    ),
    _row(
        # A 10-point CGPA typed into a box with no scale -- impossible on 4.0.
        900011, "Computer Science", "University of Washington", "Masters",
        "Rejected on Mar 18", applicant_type="International", gpa="8.25",
    ),
    _row(
        900012, "Physics", "Princeton University", "PhD", "Interview on Feb 28",
        gpa="3.85", gre="166", gre_v="159", gre_aw="4.50",
    ),
]

#: How many of the sample rows are for Fall 2026 -- everything but the Yale one.
SAMPLE_FALL_2026 = 11

#: A second batch that overlaps the first: two rows already seen (one of them
#: with an edited decision) and two genuinely new ones.  What the "pull twice"
#: tests use to check that overlap refreshes rather than duplicates.
SAMPLE_OVERLAPPING_ROWS: List[Dict[str, Any]] = [
    # Already stored, unchanged.
    copy.deepcopy(SAMPLE_RAW_ROWS[0]),
    # Already stored, but the applicant has since edited the decision.
    dict(copy.deepcopy(SAMPLE_RAW_ROWS[5]), raw_status="Accepted on Apr 04"),
    _row(
        900013, "Computer Science", "Stanford University", "PhD",
        "Rejected on Mar 09", applicant_type="International", gpa="3.75",
    ),
    _row(
        900014, "Computer Science", "Massachusetts Institute of Technology (MIT)",
        "Masters", "Accepted on Mar 11", gpa="3.65",
    ),
]

#: Canonical spellings the fake standardizer applies, standing in for what the
#: TinyLlama model produces.  Only the shape matters to the tests: a non-null
#: ``llm_generated_*`` pair on every row.
CANONICAL_UNIVERSITIES = {
    "massachusetts institute of technology (mit)":
        "Massachusetts Institute of Technology (MIT)",
    "johns hopkins university (jhu)": "Johns Hopkins University",
    "carnegie mellon university (cmu)": "Carnegie Mellon University",
}


def fake_standardizer(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add the two LLM columns without going near a language model.

    :param records: cleaned records, each carrying ``"<program>, <university>"``
        in ``program``.
    :returns: a **new** list with ``llm-generated-program`` and
        ``llm-generated-university`` filled in. A new list rather than the input
        mutated, because :func:`pull_data.run_pipeline` reads identity to tell a
        standardizer that ran from one that declined to.
    """
    standardized = []
    for record in records:
        combined = record.get("program") or ""
        program, _, university = combined.partition(",")
        university = university.strip()
        standardized.append(
            dict(
                record,
                **{
                    "llm-generated-program": program.strip() or None,
                    "llm-generated-university": CANONICAL_UNIVERSITIES.get(
                        university.lower(), university
                    )
                    or None,
                }
            )
        )
    return standardized


# ----------------------------------------------------------------------
# Doubles
# ----------------------------------------------------------------------
class FakeScraper:
    """A scraper stage that serves rows from a list instead of the network.

    :param batches: one list of raw rows per call. The last is repeated once
        exhausted, so a test that pulls twice without saying what the second
        pull should see gets the same rows again -- the realistic case, and the
        one the uniqueness policy has to survive.

    Records how many times it was called, so a test can assert that a refused
    pull did not reach the scraper.
    """

    def __init__(self, *batches: List[Dict[str, Any]]) -> None:
        self.batches = [list(batch) for batch in batches] or [[]]
        self.calls = 0

    def __call__(self) -> List[Dict[str, Any]]:
        batch = self.batches[min(self.calls, len(self.batches) - 1)]
        self.calls += 1
        return copy.deepcopy(batch)


class RecordingLoader:
    """A loader stage that remembers what it was given and writes nothing.

    Used where the point of the test is that the pull *reached* the loader with
    the scraper's rows, not what PostgreSQL did with them.
    """

    def __init__(self, summary: Optional[Dict[str, Any]] = None) -> None:
        self.calls = 0
        self.records: List[Dict[str, Any]] = []
        self.summary = summary or {
            "read": 0,
            "skipped": 0,
            "written": 0,
            "inserted": 0,
            "updated": 0,
            "total": 0,
        }

    def __call__(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        self.calls += 1
        self.records = list(records)
        summary = dict(self.summary)
        summary.setdefault("read", len(self.records))
        summary["written"] = len(self.records)
        summary["inserted"] = summary.get("inserted") or len(self.records)
        summary["total"] = summary.get("total") or len(self.records)
        return summary


class LoaderFailure(RuntimeError):
    """What :class:`FailingLoader` raises.  Its own type so a test can name it."""


class FailingLoader:
    """A loader stage that raises before writing anything.

    The error-path double: the pull must answer non-200 and PostgreSQL must be
    exactly as it was, because the real loader writes in a single transaction
    and this one never gets as far as opening it.
    """

    def __init__(self, message: str = "the database refused the write") -> None:
        self.message = message
        self.calls = 0

    def __call__(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        self.calls += 1
        raise LoaderFailure(self.message)


class FakePullRunner:
    """What a click on Pull Data does, with the scrape replaced.

    :param pipeline: the callable to run, usually
        :func:`pull_data.run_pipeline` bound to fakes.

    Runs synchronously, so by the time ``POST /pull-data`` has answered, the
    rows are in the database and the test can look.  Production spawns a
    detached process instead; the route cannot tell the difference, which is the
    point of it being a parameter.
    """

    def __init__(self, pipeline: Callable[[], Dict[str, Any]]) -> None:
        self.pipeline = pipeline
        self.calls = 0

    def __call__(self) -> Dict[str, Any]:
        self.calls += 1
        return self.pipeline()


class CountingProvider:
    """An analysis provider that returns canned answers and counts its calls.

    :param results: what to return, or a callable producing it.

    Lets the page tests render without a database, and lets the busy-state tests
    assert that a refused update recomputed nothing -- by checking a number,
    rather than by waiting to see whether anything happened.
    """

    def __init__(self, results: Any) -> None:
        self._results = results
        self.calls = 0

    def __call__(self) -> List[Any]:
        self.calls += 1
        if callable(self._results):
            return self._results()
        return list(self._results)


PERCENT_PATTERN = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?\s*%")
"""Every percentage-looking token, however many decimals it has.

The formatting tests pull these out of the rendered page and then require each
one to match :data:`TWO_DECIMAL_PERCENT`.  Matching loosely first is what makes
the test able to fail: a pattern that only matched two-decimal percentages would
pass happily on a page full of one-decimal ones.
"""

TWO_DECIMAL_PERCENT = re.compile(r"[-+]?\d[\d,]*\.\d{2}%")
"""A percentage with exactly two decimal places, e.g. ``39.28%``."""
