Testing guide
=============

487 tests, every one marked, 100% statement coverage of ``module_4/src``, under
ten seconds.  Nothing in the suite reaches the network, waits on a clock, or
needs a browser.

Running them
------------

From the **repository root**, because ``pytest.ini``'s ``--cov`` path is written
relative to it:

.. code-block:: console

   python -m pytest module_4

Selecting all five markers runs the same set, because no test is unmarked:

.. code-block:: console

   python -m pytest module_4 -m "web or buttons or analysis or db or integration"

Useful subsets:

.. code-block:: console

   python -m pytest module_4 -m "web or buttons"          # no database needed
   python -m pytest module_4 -m "not db" --no-cov          # fastest loop
   python -m pytest module_4/tests/test_buttons.py -q      # one file
   python -m pytest module_4 -k busy -v                    # by name

``--no-cov`` is worth adding while iterating: ``pytest.ini`` sets
``--cov-fail-under=100``, so a partial run will otherwise "fail" for having
covered only the part you ran.

The markers
-----------

.. list-table::
   :header-rows: 1
   :widths: 16 22 62

   * - Marker
     - Files
     - What it covers
   * - ``web``
     - ``test_flask_page.py``
     - The application factory and the rendered page: every required route,
       the status code, the heading, both buttons, the ``Answer:`` labels, and
       the page still rendering when the database is unreachable.
   * - ``buttons``
     - ``test_buttons.py``
     - Both ``POST`` endpoints, their JSON, the busy gating on each, and the
       error paths.
   * - ``analysis``
     - ``test_analysis_format.py``,
       ``test_query_data.py``,
       ``test_orm_queries.py``
     - Labelling and rounding: every answer line labelled, every percentage to
       exactly two decimals, on the page and on the console.
   * - ``db``
     - ``test_db_insert.py``,
       ``test_db_config.py``,
       ``test_models.py``,
       ``test_load_data.py``,
       ``test_query_data.py``,
       ``test_orm_queries.py``
     - The schema, what a pull writes, the uniqueness policy, reading rows back
       as dicts, and the configuration that decides which database is used.
   * - ``integration``
     - ``test_integration_end_to_end.py``,
       ``test_pull_data.py``,
       ``test_scrape.py``,
       ``test_clean.py``
     - The ETL flow: end to end through the web layer, and each stage on its
       own.

Some files carry two markers -- ``test_query_data.py`` is both ``analysis`` and
``db``, because its subject is analytical output computed by the database.

.. note::

   The five markers are fixed by the assignment, and the ETL stages
   (``scrape``, ``clean``, ``pull_data``) do not map neatly onto any of them.
   They are marked ``integration``: the pipeline they form is the flow the
   Pull Data button integrates, and they are tested a stage at a time.

Stable selectors
----------------

The UI tests never match on text or CSS class.  Everything they assert against
carries a ``data-testid``, so the page can be restyled without breaking them.

.. list-table::
   :header-rows: 1
   :widths: 34 66

   * - Selector
     - What it marks
   * - ``[data-testid="pull-data-btn"]``
     - The Pull Data button. ``disabled`` while a pull is in flight.
   * - ``[data-testid="update-analysis-btn"]``
     - The Update Analysis button. Same.
   * - ``[data-testid="pull-status"]``
     - The status line. Its ``data-running`` attribute is ``"true"`` or
       ``"false"``.
   * - ``[data-testid="analysis-updated-at"]``
     - When the rendered snapshot was computed. Changes when, and only when,
       Update Analysis succeeds.
   * - ``[data-testid="question-N"]``
     - One question's whole block: heading, answer, table, caveat.
   * - ``[data-testid="answer-N"]``
     - Just that question's answers. Each line begins ``Answer:``.
   * - ``[data-testid="required-analysis"]``
     - The section holding the assignment's questions.
   * - ``[data-testid="original-analysis"]``
     - The section holding my own two.
   * - ``[data-testid="analysis-error"]``
     - The banner shown when the database could not be read.

Fixtures
--------

All in ``tests/conftest.py``.

**Application** -- no database, no network, no clock.

``app`` / ``client`` / ``services``
    An application built through :func:`flask_app.create_app` with an in-memory
    busy flag, a recording pull runner and a canned analysis provider.
    ``services`` is the :class:`flask_app.Services` it was built with, so a test
    can read the state the routes act on rather than infer it from HTML.
``make_app``
    A callable returning a new application, for a test that needs to override
    one collaborator without restating the others::

        client = make_app(state=pull_data.MemoryState(busy=True)).test_client()

``state``
    The :class:`pull_data.MemoryState` behind ``app``. Call ``state.begin()`` to
    make the application busy; that is the whole of it.
``analysis_provider``
    A :class:`doubles.CountingProvider` serving ``conftest.CANNED_RESULTS`` and
    counting its calls -- which is how a refused update is proved to have
    recomputed nothing.
``pull_runner``
    A :class:`doubles.FakePullRunner` that records that it was called.

**Database** -- these need a running PostgreSQL.

``test_database_url``
    Reads ``TEST_DATABASE_URL``. Fails with an explanation if it is unset, and
    **refuses to run if it names the same database as** ``DATABASE_URL``: these
    tests truncate and drop tables, and that guard is what stops a mistyped
    variable from doing it to real rows.
``database``
    Points the whole application at that database for one test, and drops the
    ORM's cached Engines on the way in and out.
``db_connection``
    An open autocommitting connection to it.
``empty_database``
    The schema in place, no rows. Built with
    :func:`load_data.create_table`, so what the tests assert against is the
    schema the application ships.
``seeded_database``
    The twelve sample records loaded.

**Data**

``sample_raw_rows``
    Twelve raw scraped rows, in the shape :mod:`scrape` produces.
``overlapping_raw_rows``
    A second batch repeating two of them -- one with an edited decision -- and
    adding two new ones.
``sample_records``
    The sample rows put through the *real* cleaner and a fake standardizer, so
    what the database tests insert is what the application would have inserted.

Test doubles
------------

All in ``tests/doubles.py``.  Each stands in for one injected collaborator.

.. list-table::
   :header-rows: 1
   :widths: 28 72

   * - Double
     - Stands in for
   * - :class:`doubles.FakeScraper`
     - The scraper. Serves batches from a list, repeating the last once
       exhausted -- which is the realistic case, since consecutive real pulls
       overlap heavily. Counts its calls.
   * - :func:`doubles.fake_standardizer`
     - The local LLM. Fills the two ``llm-generated-*`` columns by splitting
       the combined program field, and returns a **new** list, because
       :func:`pull_data.run_pipeline` reads identity to tell a standardizer
       that ran from one that declined.
   * - :class:`doubles.RecordingLoader`
     - The loader. Records what it was handed and writes nothing.
   * - :class:`doubles.FailingLoader`
     - The loader, raising :class:`doubles.LoaderFailure`. The error-path
       double.
   * - :class:`doubles.FakePullRunner`
     - What a click on Pull Data does. Runs the pipeline synchronously, so by
       the time the route has answered the rows are in the database and the
       test can look.
   * - :class:`doubles.CountingProvider`
     - The analysis provider. Returns canned answers and counts its calls.

Two regular expressions live there too.  The formatting tests pull every
percentage-looking token out of the rendered page with the loose
:data:`doubles.PERCENT_PATTERN`, then require each to match the strict
:data:`doubles.TWO_DECIMAL_PERCENT`.  Matching loosely first is what makes the
test able to fail -- a pattern that only matched two-decimal percentages would
pass happily on a page full of one-decimal ones -- and a negative test feeds the
page ``39.3%`` to prove it.

No sleeps, and no network
-------------------------

**Busy state is a flag, not a race.**  "A pull is in progress" is
``state.begin()``, and a test asserts against state it set rather than a scrape
it hopes is still running.  No test in the suite calls ``sleep``.

**Nothing reaches the internet.**  The scraper touches the network in exactly
one place -- :meth:`scrape.GradCafeScraper._read_url` -- and every test replaces
either that method or the ``urlopen`` inside it.  The delays the scraper takes
between requests are patched out, which is the opposite of a test that sleeps:
nothing waits for anything.

**"Performs no update" is measured, not assumed.**  A refused
``POST /update-analysis`` is proved by the provider's call count not moving, not
by the page looking unchanged.

Coverage
--------

``pytest.ini`` sets ``--cov=module_4/src --cov-report=term-missing
--cov-fail-under=100``, so coverage is a test result rather than a number
nobody reads.  ``coverage_summary.txt`` holds the committed output.

Two things are marked ``# pragma: no cover``, both with a comment at the site
saying why:

* Every ``if __name__ == "__main__":`` guard.  Executing one means re-importing
  its module under a second name, which defines a second copy of everything in
  it -- SQLAlchemy rejects the duplicate mapping outright, and for ``scrape.py``
  it would start a real scrape.  Each ``main()`` they dispatch to is called
  directly by the tests, which is where the behaviour is.
* One unreachable guard in :func:`clean._parse_status`, kept because loosening
  either the regex above it or ``_strip_markup`` would make it reachable again.

Continuous integration
----------------------

``.github/workflows/tests.yml`` runs the same command on every push and pull
request touching ``module_4``, against a PostgreSQL 18 service container, and
then builds these pages with ``-W`` so a broken cross-reference fails the build.
``actions_success.png`` in ``module_4`` is a green run.
