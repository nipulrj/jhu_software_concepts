# Module 4 — Grad Cafe Analytics: tests, coverage and documentation

Nipul Jayasekera · JHU EN.605.256 Modern Software Concepts in Python

A Flask application that scrapes publicly posted graduate admissions results
from [The Grad Cafe](https://www.thegradcafe.com/survey/), loads them into
PostgreSQL, and answers eleven analytical questions about them on one page.
Module 4 is the same application made testable, tested, and documented.

**Documentation:** https://jhu-software-concepts.readthedocs.io/
(also committed under [`docs/_build/html/`](docs/_build/html/index.html))

| | |
|---|---|
| Tests | **487**, every one marked, in under 10 seconds |
| Coverage | **100%** of `module_4/src` — see [`coverage_summary.txt`](coverage_summary.txt) |
| CI | [`.github/workflows/tests.yml`](../.github/workflows/tests.yml) — see [`actions_success.png`](actions_success.png) |
| Repository | `git@github.com:nipulrj/jhu_software_concepts.git` |

---

## What changed from Module 3

Module 3 worked. It was not testable: it configured itself at import time, its
scrape was a subprocess nothing could stand in for, and its busy flag was a
file on disk. Six changes, each forced by something a test needs to be able to
do:

- **`create_app(config, state, pull_runner, analysis_provider)`.** No
  module-level `app` object, so nothing is configured at import and two
  differently configured applications can coexist in one test session. The
  three collaborators live on `app.extensions["gradcafe"]`.
- **`DATABASE_URL` is first-class.** It is applied over the `PG*` variables,
  which fill in whatever it leaves out. Passing it to `create_app` exports it
  and clears the ORM's Engine cache, so one value redirects psycopg and
  SQLAlchemy together.
- **The ORM Engine is built on first use**, cached per URL, with
  `reset_engine()` to drop it. Module 3 built it at import, which bound the
  process to whichever database was configured when the first import ran.
- **Every ETL stage is a parameter.** `run_pipeline(scraper=, cleaner=,
  standardizer=, loader=)` defaults to the real ones, so the suite runs a whole
  pull — including the write to PostgreSQL — against a fake scraper serving
  records from memory.
- **Busy state is an object.** `FileState` for the running application, where
  the pull is a separate process and the flag must survive a Flask restart;
  `MemoryState` for a pull that runs in-process, so "a pull is in progress" is a
  flag a test sets rather than a scrape it waits for.
- **The buttons POST to JSON endpoints** — `200 {"ok": true}`,
  `409 {"busy": true}` — instead of redirecting, and both carry
  `data-testid` attributes.

One behavioural change: `/analysis` renders a **held snapshot** that only
**Update Analysis** replaces. That is what gives the second button something to
do, and what makes "pull → update → render" an observable sequence rather than
three reads of the same live query.

---

## Setup

Needs Python 3.11+ (developed and CI'd on 3.13) and PostgreSQL 14+ (developed
against 18).

```bash
git clone git@github.com:nipulrj/jhu_software_concepts.git
cd jhu_software_concepts/module_4
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
```

### Configure PostgreSQL

Create two databases — the application's, and a scratch one the tests are
allowed to empty:

```bash
createdb gradcafe
createdb gradcafe_test
```

If `createdb` is not on your `PATH` (it is not, on a default Windows install),
use `psql -U postgres -c "CREATE DATABASE gradcafe"` from the PostgreSQL `bin`
directory.

Copy `.env.example` to `.env` and fill in the password you chose when you
installed PostgreSQL. `.env` is gitignored and no credential is ever committed.

```ini
DATABASE_URL=postgresql://postgres:yourpassword@localhost:5432/gradcafe
TEST_DATABASE_URL=postgresql://postgres:yourpassword@localhost:5432/gradcafe_test
```

| Variable | What it does |
|---|---|
| `DATABASE_URL` | The application's database. Applied over everything below. |
| `TEST_DATABASE_URL` | The scratch database the `db` and `integration` tests may empty. **Must name a different database** — those tests truncate and drop tables, and the fixtures refuse to run otherwise. |
| `PGHOST` `PGPORT` `PGDATABASE` `PGUSER` `PGPASSWORD` | The standard libpq variables, for whatever `DATABASE_URL` leaves out. |
| `FLASK_SECRET_KEY` | Signs the session cookie. Optional; a random one is generated at start-up. |
| `PORT` | The port the app listens on. Defaults to 5000. |

The `applicants` table is created by the loader on first use — there is no
separate migration step.

---

## Run the app

```bash
python src/flask_app.py
```

Then open <http://127.0.0.1:5000/>. `/` redirects to `/analysis`, which is the
whole application. `flask --app src/flask_app run` works too, because
`create_app` is an application factory.

To load the full 50,000-row Module 2 dataset instead of pulling:

```bash
python src/load_data.py --file ../module_3/module_2/llm_extend_applicant_data.json
```

That file lives under `module_3` rather than being duplicated here: it is
60 MB, and one copy in the repository is enough.

### The command-line tools

```bash
python src/query_data.py --sql        # the eleven analyses in raw SQL
python src/orm_queries.py --compare   # check the ORM's answers against them
python src/pull_data.py --target 500  # a pull, without the web app
python src/models.py                  # check the mapping against the database
```

---

## Run the tests

From the **repository root**, because `pytest.ini`'s `--cov` path is relative
to it:

```bash
python -m pytest module_4
```

Every test carries at least one of the five markers, so selecting all five runs
the whole suite:

```bash
python -m pytest module_4 -m "web or buttons or analysis or db or integration"
```

Both enforce 100% coverage of `module_4/src` and fail if it drops.

| Marker | Covers |
|---|---|
| `web` | The factory and the rendered page |
| `buttons` | Both POST endpoints, busy gating, error paths |
| `analysis` | `Answer:` labels and two-decimal percentages |
| `db` | Schema, inserts, uniqueness, reading rows back |
| `integration` | The ETL flow, end to end and stage by stage |

Useful while iterating:

```bash
python -m pytest module_4 -m "web or buttons" --no-cov   # no database needed
python -m pytest module_4 -k busy -v                     # by name
```

`--no-cov` matters for partial runs: `pytest.ini` measures all of `src` however
much of it you exercised, so a subset will otherwise "fail" on coverage.

**Nothing in the suite reaches the network, waits on a clock, or needs a
browser.** The scraper touches the network in exactly one place, and every test
replaces it. Busy state is a flag a test sets. Every request goes through
Flask's test client.

The [testing guide](https://jhu-software-concepts.readthedocs.io/en/latest/testing.html)
has the fixtures, the test doubles and the stable selectors.

---

## View the documentation

Published at <https://jhu-software-concepts.readthedocs.io/>, and built from
`docs/`:

```bash
make -C docs html          # Windows: docs\make.bat html
make -C docs strict        # the way CI builds it: warnings are errors
```

Open `docs/_build/html/index.html`. A built copy is committed as the
deliverable.

The pages are: **Overview and setup**, **Architecture** (the web, ETL and
database layers and the seams between them), **API reference** (autodoc for
every module under `src/`), **Testing guide** (markers, selectors, fixtures,
doubles), **Operational notes** (busy-state policy, uniqueness policy, scraping
policy) and **Troubleshooting**.

---

## Layout

```
module_4/
├── src/                     application code, a source root on sys.path
│   ├── flask_app.py         the factory and the routes
│   ├── pull_data.py         the pipeline and the busy state
│   ├── scrape.py            ETL stage 1
│   ├── clean.py             ETL stage 2
│   ├── load_data.py         ETL stage 4, and the schema
│   ├── query_data.py        the eleven analyses in raw SQL
│   ├── orm_queries.py       the eleven analyses through the ORM
│   ├── models.py            the mapping, the Engine, the Session
│   ├── db_config.py         where to connect
│   ├── templates/           base.html, index.html
│   └── static/              style.css
├── tests/                   all test code
│   ├── conftest.py          fixtures
│   ├── doubles.py           test doubles and sample data
│   ├── test_flask_page.py       web
│   ├── test_buttons.py          buttons
│   ├── test_analysis_format.py  analysis
│   ├── test_db_insert.py        db
│   ├── test_integration_end_to_end.py  integration
│   └── test_{scrape,clean,pull_data,load_data,models,query_data,orm_queries,db_config}.py
├── docs/                    Sphinx source, and _build/html
├── llm_hosting/             the vendored standardizer (not application code)
├── pytest.ini
├── requirements.txt
├── coverage_summary.txt
└── actions_success.png
```

`src/` is a source root rather than a package: the modules import each other by
bare name, which is how `clean.py` has always done `from scrape import ...`.
`tests/conftest.py` and `docs/conf.py` each put it on `sys.path`.

`llm_hosting/` sits outside `src/` deliberately. It is the instructor-provided
standardizer, it imports `llama_cpp` and a 670 MB model file, and the pull
invokes it as a subprocess — so it is a vendored tool rather than code this
module owns, and it is not part of what the coverage requirement measures.

`.github/workflows/tests.yml` is at the repository root because GitHub reads
workflows only from there. Everything it runs is in `module_4`.

---

## The optional standardizer

The two `llm_generated_*` columns are filled by the local TinyLlama
standardizer in `llm_hosting/`, which the pull runs as a subprocess. It needs
`llama-cpp-python` and downloads a 670 MB model on first use.

**It is optional.** Without it a pull still scrapes, cleans and loads; it just
leaves those two columns empty for the new rows, and the loader's `COALESCE`
means a later run fills them in without disturbing anything else. The test
suite never invokes it — the standardizer is an injected stage and the tests
pass a double — so CI does not install it.
