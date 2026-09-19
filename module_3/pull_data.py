"""Fetch newly posted Grad Cafe results and add them to PostgreSQL.

This is what the "Pull Data" button runs.  It drives the Module 2 pipeline
end to end, reusing that code rather than reimplementing it:

    scrape.GradCafeScraper  ->  clean.clean_data  ->  llm_hosting/app.py  ->  load_data

Only the newest page of results is walked, not the whole site: the scraper starts
at the most recent entry and collects ``--target`` records.  Anything already in
the database is recognised by its ``p_id`` and refreshed rather than duplicated,
so overlapping with the previous pull is harmless and expected.

The Flask app runs this file as a subprocess so a slow scrape never blocks a web
request, and reads ``data/pull_status.json`` to report progress.  It can also be
run directly:

    python pull_data.py                 # newest 500 results
    python pull_data.py --target 2000   # go back further
    python pull_data.py --skip-llm      # leave the LLM columns to a later run
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
PULL_DIR = DATA_DIR / "pull"

# Kept apart from the Module 2 working files on purpose.  A pull walks the newest
# results only, so letting it write data/raw_applicant_data.json would replace a
# 50,000-row scrape with a few hundred rows.
PULL_RAW_PATH = PULL_DIR / "raw_applicant_data.json"
PULL_CHECKPOINT_PATH = PULL_DIR / "checkpoint.json"
PULL_CLEAN_PATH = PULL_DIR / "applicant_data.json"
PULL_EXTENDED_PATH = PULL_DIR / "llm_extend_applicant_data.json"

STATUS_PATH = DATA_DIR / "pull_status.json"
LOCK_PATH = PROJECT_ROOT / ".pull_data.lock"

DEFAULT_TARGET = 500

# One worker, deliberately. Module 2 used eight to standardize 50,000 rows, but a
# pull yields only a handful of program strings the cache has not already seen --
# one, in a 300-record test run -- so extra workers buy no speed here and cost
# reliability: each worker re-checks the model file on start-up, and two doing
# that at once raced on Windows (WinError 32, file in use by another process).
DEFAULT_WORKERS = 1

# Stages reported to the webpage, in order.
STAGES = ("scraping", "cleaning", "standardizing", "loading")


# ----------------------------------------------------------------------
# Status file
#
# The scrape runs in a separate process, so progress has to reach the web app
# through something both can see.  A small JSON file is enough and survives a
# Flask restart, which an in-memory variable would not.
# ----------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_status(**fields: Any) -> Dict[str, Any]:
    """Merge ``fields`` into the status file and return the new status."""
    status = read_status()
    status.update(fields)
    status["updated_at"] = _now()

    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = STATUS_PATH.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(status, handle, indent=2)
    temp_path.replace(STATUS_PATH)  # atomic, so a reader never sees half a file
    return status


def read_status() -> Dict[str, Any]:
    """Read the status file, returning a sensible default when absent."""
    if not STATUS_PATH.exists():
        return {"state": "idle", "message": "No data pull has been run yet."}
    try:
        with STATUS_PATH.open("r", encoding="utf-8") as handle:
            status = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {"state": "idle", "message": "No data pull has been run yet."}
    return status if isinstance(status, dict) else {"state": "idle"}


# ----------------------------------------------------------------------
# Lock
#
# The assignment requires that a second scrape cannot start while one is
# running.  The lock is a file holding the worker's PID rather than an in-memory
# flag, because Flask's reloader runs two processes and a restart would
# otherwise forget that a scrape is still going.
# ----------------------------------------------------------------------
def _pid_is_alive(pid: int) -> bool:
    """Is a process with this id still running?"""
    if pid <= 0:
        return False
    if os.name == "nt":
        # No os.kill(pid, 0) on Windows; ask the task list instead.
        try:
            output = subprocess.run(
                ["tasklist", "/FI", "PID eq {0}".format(pid), "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return True  # cannot tell; assume alive rather than double-starting
        return str(pid) in output
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def running_pid() -> Optional[int]:
    """The PID of the pull in progress, or ``None`` if nothing is running.

    A lock left behind by a killed process is cleared here, so a crashed pull
    cannot block the button forever.
    """
    if not LOCK_PATH.exists():
        return None
    try:
        pid = int(LOCK_PATH.read_text(encoding="utf-8").strip() or 0)
    except (ValueError, OSError):
        LOCK_PATH.unlink(missing_ok=True)
        return None

    if _pid_is_alive(pid):
        return pid

    LOCK_PATH.unlink(missing_ok=True)
    return None


def is_running() -> bool:
    return running_pid() is not None


def acquire_lock() -> bool:
    """Claim the lock, or return False if a pull is already in progress."""
    if is_running():
        return False
    try:
        # "x" fails rather than truncating if another process just won the race.
        with LOCK_PATH.open("x", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
    except FileExistsError:
        return False
    return True


def release_lock() -> None:
    LOCK_PATH.unlink(missing_ok=True)


# ----------------------------------------------------------------------
# The pipeline
# ----------------------------------------------------------------------
def _scrape(target: int) -> List[Dict[str, Any]]:
    """Walk the newest Grad Cafe results with the Module 2 scraper."""
    import scrape

    # The scraper reads its own module-level paths.  Pointing them at the pull
    # directory keeps a pull from overwriting the full Module 2 scrape, and
    # starting without the checkpoint is what makes this walk the *newest*
    # results rather than resuming where the last long scrape stopped.
    PULL_DIR.mkdir(parents=True, exist_ok=True)
    scrape.RAW_DATA_PATH = PULL_RAW_PATH
    scrape.CHECKPOINT_PATH = PULL_CHECKPOINT_PATH

    scraper = scrape.GradCafeScraper()
    return scraper.scrape_data(target_entries=target, resume=False)


def _standardize(clean_path: Path, out_path: Path, workers: int) -> bool:
    """Run the Module 2 LLM standardizer over the freshly cleaned rows.

    Returns False if the standardizer could not run -- most likely because
    ``llama-cpp-python`` is not installed on this machine.  That is not fatal:
    the rows still load, just with empty LLM columns, and the loader's COALESCE
    means a later run can fill them in without disturbing anything else.
    """
    command = [
        sys.executable,
        str(PROJECT_ROOT / "llm_hosting" / "app.py"),
        "--file",
        str(clean_path),
        "--out",
        str(out_path),
        "--json-array",
        "--workers",
        str(workers),
    ]
    try:
        completed = subprocess.run(command, cwd=str(PROJECT_ROOT), check=False)
    except OSError as exc:
        print("[pull] could not start the standardizer: {0}".format(exc), file=sys.stderr)
        return False

    if completed.returncode != 0:
        print(
            "[pull] standardizer exited with {0}; loading without the LLM "
            "columns".format(completed.returncode),
            file=sys.stderr,
        )
        return False
    return out_path.exists()


def pull(
    target: int = DEFAULT_TARGET,
    workers: int = DEFAULT_WORKERS,
    skip_llm: bool = False,
) -> Dict[str, Any]:
    """Run the whole pipeline and return a summary of what changed."""
    import clean
    import load_data

    started = time.monotonic()
    PULL_DIR.mkdir(parents=True, exist_ok=True)

    # --- scrape -------------------------------------------------------
    write_status(
        state="running",
        stage="scraping",
        message="Checking Grad Cafe for new results...",
        started_at=_now(),
        finished_at=None,
        summary=None,
        error=None,
    )
    raw_rows = _scrape(target)
    if not raw_rows:
        raise RuntimeError(
            "The scraper returned no rows. Grad Cafe may be unreachable or "
            "blocking requests; nothing was written to the database."
        )

    # --- clean --------------------------------------------------------
    write_status(
        stage="cleaning",
        message="Cleaning {0:,} scraped results...".format(len(raw_rows)),
    )
    cleaned_rows = clean.clean_data(raw_rows)
    clean.save_data(cleaned_rows, PULL_CLEAN_PATH)

    # --- standardize --------------------------------------------------
    load_path = PULL_CLEAN_PATH
    llm_applied = False
    if skip_llm:
        write_status(stage="standardizing", message="Skipping LLM standardization.")
    else:
        write_status(
            stage="standardizing",
            message="Standardizing program and university names "
            "({0:,} new records)...".format(len(cleaned_rows)),
        )
        if _standardize(PULL_CLEAN_PATH, PULL_EXTENDED_PATH, workers):
            load_path = PULL_EXTENDED_PATH
            llm_applied = True

    # --- load ---------------------------------------------------------
    write_status(stage="loading", message="Writing new records to PostgreSQL...")
    summary = load_data.load_into_database(path=load_path, verbose=False)
    summary["llm_applied"] = llm_applied
    summary["scraped"] = len(raw_rows)
    summary["elapsed_seconds"] = round(time.monotonic() - started, 1)

    message = "Added {inserted:,} new records ({total:,} now in the database).".format(
        inserted=summary["inserted"], total=summary["total"]
    )
    if summary["inserted"] == 0:
        message = (
            "No new results were available -- all {scraped:,} entries checked "
            "were already in the database ({total:,} rows).".format(
                scraped=summary["scraped"], total=summary["total"]
            )
        )
    if not llm_applied and not skip_llm:
        message += " The LLM columns were left empty; the standardizer did not run."

    write_status(
        state="done",
        stage="complete",
        message=message,
        finished_at=_now(),
        summary=summary,
    )
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pull newly posted Grad Cafe results into PostgreSQL."
    )
    parser.add_argument(
        "--target",
        type=int,
        default=DEFAULT_TARGET,
        help="how many of the newest results to check (default: %(default)s)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="standardizer worker processes (default: %(default)s)",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="load without the LLM-generated program and university columns",
    )
    args = parser.parse_args(argv)

    if not acquire_lock():
        print(
            "A data pull is already running (pid {0}).".format(running_pid()),
            file=sys.stderr,
        )
        return 2

    try:
        summary = pull(
            target=args.target, workers=args.workers, skip_llm=args.skip_llm
        )
    except Exception as exc:  # reported to the webpage rather than lost in a log
        write_status(
            state="error",
            stage="failed",
            message="The data pull failed: {0}".format(exc),
            finished_at=_now(),
            error="{0}: {1}".format(type(exc).__name__, exc),
        )
        print("[pull] failed: {0}".format(exc), file=sys.stderr)
        return 1
    finally:
        release_lock()

    print(
        "Scraped {scraped:,}, inserted {inserted:,} new, {total:,} rows total "
        "in {elapsed_seconds}s".format(**summary),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
