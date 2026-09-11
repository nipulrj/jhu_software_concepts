# -*- coding: utf-8 -*-
"""Flask + tiny local LLM standardizer with incremental JSONL CLI output."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import difflib
import inspect
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, request
from huggingface_hub import hf_hub_download
from llama_cpp import Llama  # CPU-only by default if N_GPU_LAYERS=0

app = Flask(__name__)

# MODIFIED: resolve bundled files relative to this file rather than the caller's
# working directory, so the canonical lists and the model cache are found no
# matter where the script is launched from.
BASE_DIR = Path(__file__).resolve().parent
MODELS_DIR = BASE_DIR / "models"

# ---------------- Model config ----------------
MODEL_REPO = os.getenv(
    "MODEL_REPO",
    "TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF",
)
MODEL_FILE = os.getenv(
    "MODEL_FILE",
    "tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf",
)

N_THREADS = int(os.getenv("N_THREADS", str(os.cpu_count() or 2)))
N_CTX = int(os.getenv("N_CTX", "2048"))
N_GPU_LAYERS = int(os.getenv("N_GPU_LAYERS", "0"))  # 0 → CPU-only

CANON_UNIS_PATH = os.getenv("CANON_UNIS_PATH", str(BASE_DIR / "canon_universities.txt"))
CANON_PROGS_PATH = os.getenv("CANON_PROGS_PATH", str(BASE_DIR / "canon_programs.txt"))

# Precompiled, non-greedy JSON object matcher to tolerate chatter around JSON
JSON_OBJ_RE = re.compile(r"\{.*?\}", re.DOTALL)

# ---------------- Canonical lists + abbrev maps ----------------
def _read_lines(path: str) -> List[str]:
    """Read non-empty, stripped lines from a file (UTF-8)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip()]
    except FileNotFoundError:
        return []


CANON_UNIS = _read_lines(CANON_UNIS_PATH)
CANON_PROGS = _read_lines(CANON_PROGS_PATH)

ABBREV_UNI: Dict[str, str] = {
    r"(?i)^mcg(\.|ill)?$": "McGill University",
    r"(?i)^(ubc|u\.?b\.?c\.?)$": "University of British Columbia",
    r"(?i)^uoft$": "University of Toronto",
}

COMMON_UNI_FIXES: Dict[str, str] = {
    "McGiill University": "McGill University",
    "Mcgill University": "McGill University",
    # Normalize 'Of' → 'of'
    "University Of British Columbia": "University of British Columbia",
}

COMMON_PROG_FIXES: Dict[str, str] = {
    "Mathematic": "Mathematics",
    "Info Studies": "Information Studies",
}

# ---------------- Few-shot prompt ----------------
SYSTEM_PROMPT = (
    "You are a data cleaning assistant. Standardize degree program and university "
    "names.\n\n"
    "Rules:\n"
    "- Input provides a single string under key `program` that may contain both "
    "program and university.\n"
    "- Split into (program name, university name).\n"
    "- Trim extra spaces and commas.\n"
    '- Expand obvious abbreviations (e.g., "McG" -> "McGill University", '
    '"UBC" -> "University of British Columbia").\n'
    "- Use Title Case for program; use official capitalization for university "
    "names (e.g., \"University of X\").\n"
    '- Ensure correct spelling (e.g., "McGill", not "McGiill").\n'
    '- If university cannot be inferred, return "Unknown".\n\n'
    "Return JSON ONLY with keys:\n"
    "  standardized_program, standardized_university\n"
)

FEW_SHOTS: List[Tuple[Dict[str, str], Dict[str, str]]] = [
    (
        {"program": "Information Studies, McGill University"},
        {
            "standardized_program": "Information Studies",
            "standardized_university": "McGill University",
        },
    ),
    (
        {"program": "Information, McG"},
        {
            "standardized_program": "Information Studies",
            "standardized_university": "McGill University",
        },
    ),
    (
        {"program": "Mathematics, University Of British Columbia"},
        {
            "standardized_program": "Mathematics",
            "standardized_university": "University of British Columbia",
        },
    ),
]

_LLM: Llama | None = None


def _load_llm() -> Llama:
    """Download (or reuse) the GGUF file and initialize llama.cpp."""
    global _LLM
    if _LLM is not None:
        return _LLM

    # MODIFIED (compat): huggingface_hub 1.x removed `force_filename` and
    # deprecated `local_dir_use_symlinks`; passing them raises a TypeError on a
    # current install.  Only send the kwargs this version actually accepts, so
    # the same file works on both old and new hub releases.
    download_kwargs = {
        "repo_id": MODEL_REPO,
        "filename": MODEL_FILE,
        "local_dir": str(MODELS_DIR),
    }
    supported = inspect.signature(hf_hub_download).parameters
    if "local_dir_use_symlinks" in supported:
        download_kwargs["local_dir_use_symlinks"] = False
    if "force_filename" in supported:
        download_kwargs["force_filename"] = MODEL_FILE

    model_path = hf_hub_download(**download_kwargs)

    _LLM = Llama(
        model_path=model_path,
        n_ctx=N_CTX,
        n_threads=N_THREADS,
        n_gpu_layers=N_GPU_LAYERS,
        verbose=False,
    )
    return _LLM


def _split_fallback(text: str) -> Tuple[str, str]:
    """Simple, rules-first parser if the model returns non-JSON."""
    s = re.sub(r"\s+", " ", (text or "")).strip().strip(",")
    parts = [p.strip() for p in re.split(r",| at | @ ", s) if p.strip()]
    prog = parts[0] if parts else ""
    uni = parts[1] if len(parts) > 1 else ""

    # High-signal expansions
    if re.fullmatch(r"(?i)mcg(ill)?(\.)?", uni or ""):
        uni = "McGill University"
    if re.fullmatch(
        r"(?i)(ubc|u\.?b\.?c\.?|university of british columbia)",
        uni or "",
    ):
        uni = "University of British Columbia"

    # Title-case program; normalize 'Of' → 'of' for universities
    prog = prog.title()
    if uni:
        uni = re.sub(r"\bOf\b", "of", uni.title())
    else:
        uni = "Unknown"
    return prog, uni


# MODIFIED: guard the fuzzy matcher against cross-institution corruption.
#
# difflib scores on raw character overlap, so a name missing from the canonical
# list gets rewritten to whatever looks closest.  Observed on real scraped data:
# "University of Michigan" was not in canon_universities.txt (only "University
# of Michigan, Ann Arbor" was), and difflib rewrote it to "University of Milan"
# - a different school on a different continent.  Silently relabelling one real
# institution as another is far worse than leaving a name unstandardized.
#
# The guard below requires the match to keep at least one of the name's
# distinctive words, so "Michigan" can no longer become "Milan" while genuine
# fixes ("Mathematic" -> "Mathematics", "University of California (UCLA)" ->
# "University of California, Los Angeles") still pass.
_NAME_STOPWORDS = {
    "university", "universities", "college", "school", "institute", "institution",
    "polytechnic", "academy", "campus", "state", "graduate", "program", "programs",
    "the", "and", "for", "with",
}


def _significant_tokens(name: str) -> List[str]:
    """Accent-folded words of four or more characters, minus generic filler."""
    folded = unicodedata.normalize("NFKD", (name or "").lower())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return [
        token
        for token in re.findall(r"[a-z0-9]+", folded)
        if len(token) >= 4 and token not in _NAME_STOPWORDS
    ]


def _tokens_agree(left: str, right: str) -> bool:
    """True when two words are the same or share a stem (Mathematic/Mathematics)."""
    if left == right:
        return True
    return (
        len(left) >= 5
        and len(right) >= 5
        and (left.startswith(right[:5]) or right.startswith(left[:5]))
    )


def _keeps_identity(name: str, candidate: str) -> bool:
    """True when ``candidate`` still names the same thing as ``name``.

    Two ways a match can change the subject, both seen in the scraped data:

    * it drops the identity - "University of Michigan" -> "University of Milan";
    * it invents specificity the source never gave - "University of Nebraska" ->
      "University of Nebraska Omaha", or "University of Wisconsin" ->
      "University of Wisconsin-Stout".  Picking a campus for someone who named
      only the parent institution is a guess, and it is wrong whenever they
      meant a different campus.
    """
    source = _significant_tokens(name)
    if not source:
        return True  # nothing distinctive to preserve (e.g. a bare acronym)
    target = _significant_tokens(candidate)

    # Must keep at least one distinctive word of the original.
    if not any(_tokens_agree(a, b) for a in source for b in target):
        return False

    # Must not add a distinctive word the original never mentioned.
    return all(any(_tokens_agree(b, a) for a in source) for b in target)


def _best_match(name: str, candidates: List[str], cutoff: float = 0.86) -> str | None:
    """Fuzzy match via difflib, rejecting matches that change the name's identity."""
    if not name or not candidates:
        return None
    # Consider several near matches so a safe one further down can still win.
    for match in difflib.get_close_matches(name, candidates, n=5, cutoff=cutoff):
        if _keeps_identity(name, match):
            return match
    return None


# MODIFIED: look canonical entries up case- and accent-insensitively.
#
# The post-processor title-cases before checking membership, which capitalises
# connectives: "Earth and Environmental Sciences" becomes "Earth And
# Environmental Sciences" and no longer equals the canonical entry.  Exact
# matches were therefore missing for most multi-word names and falling through
# to fuzzy matching - the very step where a wrong match can change the meaning.
# Matching on a folded key restores the exact hit and returns the canonical
# spelling, so fuzzy matching is only reached by names genuinely not in the list.
def _canon_index(entries: List[str]) -> Dict[str, str]:
    """Map a folded lookup key to the canonical spelling."""
    index: Dict[str, str] = {}
    for entry in entries:
        index.setdefault(_canon_key(entry), entry)
    return index


def _canon_key(name: str) -> str:
    """Case-, accent-, and punctuation-insensitive key for canonical lookups."""
    folded = unicodedata.normalize("NFKD", (name or "").strip().lower())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()


CANON_PROGS_INDEX = _canon_index(CANON_PROGS)
CANON_UNIS_INDEX = _canon_index(CANON_UNIS)


def _post_normalize_program(prog: str) -> str:
    """Apply common fixes, title case, then canonical/fuzzy mapping."""
    p = (prog or "").strip()
    p = COMMON_PROG_FIXES.get(p, p)
    p = p.title()
    canonical = CANON_PROGS_INDEX.get(_canon_key(p))
    if canonical:
        return canonical
    match = _best_match(p, CANON_PROGS, cutoff=0.84)
    return match or p


def _post_normalize_university(uni: str) -> str:
    """Expand abbreviations, apply common fixes, capitalization, and canonical map."""
    u = (uni or "").strip()

    # Abbreviations
    for pat, full in ABBREV_UNI.items():
        if re.fullmatch(pat, u):
            u = full
            break

    # Common spelling fixes
    u = COMMON_UNI_FIXES.get(u, u)

    # Normalize 'Of' → 'of'
    if u:
        u = re.sub(r"\bOf\b", "of", u.title())

    # Canonical or fuzzy map (folded lookup first - see _post_normalize_program)
    canonical = CANON_UNIS_INDEX.get(_canon_key(u))
    if canonical:
        return canonical
    match = _best_match(u, CANON_UNIS, cutoff=0.86)
    return match or u or "Unknown"


def _normalize_pair(std_prog: str, std_uni: str) -> Dict[str, str]:
    """Post-process a raw model answer. Cheap, deterministic, always re-run.

    Kept separate from the model call so that the expensive half can be cached
    while this half still picks up changes to the canonical lists.
    """
    return {
        "standardized_program": _post_normalize_program(std_prog),
        "standardized_university": _post_normalize_university(std_uni),
    }


def _llm_raw(program_text: str) -> Tuple[str, str]:
    """Ask the model to split one string, returning its answer un-normalized.

    This is the expensive half of the pipeline and the only part worth caching:
    the same program string always produces the same request.
    """
    llm = _load_llm()

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for x_in, x_out in FEW_SHOTS:
        messages.append(
            {"role": "user", "content": json.dumps(x_in, ensure_ascii=False)}
        )
        messages.append(
            {
                "role": "assistant",
                "content": json.dumps(x_out, ensure_ascii=False),
            }
        )
    messages.append(
        {
            "role": "user",
            "content": json.dumps({"program": program_text}, ensure_ascii=False),
        }
    )

    out = llm.create_chat_completion(
        messages=messages,
        temperature=0.0,
        max_tokens=128,
        top_p=1.0,
    )

    text = (out["choices"][0]["message"]["content"] or "").strip()
    try:
        match = JSON_OBJ_RE.search(text)
        obj = json.loads(match.group(0) if match else text)
        std_prog = str(obj.get("standardized_program", "")).strip()
        std_uni = str(obj.get("standardized_university", "")).strip()
    except Exception:
        std_prog, std_uni = _split_fallback(program_text)

    return std_prog, std_uni


def _call_llm(program_text: str) -> Dict[str, str]:
    """Query the tiny LLM and return standardized fields."""
    return _normalize_pair(*_llm_raw(program_text))


def _normalize_input(payload: Any) -> List[Dict[str, Any]]:
    """Accept either a list of rows or {'rows': [...]}."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        return payload["rows"]
    return []


@app.get("/")
def health() -> Any:
    """Simple liveness check."""
    return jsonify({"ok": True})


@app.post("/standardize")
def standardize() -> Any:
    """Standardize rows from an HTTP request and return JSON."""
    payload = request.get_json(force=True, silent=True)
    rows = _normalize_input(payload)

    out: List[Dict[str, Any]] = []
    for row in rows:
        program_text = (row or {}).get("program") or ""
        result = _call_llm(program_text)
        row["llm-generated-program"] = result["standardized_program"]
        row["llm-generated-university"] = result["standardized_university"]
        out.append(row)

    return jsonify({"rows": out})


# ---------------- MODIFIED: dedupe + parallel CLI ----------------
# The original CLI called the model once per row.  Over 30k rows that is hours of
# CPU time spent re-answering the same question, because the dataset contains far
# fewer distinct "program" strings than rows.  The CLI below standardizes each
# DISTINCT string once and fans that work across worker processes, then maps the
# answers back onto every row.  Output is unchanged per row.


CACHE_PATH = BASE_DIR / "standardization_cache.json"


def _standardize_unique(program_text: str) -> Tuple[str, Tuple[str, str]]:
    """Worker entry point: run the model on one distinct program string."""
    return program_text, _llm_raw(program_text)


def _load_cache(path: Path) -> Dict[str, Tuple[str, str]]:
    """Load previously computed raw model answers, keyed by program string."""
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            stored = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {}
    return {
        key: (value[0], value[1])
        for key, value in stored.items()
        if isinstance(value, list) and len(value) == 2
    }


def _save_cache(path: Path, cache: Dict[str, Tuple[str, str]]) -> None:
    """Write the cache out atomically so an interrupted run cannot corrupt it."""
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump({k: list(v) for k, v in cache.items()}, handle, ensure_ascii=False)
    temp_path.replace(path)


def _init_worker() -> None:
    """Load this process's own copy of the model once, before any task runs."""
    # Each worker holds its own llama.cpp context, so keep per-instance threads
    # low; the parallelism comes from running many workers, not many threads.
    os.environ.setdefault("N_THREADS", "1")
    global N_THREADS
    N_THREADS = 1
    _load_llm()


def _standardize_texts(
    texts: List[str],
    workers: int,
    cache: Dict[str, Tuple[str, str]],
    cache_path: Optional[Path] = None,
    progress_every: int = 250,
) -> Dict[str, Tuple[str, str]]:
    """Run the model over the strings not already answered in ``cache``."""
    pending = [text for text in texts if text not in cache]
    total = len(pending)
    print(
        f"[llm] {len(texts) - total:,} of {len(texts):,} distinct strings already "
        f"cached; {total:,} to compute",
        file=sys.stderr,
    )
    if not pending:
        return cache

    started = time.time()
    done = 0

    def _note_progress() -> None:
        if done % progress_every and done != total:
            return
        elapsed = time.time() - started
        rate = done / elapsed if elapsed else 0.0
        remaining = (total - done) / rate / 60 if rate else 0.0
        print(
            f"[llm] {done:,}/{total:,} new strings | "
            f"{elapsed / 60:.1f} min elapsed | ~{remaining:.1f} min left",
            file=sys.stderr,
        )
        # Checkpoint so an interrupted run keeps the work it has already paid for.
        if cache_path is not None:
            _save_cache(cache_path, cache)

    if workers <= 1:
        _load_llm()
        for text in pending:
            cache[text] = _llm_raw(text)
            done += 1
            _note_progress()
    else:
        import multiprocessing as mp

        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=workers, initializer=_init_worker) as pool:
            for text, raw in pool.imap_unordered(
                _standardize_unique, pending, chunksize=8
            ):
                cache[text] = raw
                done += 1
                _note_progress()

    if cache_path is not None:
        _save_cache(cache_path, cache)
    return cache


def _cli_process_file(
    in_path: str,
    out_path: str | None,
    append: bool,
    to_stdout: bool,
    workers: int = 1,
    json_array: bool = False,
    cache_path: Optional[Path] = CACHE_PATH,
) -> None:
    """Standardize every row of a JSON file.

    Writes JSON Lines by default (the original behaviour).  With ``--json-array``
    the whole result is written as a single JSON array instead, which is the
    shape the assignment's deliverable expects.
    """
    with open(in_path, "r", encoding="utf-8") as f:
        rows = _normalize_input(json.load(f))

    # One model call per distinct program string instead of one per row.
    unique_texts = sorted({(row or {}).get("program") or "" for row in rows})
    print(
        f"[llm] {len(rows):,} rows -> {len(unique_texts):,} distinct program strings "
        f"({len(unique_texts) / max(len(rows), 1):.1%}) on {workers} worker(s)",
        file=sys.stderr,
    )

    cache = _load_cache(cache_path) if cache_path else {}
    cache = _standardize_texts(unique_texts, workers, cache, cache_path)

    # Post-processing is re-applied on every run, so edits to the canonical
    # lists take effect without paying for the model again.
    for row in rows:
        program_text = (row or {}).get("program") or ""
        raw = cache.get(program_text)
        result = _normalize_pair(*raw) if raw else _call_llm(program_text)
        row["llm-generated-program"] = result["standardized_program"]
        row["llm-generated-university"] = result["standardized_university"]

    sink = sys.stdout if to_stdout else None
    if not to_stdout:
        out_path = out_path or (in_path + (".json" if json_array else ".jsonl"))
        sink = open(out_path, "a" if append and not json_array else "w", encoding="utf-8")

    assert sink is not None  # for type-checkers
    try:
        if json_array:
            json.dump(rows, sink, ensure_ascii=False, indent=2)
            sink.write("\n")
        else:
            for row in rows:
                json.dump(row, sink, ensure_ascii=False)
                sink.write("\n")
        sink.flush()
    finally:
        if sink is not sys.stdout:
            sink.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Standardize program/university with a tiny local LLM.",
    )
    parser.add_argument(
        "--file",
        help="Path to JSON input (list of rows or {'rows': [...]})",
        default=None,
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Run the HTTP server instead of CLI.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output path for JSON Lines (ndjson). "
        "Defaults to <input>.jsonl when --file is set.",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to the output file instead of overwriting.",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Write JSON Lines to stdout instead of a file.",
    )
    # MODIFIED: added --workers and --json-array.
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Worker processes to standardize distinct program strings with. "
        "Each worker loads its own ~670 MB model copy, so size this to your RAM "
        "and core count (default: 1).",
    )
    parser.add_argument(
        "--json-array",
        action="store_true",
        help="Write one JSON array instead of JSON Lines.",
    )
    parser.add_argument(
        "--cache",
        default=str(CACHE_PATH),
        help="JSON file of raw model answers reused across runs "
        "(default: %(default)s). Pass an empty string to disable.",
    )
    args = parser.parse_args()

    if args.serve or args.file is None:
        port = int(os.getenv("PORT", "8000"))
        app.run(host="0.0.0.0", port=port, debug=False)
    else:
        _cli_process_file(
            in_path=args.file,
            out_path=args.out,
            append=bool(args.append),
            to_stdout=bool(args.stdout),
            workers=max(1, int(args.workers)),
            json_array=bool(args.json_array),
            cache_path=Path(args.cache) if args.cache else None,
        )
