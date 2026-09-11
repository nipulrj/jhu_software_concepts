"""Grow ``llm_hosting``'s canonical lists from the names Grad Cafe itself uses.

The bundled ``canon_universities.txt`` / ``canon_programs.txt`` cover a few
hundred well-known names.  Anything outside them either survives unstandardized
or - worse, before the guard added to ``_best_match`` - got fuzzy-matched onto a
different institution entirely.

Grad Cafe stores the school and programme on each result as foreign keys
(``school_id`` / ``program_id``), so the names rendered in the listing come from
the site's own controlled vocabulary rather than free text.  That makes the
scraped data a sound source of additional canonical entries: this script counts
the distinct names, keeps the ones that recur (so a stray one-off cannot become
canonical), and appends those missing from the lists.

Existing entries are never reordered or removed - additions go in a clearly
marked block at the end of each file, so the diff against the provided lists
stays easy to review.

    python expand_canon_lists.py --min-count 3
    python expand_canon_lists.py --dry-run
"""

from __future__ import annotations

import argparse
import collections
import sys
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from clean import CLEANED_DATA_PATH
from scrape import load_data

MODULE_DIR = Path(__file__).resolve().parent
CANON_DIR = MODULE_DIR / "llm_hosting"
CANON_UNIVERSITIES = CANON_DIR / "canon_universities.txt"
CANON_PROGRAMS = CANON_DIR / "canon_programs.txt"

ADDITION_HEADER = (
    "# --- appended from scraped Grad Cafe data (expand_canon_lists.py) ---"
)

DEFAULT_MIN_COUNT = 3


def _fold(name: str) -> str:
    """Case- and accent-insensitive key for comparing two names."""
    folded = unicodedata.normalize("NFKD", (name or "").strip().lower())
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def _read_list(path: Path) -> List[str]:
    """Read a canonical list, dropping blanks and comment lines."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [
            line.strip()
            for line in handle
            if line.strip() and not line.lstrip().startswith("#")
        ]


def _looks_like_a_name(name: str) -> bool:
    """Filter out placeholders and fragments that should never become canonical."""
    if len(name) < 3 or len(name) > 120:
        return False
    if not any(ch.isalpha() for ch in name):
        return False
    # Entries the site stores entirely in lower case ("yale", "english",
    # "carnegie") are sloppy source data, not canonical spellings. Adding one
    # would make it the canonical form and hand that lower-case text back as the
    # standardized answer. The properly-capitalised name is normally already in
    # the list, and the folded lookup in app.py maps these onto it anyway.
    if name == name.lower():
        return False
    # Obvious placeholders applicants type when they do not want to say.
    return _fold(name) not in {"unknown", "n/a", "na", "none", "other", "test"}


def _load_matcher():
    """Borrow ``app._best_match`` so this script and the standardizer agree.

    Importing ``app`` pulls in llama_cpp (an import only - no model is loaded).
    If that stack is not installed, fall back to adding every missing name, which
    is the safe direction: a redundant canonical entry is harmless, a missing one
    is what lets the fuzzy matcher wander onto a different institution.
    """
    sys.path.insert(0, str(CANON_DIR))
    try:
        from app import _best_match  # type: ignore

        return _best_match
    except Exception as exc:  # llama_cpp missing, model stack unavailable, ...
        print(
            f"note: could not import the standardizer's matcher ({exc.__class__.__name__}); "
            "adding every missing name",
            file=sys.stderr,
        )
        return lambda name, candidates, cutoff=0.86: None


def _candidates(
    values: Iterable[str], existing: List[str], min_count: int
) -> List[Tuple[str, int]]:
    """Pick recurring names the canonical list does not already cover.

    A name is skipped when the standardizer's guarded fuzzy matcher already maps
    it onto an existing entry - that is the whole point of standardizing, so
    adding it would freeze the variant in place instead of collapsing it.
    "University of Michigan - Ann Arbor" already resolves to the canonical
    "University of Michigan, Ann Arbor" and is therefore not added, while the
    bare "University of Michigan" has no safe match and is.
    """
    counts: collections.Counter = collections.Counter(
        value.strip() for value in values if value and _looks_like_a_name(value.strip())
    )
    known = {_fold(entry) for entry in existing}
    best_match = _load_matcher()

    # Where the same name appears in several casings, keep the most common one.
    best_by_key: Dict[str, Tuple[str, int]] = {}
    for name, count in counts.items():
        key = _fold(name)
        if key in known or count < min_count:
            continue
        current = best_by_key.get(key)
        if current is None or count > current[1]:
            best_by_key[key] = (name, count)

    # Drop anything the standardizer can already fold into a canonical entry.
    resolved = 0
    candidates: List[Tuple[str, int]] = []
    for name, count in best_by_key.values():
        if best_match(name, existing, 0.86) is not None:
            resolved += 1
            continue
        candidates.append((name, count))

    if resolved:
        print(
            f"    ({resolved:,} variants already fold into an existing entry; not added)",
            file=sys.stderr,
        )
    return sorted(candidates, key=lambda pair: (-pair[1], pair[0]))


def _append(path: Path, additions: List[str]) -> None:
    """Append new entries under a marked header, leaving prior content intact."""
    if not additions:
        return
    existing_text = path.read_text(encoding="utf-8") if path.exists() else ""
    separator = "" if existing_text.endswith("\n") or not existing_text else "\n"
    header = "" if ADDITION_HEADER in existing_text else f"\n{ADDITION_HEADER}\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(separator + header + "\n".join(additions) + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--in", dest="input_path", type=Path, default=CLEANED_DATA_PATH,
        help="cleaned applicant data to mine (default: %(default)s)",
    )
    parser.add_argument(
        "--min-count", type=int, default=DEFAULT_MIN_COUNT,
        help="how many times a name must appear to be added (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report what would be added without writing the files",
    )
    args = parser.parse_args(argv)

    rows = load_data(args.input_path)
    if not rows:
        print(f"No records in {args.input_path}. Run clean.py first.", file=sys.stderr)
        return 1

    targets = (
        ("universities", CANON_UNIVERSITIES, [r.get("university") or "" for r in rows]),
        ("programs", CANON_PROGRAMS, [r.get("program_name") or "" for r in rows]),
    )

    for label, path, values in targets:
        existing = _read_list(path)
        additions = _candidates(values, existing, args.min_count)
        print(
            f"{label}: {len(existing):,} canonical entries, "
            f"{len({_fold(v) for v in values if v}):,} distinct in data, "
            f"{len(additions):,} to add (seen >= {args.min_count} times)",
            file=sys.stderr,
        )
        for name, count in additions[:10]:
            print(f"    + {name}  ({count:,} rows)", file=sys.stderr)
        if len(additions) > 10:
            print(f"    ... and {len(additions) - 10:,} more", file=sys.stderr)

        if not args.dry_run:
            _append(path, [name for name, _ in additions])
            print(f"    wrote {path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
