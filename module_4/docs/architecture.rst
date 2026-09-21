Architecture
============

Three layers, and the seams between them
----------------------------------------

.. code-block:: text

    ┌─────────────────────────────────────────────────────────────────┐
    │  WEB                              flask_app.py                  │
    │                                                                 │
    │  GET  /analysis          renders the held analysis snapshot     │
    │  POST /pull-data         asks the pull_runner to start a pull   │
    │  POST /update-analysis   asks the AnalysisCache to recompute    │
    │  GET  /status            reports the busy flag and progress     │
    └───────┬──────────────────────────────────┬──────────────────────┘
            │  pull_runner                     │  analysis_provider
            │  (injected)                      │  (injected)
    ┌───────▼──────────────────────┐   ┌───────▼──────────────────────┐
    │  ETL           pull_data.py  │   │  ANALYSIS                    │
    │                              │   │                              │
    │  scrape.py    → raw rows     │   │  orm_queries.py  SQLAlchemy  │
    │  clean.py     → records      │   │  query_data.py   raw SQL     │
    │  llm_hosting/ → + 2 columns  │   │                              │
    │  load_data.py → PostgreSQL   │   │  (the page reads the ORM;    │
    │                              │   │   the two must agree)        │
    └───────┬──────────────────────┘   └───────┬──────────────────────┘
            │                                  │
    ┌───────▼──────────────────────────────────▼──────────────────────┐
    │  DATABASE      models.py (ORM mapping)  db_config.py (where)    │
    │                                                                 │
    │  PostgreSQL, one table: applicants, primary key p_id            │
    └─────────────────────────────────────────────────────────────────┘

The two dashed arrows out of the web layer are the only places the application
reaches sideways, and both are constructor parameters of
:func:`flask_app.create_app`.  That is what lets the page tests run with no
database and the pull tests run with no network: nothing is replaced by
patching, because nothing needs to be.

The web layer
-------------

:mod:`flask_app` is an application factory and nothing else.  There is no
module-level ``app`` object, so nothing is configured at import time and two
differently configured applications can exist in one process.

Its three collaborators each default to the real thing:

``state``
    A :class:`pull_data.PullState` -- the busy flag and the progress message.
``pull_runner``
    A zero-argument callable, called when **Pull Data** is pressed.
``analysis_provider``
    A zero-argument callable returning answered
    :class:`query_data.QuestionResult` objects.

They live on ``app.extensions["gradcafe"]`` as a :class:`flask_app.Services`,
reachable with :func:`flask_app.get_services`.

**The page renders a snapshot, not a live query.**
:class:`flask_app.AnalysisCache` holds the last computed answers;
``GET /analysis`` renders them, computing on first use, and only
``POST /update-analysis`` replaces them.  Eleven analytical queries over fifty
thousand rows is real work, so not repeating it on every page load is worth
having anyway -- but the reason it is a cache is that it makes **Update
Analysis** mean something.  A page that silently recomputed itself would make
the second button a no-op and "pull, then update, then look" unobservable.

Both ``POST`` routes answer JSON rather than redirecting, so the buttons are
ordinary ``fetch`` calls and the same endpoints are directly assertable from
Flask's test client.  The page's script has no logic the tests cannot reach: it
posts to the same two URLs and reads the same JSON.

.. list-table:: What the buttons answer
   :header-rows: 1
   :widths: 30 20 50

   * - Request
     - Status
     - Body
   * - ``POST /pull-data``, idle
     - 200
     - ``{"ok": true, "busy": false, "summary": {...}}``
   * - ``POST /pull-data``, busy
     - 409
     - ``{"ok": false, "busy": true, ...}``
   * - ``POST /pull-data``, stage failed
     - 500
     - ``{"ok": false, "error": "..."}``
   * - ``POST /update-analysis``, idle
     - 200
     - ``{"ok": true, "questions": 11, "updated_at": "..."}``
   * - ``POST /update-analysis``, busy
     - 409
     - ``{"ok": false, "busy": true, ...}``
   * - ``POST /update-analysis``, database down
     - 503
     - ``{"ok": false, "error": "..."}``

The ETL layer
-------------

:mod:`pull_data` orchestrates four stages, each of which is an argument to
:func:`pull_data.run_pipeline` with the real one as its default:

1. **scrape** -- :class:`scrape.GradCafeScraper` walks the newest results.  It
   reads ``robots.txt`` first, throttles itself, and stops rather than retrying
   when the site returns 401, 403 or 429.
2. **clean** -- :func:`clean.clean_data` turns the site's rendered text into
   typed fields: ISO dates, numeric scores, canonical decision labels, and one
   consistent representation of "missing".
3. **standardize** -- the vendored ``llm_hosting/app.py``, run as a subprocess,
   adds ``llm-generated-program`` and ``llm-generated-university``.  Optional;
   without it the rows still load.
4. **load** -- :func:`load_data.load_into_database` writes to PostgreSQL.

Records pass from stage to stage in memory.  ``load_into_database`` takes
``records=`` as well as ``path=``, so a fake scraper's rows reach the database
without a temporary file in between -- which is what makes the end-to-end tests
possible.

**The pull outlives the request that starts it.**  A scrape takes minutes, so
in production ``pull_runner`` is :func:`pull_data.spawn_pull`, which starts a
detached child process and returns immediately.  The child claims the busy flag
as its first act.  In the tests the runner runs the pipeline synchronously, and
the route cannot tell the difference -- which is the point of it being a
parameter.

The database layer
------------------

One table, ``applicants``, with the fifteen columns
:data:`models.REQUIRED_FIELDS` names.  ``p_id`` is the Grad Cafe result id
rather than a generated sequence, and that choice is the whole uniqueness
policy: see :doc:`operations`.

The layer is reached two ways, on purpose:

* :mod:`load_data` and :mod:`query_data` use **psycopg 3** and hand-written SQL.
  Each analytical question carries its own complete statement rather than one
  assembled from shared fragments, so what you read is exactly what ran.
* :mod:`models` and :mod:`orm_queries` use **SQLAlchemy 2.0**.  No ``text()``
  and no cursor appears anywhere in ``orm_queries`` -- every answer is composed
  from ``select()``, ``func`` and the mapped columns, and a test tokenizes the
  module to keep it that way.

The page reads through the ORM.  The two implementations are independent, which
makes their agreement worth something: ``python src/orm_queries.py --compare``
runs both and checks every answer, and an integration test does the same
against seeded rows.

:mod:`db_config` is the single place that decides *where* to connect.  Both
adapters build their connection from it, so they cannot end up talking to
different databases, and one ``DATABASE_URL`` redirects them together.

:mod:`models` builds its Engine on first use and caches one per URL.  Module 3
built it at import time, which bound the process to whichever database was
configured when the first import ran; a test could not point the ORM at a
scratch database without reloading the module.

Where the files are
-------------------

.. code-block:: text

   module_4/
   ├── src/                     application code, a source root on sys.path
   │   ├── flask_app.py         the factory and the routes
   │   ├── pull_data.py         the pipeline and the busy state
   │   ├── scrape.py            stage 1
   │   ├── clean.py             stage 2
   │   ├── load_data.py         stage 4, and the schema
   │   ├── query_data.py        the eleven analyses in raw SQL
   │   ├── orm_queries.py       the eleven analyses through the ORM
   │   ├── models.py            the mapping, the Engine, the Session
   │   ├── db_config.py         where to connect
   │   ├── templates/           base.html, index.html
   │   └── static/              style.css
   ├── tests/                   all test code
   ├── docs/                    this documentation
   ├── llm_hosting/             the vendored standardizer (not application code)
   ├── data/                    what a pull writes (gitignored)
   ├── pytest.ini
   ├── requirements.txt
   ├── coverage_summary.txt
   └── README.md

``src/`` is a source root rather than a package: the modules import each other
by bare name, which is how ``clean.py`` has always done ``from scrape import
...``.  ``tests/conftest.py`` and ``docs/conf.py`` each put it on ``sys.path``,
and nothing else is needed to import the application.

``llm_hosting/`` sits outside ``src/`` deliberately.  It is instructor-provided
code, it imports ``llama_cpp`` and a 670 MB model file, and the pull invokes it
as a subprocess -- so it is a vendored tool rather than code this module owns,
and it is not part of what the coverage requirement measures.
