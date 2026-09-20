"""Flask front end for the Grad Cafe analysis.

One page with two buttons, and an application factory so that a test can build
its own copy of it:

* **Pull Data** checks Grad Cafe for newly submitted results and adds them to
  PostgreSQL.  It refuses to start a second pull while the first is still going.
* **Update Analysis** recomputes the figures from whatever PostgreSQL holds right
  now.  It never starts a scrape, and while a pull is in flight it refuses --
  reporting that rather than publishing half-loaded figures as final.

Every number on the page is read through the SQLAlchemy ``Applicant`` model in
:mod:`orm_queries`, not through a psycopg cursor.

Routes
------
========================  ======================================================
``GET  /``                redirect to ``/analysis``
``GET  /analysis``        the page
``POST /pull-data``       start a pull; ``200 {"ok": true}``, ``409`` if busy
``POST /update-analysis``  recompute; ``200 {"ok": true}``, ``409`` if busy
``GET  /status``          the pull's progress, polled by the page
========================  ======================================================

The two ``POST`` routes answer with JSON rather than a redirect, so the buttons
are ordinary ``fetch`` calls and the same endpoints are directly assertable from
Flask's test client.

What the tests replace
----------------------
:func:`create_app` takes three collaborators, each defaulting to the real thing:

``state``
    the busy flag and progress -- a :class:`pull_data.MemoryState` in tests, so
    "a pull is in progress" is a flag that is set, never a scrape waited on.
``pull_runner``
    what a click on Pull Data actually does. In production it spawns a detached
    child process; in tests it runs the pipeline synchronously with a fake
    scraper, which is how a test can assert the rows landed in the database.
``analysis_provider``
    what answers the questions. Injecting it lets the page-rendering tests run
    with no database at all.

    python flask_app.py            # http://127.0.0.1:5000
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

from flask import Flask, jsonify, redirect, render_template, url_for
from sqlalchemy.exc import SQLAlchemyError

import db_config
import models
import orm_queries
import pull_data
from query_data import QuestionResult

#: Where :meth:`Flask.extensions` keeps this application's collaborators.
EXTENSION_KEY = "gradcafe"

#: What the page says before anything has been computed.
NEVER_UPDATED = "not yet computed"


def _now() -> str:
    """The current UTC time, to the second, as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------------
# The analysis the page renders
# ----------------------------------------------------------------------
def default_analysis_provider() -> List[QuestionResult]:
    """Answer every question through the ORM.

    The default collaborator behind :class:`AnalysisCache`.  Separated out so a
    test can pass its own and render the page without a database.
    """
    return orm_queries.answer_all()


class AnalysisCache:
    """The answers the page shows, recomputed only when asked.

    This is what gives **Update Analysis** something to do.  ``GET /analysis``
    renders the held snapshot -- computing one on first use -- and only
    ``POST /update-analysis`` replaces it.  Eleven analytical queries over fifty
    thousand rows is real work, so not repeating it on every page load is worth
    having anyway; but the reason it is a cache and not a live read is that
    "pull, then update, then look" is the workflow the two buttons describe, and
    a page that silently recomputed itself would make the second button a no-op.

    :param provider: a callable returning answered questions. Anything it raises
        that is a :class:`sqlalchemy.exc.SQLAlchemyError` becomes the snapshot's
        ``error`` instead of a stack trace in the browser.
    """

    def __init__(self, provider: Callable[[], List[QuestionResult]]) -> None:
        self._provider = provider
        self._snapshot: Optional[Dict[str, Any]] = None
        #: How many times the provider has actually been called. The busy-state
        #: tests assert on this: a refused update must not move it.
        self.computations = 0

    @property
    def is_empty(self) -> bool:
        """Has anything been computed yet?"""
        return self._snapshot is None

    def snapshot(self) -> Dict[str, Any]:
        """The held answers, computing them if there are none yet."""
        if self._snapshot is None:
            return self.refresh()
        return self._snapshot

    def refresh(self) -> Dict[str, Any]:
        """Recompute the answers and hold the result.

        :returns: ``{"results": [...], "error": None | str, "updated_at": str}``
            -- an error message rather than an exception, so a database that is
            down produces a page explaining what to do.
        """
        self.computations += 1
        try:
            results = self._provider()
        except SQLAlchemyError as exc:
            snapshot: Dict[str, Any] = {
                "results": [],
                "error": (
                    "Could not read the database at {where}. Check that "
                    "PostgreSQL is running and that load_data.py has been run. "
                    "({exc})".format(where=db_config.describe(), exc=type(exc).__name__)
                ),
                "updated_at": _now(),
            }
        else:
            snapshot = {"results": results, "error": None, "updated_at": _now()}

        self._snapshot = snapshot
        return snapshot


@dataclass
class Services:
    """The collaborators one application instance was built with.

    Reachable as ``app.extensions["gradcafe"]`` -- see :func:`get_services` --
    so a test can read the busy flag and the refresh count that the routes act
    on, rather than inferring them from the rendered page.
    """

    state: pull_data.PullState
    pull_runner: Callable[[], Any]
    analysis: AnalysisCache
    #: Summaries of the pulls this instance has run, newest last.
    pulls: List[Dict[str, Any]] = field(default_factory=list)


def get_services(app: Flask) -> Services:
    """The :class:`Services` an application was built with."""
    return app.extensions[EXTENSION_KEY]


def split_results(results: List[QuestionResult]) -> Dict[str, List[QuestionResult]]:
    """Separate the assignment's questions from the two of my own."""
    return {
        "required": [result for result in results if not result.original],
        "original": [result for result in results if result.original],
    }


def total_rows(results: List[QuestionResult]) -> Optional[str]:
    """The Fall 2026 count, shown in the page header for a sense of scale.

    Read out of Question 1's own answer rather than queried again, so the header
    and the answer below it can never disagree.
    """
    for result in results:
        if result.number == 1 and result.answer_lines:
            return result.answer_lines[0].split(":", 1)[-1].strip()
    return None


# ----------------------------------------------------------------------
# The application factory
# ----------------------------------------------------------------------
def create_app(
    config: Optional[Mapping[str, Any]] = None,
    state: Optional[pull_data.PullState] = None,
    pull_runner: Optional[Callable[[], Any]] = None,
    analysis_provider: Optional[Callable[[], List[QuestionResult]]] = None,
) -> Flask:
    """Build a configured application.

    :param config: values to put into ``app.config``. ``DATABASE_URL`` is
        special-cased: it is also exported to the environment and the ORM's
        Engine cache is cleared, so passing it here redirects every connection
        the application makes -- psycopg and SQLAlchemy alike. That is the one
        knob a test needs to point the whole app at a scratch database.
    :param state: the busy flag and progress store. Defaults to the file-backed
        one, which is what lets the flag survive the detached pull process.
    :param pull_runner: what Pull Data does, called with no arguments. Defaults
        to spawning that detached process.
    :param analysis_provider: what answers the questions. Defaults to the ORM.
    :returns: the application, with its collaborators on
        ``app.extensions["gradcafe"]``.
    """
    app = Flask(__name__)

    # Flask needs a signing key for its session cookie. Read it from the
    # environment when one is set; otherwise generate a fresh random one at
    # start-up rather than committing a constant to the repository.
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

    if config:
        app.config.update(config)
        url = config.get("DATABASE_URL")
        if url:
            os.environ["DATABASE_URL"] = str(url)
            models.reset_engine()

    state = state if state is not None else pull_data.default_state()
    services = Services(
        state=state,
        pull_runner=pull_runner if pull_runner is not None else _default_pull_runner(state),
        analysis=AnalysisCache(
            analysis_provider if analysis_provider is not None else default_analysis_provider
        ),
    )
    app.extensions[EXTENSION_KEY] = services

    _register_routes(app, services)
    return app


def _default_pull_runner(state: pull_data.PullState) -> Callable[[], Any]:
    """The production runner: start a detached child process and return."""

    def run() -> Dict[str, Any]:
        return pull_data.spawn_pull(state=state)

    return run


def _register_routes(app: Flask, services: Services) -> None:
    """Attach every route to ``app``.

    A function rather than decorators at module level, because the routes close
    over the ``services`` this particular application was built with -- which is
    what makes two differently configured applications able to coexist in one
    test session.
    """

    @app.get("/")
    def home():
        """Send visitors to the analysis page, which is the whole application."""
        return redirect(url_for("analysis"))

    @app.get("/analysis")
    def analysis():
        """The analysis page, rendered from the held snapshot."""
        snapshot = services.analysis.snapshot()
        results = snapshot["results"]
        return render_template(
            "index.html",
            groups=split_results(results),
            error=snapshot["error"],
            updated_at=snapshot["updated_at"],
            status=services.state.read(),
            running=services.state.is_busy(),
            database=db_config.describe(),
            total_rows=total_rows(results),
        )

    @app.post("/pull-data")
    def pull_data_route():
        """Start a pull, unless one is already running.

        ``200 {"ok": true}`` when the pull was started, ``409 {"busy": true}``
        when one was already in flight -- in which case nothing is started, so a
        double click cannot scrape Grad Cafe twice for the same results.
        """
        if services.state.is_busy():
            return _busy_response(services)

        try:
            result = services.pull_runner()
        except pull_data.PullInProgress:
            # The runner itself found the flag taken -- the narrow race between
            # the check above and claiming it. Same answer, same reason.
            return _busy_response(services)
        except Exception as exc:  # a stage failed; nothing was committed
            return (
                jsonify(
                    {
                        "ok": False,
                        "busy": False,
                        "error": "{0}: {1}".format(type(exc).__name__, exc),
                        "message": "The data pull failed: {0}".format(exc),
                        "status": services.state.snapshot(),
                    }
                ),
                500,
            )

        summary = result if isinstance(result, dict) else {}
        services.pulls.append(summary)
        return (
            jsonify(
                {
                    "ok": True,
                    "busy": False,
                    "summary": summary,
                    "status": services.state.snapshot(),
                }
            ),
            200,
        )

    @app.post("/update-analysis")
    def update_analysis():
        """Recompute the analysis.  Never starts a scrape.

        ``200 {"ok": true}`` when it ran, ``409 {"busy": true}`` while a pull is
        in flight -- and in that case nothing is recomputed, because figures
        drawn from a half-loaded table would be reported as final.
        """
        if services.state.is_busy():
            return _busy_response(services)

        snapshot = services.analysis.refresh()
        if snapshot["error"]:
            return (
                jsonify(
                    {
                        "ok": False,
                        "busy": False,
                        "error": snapshot["error"],
                        "message": snapshot["error"],
                    }
                ),
                503,
            )

        return (
            jsonify(
                {
                    "ok": True,
                    "busy": False,
                    "questions": len(snapshot["results"]),
                    "updated_at": snapshot["updated_at"],
                    "message": "Analysis refreshed from the current contents of "
                    "the database.",
                }
            ),
            200,
        )

    @app.get("/status")
    def status():
        """The pull's progress, polled by the page while a pull is running."""
        payload = services.state.snapshot()
        payload["analysis_updated_at"] = (
            NEVER_UPDATED
            if services.analysis.is_empty
            else services.analysis.snapshot()["updated_at"]
        )
        return jsonify(payload)


def _busy_response(services: Services):
    """The shared ``409`` for both buttons while a pull is in flight."""
    return (
        jsonify(
            {
                "ok": False,
                "busy": True,
                "message": "A data pull is already running. Wait for it to "
                "finish -- the figures will change again once it does.",
                "status": services.state.snapshot(),
            }
        ),
        409,
    )


def main() -> None:
    """Serve the application on the configured port.

    There is no module-level ``app`` object: the served application comes out of
    :func:`create_app` like every other one, so there is a single code path and
    nothing is configured at import time.  ``flask --app flask_app run`` finds
    the factory by name and works the same way.
    """
    # debug=False: the reloader would run two processes, and the second would
    # re-check the pull lock on every file touch for no benefit here.
    create_app().run(
        host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False
    )


if __name__ == "__main__":
    main()
