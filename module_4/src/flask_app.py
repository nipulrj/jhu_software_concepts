"""Flask front end for the Grad Cafe analysis.

One page, built the way the lecture builds one -- a base template, an index
template that extends it, and POST routes that redirect back with ``url_for`` --
with two buttons on top of it:

* **Pull Data** checks Grad Cafe for newly submitted results and adds them to
  PostgreSQL.  It runs ``pull_data.py`` as a subprocess so a scrape that takes
  minutes never holds a web request open, and it refuses to start a second one
  while the first is still going.
* **Update Analysis** re-runs the queries against whatever PostgreSQL holds
  right now.  It never starts a scrape, and while a pull is in flight it says so
  instead of pretending the figures are final.

Every number on the page is read through the SQLAlchemy ``Applicant`` model in
``orm_queries.py``, not through a psycopg cursor, as Part 8 requires.

    python app.py            # http://127.0.0.1:5000
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, flash, jsonify, redirect, render_template, url_for
from sqlalchemy.exc import SQLAlchemyError

import db_config
import orm_queries
import pull_data
from query_data import QuestionResult

PROJECT_ROOT = Path(__file__).resolve().parent

app = Flask(__name__)

# Flash messages need a signing key.  Read it from the environment when one is
# set; otherwise generate a fresh random one at start-up rather than committing a
# constant to the repository.  Nothing here survives a restart by design -- there
# are no logins, only one-shot status messages.
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)


# ----------------------------------------------------------------------
# Reading the analysis
# ----------------------------------------------------------------------
def fetch_analysis() -> Dict[str, Any]:
    """Answer every question through the ORM.

    Returns the results alongside an error message rather than raising, so a
    database that is down produces a page explaining what to do instead of a
    stack trace in the browser.
    """
    try:
        results = orm_queries.answer_all()
    except SQLAlchemyError as exc:
        return {
            "results": [],
            "error": (
                "Could not read the database at {where}. Check that PostgreSQL "
                "is running and that load_data.py has been run. ({exc})".format(
                    where=db_config.describe(), exc=type(exc).__name__
                )
            ),
        }
    return {"results": results, "error": None}


def _split_results(results: List[QuestionResult]) -> Dict[str, List[QuestionResult]]:
    """Separate the assignment's questions from my own two."""
    return {
        "required": [result for result in results if not result.original],
        "original": [result for result in results if result.original],
    }


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------
@app.route("/")
def index() -> str:
    """The analysis page.

    Re-queries PostgreSQL on every request, so this is also what the Update
    Analysis button lands on -- there is no cached copy that could go stale.
    """
    analysis = fetch_analysis()
    status = pull_data.read_status()
    running = pull_data.is_running()

    return render_template(
        "index.html",
        groups=_split_results(analysis["results"]),
        error=analysis["error"],
        status=status,
        running=running,
        database=db_config.describe(),
        total_rows=_total_rows(analysis["results"]),
    )


def _total_rows(results: List[QuestionResult]) -> Optional[str]:
    """The Fall 2026 count, shown in the page header for a sense of scale."""
    for result in results:
        if result.number == 1 and result.answer_lines:
            return result.answer_lines[0].split(":", 1)[-1].strip()
    return None


@app.route("/pull-data", methods=["POST"])
def pull_data_route():
    """Start a scrape, unless one is already running."""
    if pull_data.is_running():
        flash(
            "A data pull is already running. Wait for it to finish -- starting a "
            "second scrape would hit Grad Cafe twice over for the same results.",
            "warning",
        )
        return redirect(url_for("index"))

    command = [sys.executable, str(PROJECT_ROOT / "pull_data.py")]
    try:
        # Detached on purpose: the scrape outlives this request, and the browser
        # gets an answer immediately instead of waiting minutes for a response.
        subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        flash("Could not start the data pull: {0}".format(exc), "error")
        return redirect(url_for("index"))

    pull_data.write_status(
        state="running",
        stage="starting",
        message="Starting up -- contacting Grad Cafe...",
        started_at=None,
        finished_at=None,
        error=None,
    )
    flash(
        "Pulling new results from Grad Cafe. This page will update on its own "
        "when the pull finishes; you can keep using it meanwhile.",
        "success",
    )
    return redirect(url_for("index"))


@app.route("/update-analysis", methods=["POST"])
def update_analysis():
    """Re-query PostgreSQL. Never starts a scrape."""
    if pull_data.is_running():
        # Reads do not interfere with the running pull, so the figures are still
        # shown -- but they are interim, and saying so is better than implying
        # the pull has already landed.
        flash(
            "New data is currently being retrieved. The figures below are "
            "up to date with the database as it stands right now; they will "
            "change again once the pull finishes.",
            "warning",
        )
    else:
        flash("Analysis refreshed from the current contents of the database.", "success")
    return redirect(url_for("index"))


@app.route("/status")
def status() -> Any:
    """Current pull state, polled by the page while a scrape is running."""
    current = pull_data.read_status()
    current["running"] = pull_data.is_running()
    return jsonify(current)


if __name__ == "__main__":
    # debug=False: the reloader would run two processes, and the second would
    # re-check the pull lock on every file touch for no benefit here.
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
