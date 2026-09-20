"""Fetch newly posted Grad Cafe results and add them to PostgreSQL.

This is what the "Pull Data" button runs.  It drives the ETL pipeline end to
end, reusing the scraper and cleaner rather than reimplementing them::

    scrape.GradCafeScraper -> clean.clean_data -> llm_hosting/app.py -> load_data

Only the newest page of results is walked, not the whole site: the scraper starts
at the most recent entry and collects ``--target`` records.  Anything already in
the database is recognized by its ``p_id`` and refreshed rather than duplicated,
so overlapping with the previous pull is harmless and expected.

Every stage is a parameter
--------------------------
:func:`run_pipeline` takes its scraper, cleaner, standardizer and loader as
arguments, defaulting to the real ones.  That is what lets the test suite run
the whole pull -- including the write to PostgreSQL -- against a fake scraper
that returns a handful of records from memory, with no network access and no
minutes-long scrape.  Nothing in the tests reaches the internet, and nothing in
this module knows it is being tested.

Busy state is an object, not a sleep
------------------------------------
While a pull is in flight the web app refuses to start a second one, and says so
rather than pretending the figures are final.  That state lives behind
:class:`PullState`, which has two implementations: :class:`FileState` for the
running application, where the pull is a separate process and the flag has to
survive a Flask restart, and :class:`MemoryState` for a pull that runs in-process
-- which is how the tests observe and set it without waiting on anything.

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
from typing import Any, Callable, Dict, List, Optional, Sequence

import load_data

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent

# The standardizer is the instructor-provided tool, vendored beside the
# application rather than inside src/: it imports llama_cpp and downloads a
# 670 MB model, so it is a subprocess this module shells out to, never an import.
LLM_DIR = PROJECT_ROOT / "llm_hosting"

DATA_DIR = PROJECT_ROOT / "data"
PULL_DIR = DATA_DIR / "pull"

# The pull's own working files. A pull walks the newest results only, so these
# are deliberately not the paths a full 50,000-row scrape would write.
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

#: Stages reported to the webpage, in order.
STAGES = ("scraping", "cleaning", "standardizing", "loading")

#: What the status file says when no pull has ever run.
IDLE_STATUS: Dict[str, Any] = {
    "state": "idle",
    "message": "No data pull has been run yet.",
}


def _now() -> str:
    """The current UTC time, to the second, as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PullInProgress(RuntimeError):
    """Raised when a pull is asked for while one is already running.

    Distinct from every other failure because it is not one: the right response
    is to wait, and the web layer turns it into ``409`` rather than ``500``.
    """


# ----------------------------------------------------------------------
# Busy state
# ----------------------------------------------------------------------
class PullState:
    """The busy flag and progress message shared by the app and the pull.

    Two things are needed of it, and the reason it is an object rather than a
    module-level variable is that the web app must be able to be handed a
    different one:

    * ``is_busy`` / ``begin`` / ``end`` -- the gate that stops a second scrape
      starting while one is running.
    * ``read`` / ``write`` -- the progress message the page polls.

    Subclasses implement the storage.  This base class raises, so a partially
    implemented backend fails loudly rather than reporting "not busy" forever.
    """

    def is_busy(self) -> bool:
        """Is a pull in progress right now?"""
        raise NotImplementedError

    def begin(self) -> bool:
        """Claim the flag.  ``False`` means a pull was already running."""
        raise NotImplementedError

    def end(self) -> None:
        """Release the flag.  Safe to call when it was never claimed."""
        raise NotImplementedError

    def read(self) -> Dict[str, Any]:
        """The current status, defaulting to :data:`IDLE_STATUS`."""
        raise NotImplementedError

    def write(self, **fields: Any) -> Dict[str, Any]:
        """Merge ``fields`` into the status and return the new one."""
        raise NotImplementedError

    def snapshot(self) -> Dict[str, Any]:
        """The status plus a ``running`` flag: what ``GET /status`` returns."""
        status = dict(self.read())
        status["running"] = self.is_busy()
        return status


def pid_is_alive(pid: int) -> bool:
    """Is a process with this id still running?

    :param pid: the process id to test. Zero and negatives are never alive.

    Used to clear a lock left behind by a pull that was killed, so a crash
    cannot block the button forever.  Where the answer cannot be determined,
    this reports *alive*: refusing to start a second scrape is the safer error.
    """
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
        return True  # someone else's process, but a live one
    return True


class FileState(PullState):
    """Busy flag and status kept in two files, for the running application.

    The pull is a separate process there, so the flag cannot be a variable in
    the web app: it is a file holding the worker's process id, and the status is
    a small JSON document written atomically.  Both survive a Flask restart,
    which an in-memory flag would not, and both are readable by a person
    debugging a stuck pull.

    :param status_path: where the progress document lives.
    :param lock_path: where the process id lives while a pull runs.
    """

    def __init__(
        self,
        status_path: Path = STATUS_PATH,
        lock_path: Path = LOCK_PATH,
    ) -> None:
        self.status_path = Path(status_path)
        self.lock_path = Path(lock_path)

    # --- the flag -----------------------------------------------------
    def running_pid(self) -> Optional[int]:
        """The process id of the pull in progress, or ``None``.

        A lock left behind by a killed process is cleared here rather than
        trusted, so a crashed pull does not disable the button permanently.
        """
        if not self.lock_path.exists():
            return None
        try:
            pid = int(self.lock_path.read_text(encoding="utf-8").strip() or 0)
        except (ValueError, OSError):
            self.lock_path.unlink(missing_ok=True)
            return None

        if pid_is_alive(pid):
            return pid

        self.lock_path.unlink(missing_ok=True)
        return None

    def is_busy(self) -> bool:
        return self.running_pid() is not None

    def begin(self) -> bool:
        if self.is_busy():
            return False
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # "x" fails rather than truncating if another process just won the race.
            with self.lock_path.open("x", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
        except FileExistsError:
            return False
        return True

    def end(self) -> None:
        self.lock_path.unlink(missing_ok=True)

    # --- the status ---------------------------------------------------
    def read(self) -> Dict[str, Any]:
        if not self.status_path.exists():
            return dict(IDLE_STATUS)
        try:
            with self.status_path.open("r", encoding="utf-8") as handle:
                status = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return dict(IDLE_STATUS)
        return status if isinstance(status, dict) else dict(IDLE_STATUS)

    def write(self, **fields: Any) -> Dict[str, Any]:
        status = self.read()
        status.update(fields)
        status["updated_at"] = _now()

        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.status_path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(status, handle, indent=2)
        temp_path.replace(self.status_path)  # atomic: no reader sees half a file
        return status


class MemoryState(PullState):
    """Busy flag and status held in this process, for an in-process pull.

    Used wherever the pull does not outlive the request that started it -- which
    is how the tests run it.  Nothing is written to disk, the flag flips the
    instant :meth:`begin` is called, and a caller can set or inspect it directly.
    That is what makes the busy-state tests deterministic: they assert against an
    observable flag rather than waiting for a scrape to happen to be running.
    """

    def __init__(self, busy: bool = False, status: Optional[Dict[str, Any]] = None) -> None:
        self.busy = busy
        self.status: Dict[str, Any] = dict(status) if status else dict(IDLE_STATUS)

    def is_busy(self) -> bool:
        return self.busy

    def begin(self) -> bool:
        if self.busy:
            return False
        self.busy = True
        return True

    def end(self) -> None:
        self.busy = False

    def read(self) -> Dict[str, Any]:
        return dict(self.status)

    def write(self, **fields: Any) -> Dict[str, Any]:
        self.status.update(fields)
        self.status["updated_at"] = _now()
        return dict(self.status)


def default_state() -> FileState:
    """The state backend the running application uses."""
    return FileState()


# ----------------------------------------------------------------------
# The pipeline stages, each replaceable
# ----------------------------------------------------------------------
#: A stage that returns raw scraped rows.
Scraper = Callable[[], List[Dict[str, Any]]]
#: A stage that normalizes raw rows into cleaned records.
Cleaner = Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]
#: A stage that adds the two LLM columns, or returns its input unchanged.
Standardizer = Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]
#: A stage that writes cleaned records and returns the loader's summary.
Loader = Callable[[List[Dict[str, Any]]], Dict[str, Any]]


def make_scraper(target: int = DEFAULT_TARGET) -> Scraper:
    """Build the real scraper stage: walk the newest ``target`` results.

    :param target: how many of the newest results to collect.

    ``scrape`` is imported inside the returned callable rather than at module
    import, for two reasons: importing this module must not require
    BeautifulSoup, and a caller who injects a different scraper should not pay
    for an import it will never use.
    """

    def scrape_newest() -> List[Dict[str, Any]]:
        import scrape

        # The scraper reads its own module-level paths. Pointing them at the pull
        # directory keeps a pull from overwriting a full scrape, and starting
        # without the checkpoint is what makes this walk the *newest* results
        # rather than resuming where the last long scrape stopped.
        PULL_DIR.mkdir(parents=True, exist_ok=True)
        scrape.RAW_DATA_PATH = PULL_RAW_PATH
        scrape.CHECKPOINT_PATH = PULL_CHECKPOINT_PATH

        scraper = scrape.GradCafeScraper()
        return scraper.scrape_data(target_entries=target, resume=False)

    return scrape_newest


def default_cleaner(raw_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The real cleaning stage: normalize raw scraped rows.

    :param raw_rows: rows as the scraper stored them, text exactly as rendered.
    :returns: cleaned records, with typed fields and consistent ``None``s.
    """
    import clean

    return clean.clean_data(raw_rows)


def make_standardizer(
    workers: int = DEFAULT_WORKERS,
    clean_path: Path = PULL_CLEAN_PATH,
    out_path: Path = PULL_EXTENDED_PATH,
) -> Standardizer:
    """Build the real standardizer stage: run the local LLM over the records.

    :param workers: standardizer worker processes.
    :param clean_path: where to write the records for the subprocess to read.
    :param out_path: where the subprocess writes its output.

    The stage returns its input unchanged if the standardizer could not run --
    most likely because ``llama-cpp-python`` is not installed on this machine.
    That is not fatal: the rows still load, just with empty LLM columns, and the
    loader's ``COALESCE`` means a later run can fill them in without disturbing
    anything else.
    """

    def standardize(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        clean_path.parent.mkdir(parents=True, exist_ok=True)
        clean_path.write_text(
            json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        command = [
            sys.executable,
            str(LLM_DIR / "app.py"),
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
            print(
                "[pull] could not start the standardizer: {0}".format(exc),
                file=sys.stderr,
            )
            return records

        if completed.returncode != 0:
            print(
                "[pull] standardizer exited with {0}; loading without the LLM "
                "columns".format(completed.returncode),
                file=sys.stderr,
            )
            return records

        if not out_path.exists():
            return records
        return load_data.read_records(out_path)

    return standardize


def passthrough_standardizer(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The ``--skip-llm`` stage: leave the two LLM columns to a later run."""
    return records


def default_loader(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The real loading stage: write records to PostgreSQL.

    :param records: cleaned records, straight from the previous stage -- no
        temporary file, so a pull that scraped nothing writes nothing.
    :returns: the loader's summary of what changed.
    """
    return load_data.load_into_database(records=records, verbose=False)


# ----------------------------------------------------------------------
# The pull
# ----------------------------------------------------------------------
def run_pipeline(
    target: int = DEFAULT_TARGET,
    workers: int = DEFAULT_WORKERS,
    skip_llm: bool = False,
    scraper: Optional[Scraper] = None,
    cleaner: Optional[Cleaner] = None,
    standardizer: Optional[Standardizer] = None,
    loader: Optional[Loader] = None,
    state: Optional[PullState] = None,
) -> Dict[str, Any]:
    """Run the whole pipeline and return a summary of what changed.

    :param target: how many of the newest results to check, for the default
        scraper. Ignored when ``scraper`` is given.
    :param workers: standardizer worker processes, for the default standardizer.
    :param skip_llm: load without the two LLM columns.
    :param scraper: a callable returning raw scraped rows.
    :param cleaner: a callable normalizing raw rows into records.
    :param standardizer: a callable adding the LLM columns.
    :param loader: a callable writing records and returning a summary.
    :param state: where to record the busy flag and progress.
    :returns: the loader's summary, plus ``scraped``, ``llm_applied`` and
        ``elapsed_seconds``.
    :raises PullInProgress: if a pull is already running.
    :raises Exception: whatever a stage raised, after recording the failure in
        the status so the page can report it. The database is left as it was:
        the loader writes in one transaction and is the last stage to run.

    Claims the busy flag for the duration and releases it in a ``finally``, so a
    stage that raises cannot leave the button disabled.
    """
    state = state if state is not None else default_state()
    scraper = scraper if scraper is not None else make_scraper(target)
    cleaner = cleaner if cleaner is not None else default_cleaner
    loader = loader if loader is not None else default_loader
    if standardizer is None:
        standardizer = (
            passthrough_standardizer if skip_llm else make_standardizer(workers)
        )

    if not state.begin():
        raise PullInProgress(
            "A data pull is already running. Wait for it to finish -- starting a "
            "second scrape would hit Grad Cafe twice over for the same results."
        )

    started = time.monotonic()
    try:
        return _run_stages(
            state=state,
            scraper=scraper,
            cleaner=cleaner,
            standardizer=standardizer,
            loader=loader,
            skip_llm=skip_llm,
            started=started,
        )
    except Exception as exc:
        state.write(
            state="error",
            stage="failed",
            message="The data pull failed: {0}".format(exc),
            finished_at=_now(),
            error="{0}: {1}".format(type(exc).__name__, exc),
        )
        raise
    finally:
        state.end()


def _run_stages(
    state: PullState,
    scraper: Scraper,
    cleaner: Cleaner,
    standardizer: Standardizer,
    loader: Loader,
    skip_llm: bool,
    started: float,
) -> Dict[str, Any]:
    """The four stages, with the progress messages the page shows between them."""
    # --- scrape -------------------------------------------------------
    state.write(
        state="running",
        stage="scraping",
        message="Checking Grad Cafe for new results...",
        started_at=_now(),
        finished_at=None,
        summary=None,
        error=None,
    )
    raw_rows = scraper()
    if not raw_rows:
        raise RuntimeError(
            "The scraper returned no rows. Grad Cafe may be unreachable or "
            "blocking requests; nothing was written to the database."
        )

    # --- clean --------------------------------------------------------
    state.write(
        stage="cleaning",
        message="Cleaning {0:,} scraped results...".format(len(raw_rows)),
    )
    records = cleaner(raw_rows)

    # --- standardize --------------------------------------------------
    state.write(
        stage="standardizing",
        message=(
            "Skipping LLM standardization."
            if skip_llm
            else "Standardizing program and university names "
            "({0:,} new records)...".format(len(records))
        ),
    )
    standardized = standardizer(records)

    # A standardizer that could not run returns its input unchanged -- the same
    # list object -- so identity is what distinguishes "ran" from "declined".
    llm_applied = not skip_llm and standardized is not records

    # --- load ---------------------------------------------------------
    state.write(stage="loading", message="Writing new records to PostgreSQL...")
    summary = dict(loader(standardized))
    summary["llm_applied"] = llm_applied
    summary["scraped"] = len(raw_rows)
    summary["elapsed_seconds"] = round(time.monotonic() - started, 1)

    state.write(
        state="done",
        stage="complete",
        message=describe_summary(summary, skip_llm=skip_llm),
        finished_at=_now(),
        summary=summary,
    )
    return summary


def describe_summary(summary: Dict[str, Any], skip_llm: bool = False) -> str:
    """The one-line result the page shows when a pull finishes.

    :param summary: the summary :func:`run_pipeline` assembled.
    :param skip_llm: whether the LLM stage was deliberately skipped, which
        decides whether empty LLM columns are worth mentioning.
    """
    if summary.get("inserted"):
        message = (
            "Added {inserted:,} new records ({total:,} now in the database). "
            "Press Update Analysis to recompute the figures below.".format(
                inserted=summary["inserted"], total=summary["total"]
            )
        )
    else:
        message = (
            "No new results were available -- all {scraped:,} entries checked "
            "were already in the database ({total:,} rows).".format(
                scraped=summary.get("scraped", 0), total=summary.get("total", 0)
            )
        )
    if not summary.get("llm_applied") and not skip_llm:
        message += " The LLM columns were left empty; the standardizer did not run."
    return message


def spawn_pull(
    state: Optional[PullState] = None,
    target: int = DEFAULT_TARGET,
    python: Optional[str] = None,
) -> Dict[str, Any]:
    """Start a pull in a detached child process and return immediately.

    :param state: where to record that a pull is starting.
    :param target: how many of the newest results to check.
    :param python: the interpreter to run. Defaults to the current one.
    :returns: the status the page should display next.
    :raises OSError: if the child could not be started, which the route turns
        into an error message rather than a stack trace.

    This is the runner the web application uses.  A scrape takes minutes; doing
    it inside the request would hold the connection open for all of them, and
    doing it in a thread would tie it to the lifetime of one Flask worker.  The
    child claims the busy flag itself as its first act, so two clicks in quick
    succession cannot produce two scrapes.
    """
    state = state if state is not None else default_state()
    command = [python or sys.executable, str(SRC_DIR / "pull_data.py")]
    if target != DEFAULT_TARGET:
        command += ["--target", str(target)]

    # Detached on purpose: the scrape outlives this request, and the browser gets
    # an answer immediately instead of waiting minutes for a response.
    subprocess.Popen(
        command,
        cwd=str(SRC_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    return state.write(
        state="running",
        stage="starting",
        message="Starting up -- contacting Grad Cafe...",
        started_at=_now(),
        finished_at=None,
        error=None,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run one pull from the command line."""
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

    try:
        summary = run_pipeline(
            target=args.target,
            workers=args.workers,
            skip_llm=args.skip_llm,
        )
    except PullInProgress as exc:
        # Not a failure, and deliberately not written to the status: the pull
        # that *is* running owns that, and overwriting it would report the wrong
        # thing on the page.
        print(exc, file=sys.stderr)
        return 2
    except Exception as exc:  # already recorded in the status by run_pipeline
        print("[pull] failed: {0}".format(exc), file=sys.stderr)
        return 1

    print(
        "Scraped {scraped:,}, inserted {inserted:,} new, {total:,} rows total "
        "in {elapsed_seconds}s".format(**summary),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    # Excluded from coverage rather than exercised: running this line means
    # re-executing the module under a second name, which would define a
    # second copy of everything in it. The main() it dispatches to is
    # called directly by the tests, which is where the behaviour lives.
    raise SystemExit(main())
