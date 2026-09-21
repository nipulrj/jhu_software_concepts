Overview and setup
==================

What you need
-------------

* **Python 3.11 or newer.**  Developed, run and continuously tested on
  CPython 3.13, which is what ``requirements.txt`` is pinned against.
* **PostgreSQL 14 or newer.**  Developed against 18.  The Windows graphical
  installer from `postgresql.org <https://www.postgresql.org/download/>`_ is the
  least painful route; on macOS, ``brew install postgresql@18``.
* Nothing else.  The local LLM standardizer is optional -- see
  :ref:`overview:The optional standardizer` below.

Installing
----------

.. code-block:: console

   git clone git@github.com:nipulrj/jhu_software_concepts.git
   cd jhu_software_concepts/module_4
   python -m venv .venv
   .venv\Scripts\activate          # Windows
   source .venv/bin/activate       # macOS or Linux
   python -m pip install -r requirements.txt

Configuration
-------------

Every connection the application makes is described by environment variables.
No credential is committed to the repository, and none is read from anywhere
else.

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Variable
     - What it does
   * - ``DATABASE_URL``
     - The application's database, as one libpq URL:
       ``postgresql://user:password@host:5432/gradcafe``. Applied over
       everything below, so setting it redirects psycopg and SQLAlchemy
       together.
   * - ``TEST_DATABASE_URL``
     - The scratch database the ``db`` and ``integration`` tests may empty.
       **Must name a different database from** ``DATABASE_URL`` -- the fixtures
       refuse to run otherwise, because those tests truncate and drop tables.
   * - ``PGHOST``, ``PGPORT``, ``PGDATABASE``, ``PGUSER``, ``PGPASSWORD``
     - The standard libpq variables, used for whatever ``DATABASE_URL`` leaves
       out. Either form works; the URL is the one CI and the tests set.
   * - ``FLASK_SECRET_KEY``
     - Signs the Flask session cookie. Optional: without it a fresh random key
       is generated at start-up.
   * - ``PORT``
     - The port ``python src/flask_app.py`` listens on. Defaults to 5000.

On a development machine these go in ``module_4/.env``, which ``.gitignore``
keeps out of version control.  Copy ``.env.example`` and fill in the password
you chose when you installed PostgreSQL:

.. code-block:: console

   cp .env.example .env

Variables already set in the real environment always win over the file, so
``DATABASE_URL=... python -m pytest`` overrides it and a test that sets the
variable is never quietly overruled by your ``.env``.

Creating the databases
----------------------

Two of them: the application's, and a scratch one for the tests.

.. code-block:: console

   createdb gradcafe
   createdb gradcafe_test

If ``createdb`` is not on your ``PATH`` -- it is not, on a default Windows
install -- use ``psql`` from the PostgreSQL ``bin`` directory, or run:

.. code-block:: console

   psql -U postgres -c "CREATE DATABASE gradcafe"
   psql -U postgres -c "CREATE DATABASE gradcafe_test"

The ``applicants`` table is created by the loader on first use; there is no
separate migration step.

Loading some data
-----------------

The fastest way to get a populated database is to press **Pull Data** on the
running page, which scrapes the newest results.  To load the full 50,000-row
dataset collected in Module 2 instead:

.. code-block:: console

   python src/load_data.py --file ../module_3/module_2/llm_extend_applicant_data.json

That file is committed under ``module_3`` rather than duplicated here: it is
60 MB, and one copy in the repository is enough.

Running the application
-----------------------

.. code-block:: console

   python src/flask_app.py

Then open http://127.0.0.1:5000/.  ``/`` redirects to ``/analysis``, which is
the whole application.

The ``flask`` command works too, because :func:`flask_app.create_app` is an
application factory and the CLI finds it by name:

.. code-block:: console

   flask --app src/flask_app run

Running the tests
-----------------

From the **repository root**, not from ``module_4`` -- the ``--cov`` path in
``pytest.ini`` is written relative to the root:

.. code-block:: console

   python -m pytest module_4

Every test carries at least one of the five markers, so selecting all five runs
the whole suite:

.. code-block:: console

   python -m pytest module_4 -m "web or buttons or analysis or db or integration"

Both commands enforce 100% coverage of ``module_4/src`` and fail if it drops.
The full run takes under ten seconds.  See :doc:`testing` for the markers, the
fixtures and the test doubles.

Building the documentation
--------------------------

.. code-block:: console

   python -m sphinx -b html module_4/docs module_4/docs/_build/html

Open ``module_4/docs/_build/html/index.html``.  CI builds the same pages with
``-W``, so a broken cross-reference fails the build.

.. _overview-optional-standardizer:

The optional standardizer
-------------------------

The two ``llm_generated_*`` columns are filled by the local TinyLlama
standardizer vendored in ``module_4/llm_hosting/``, which the pull runs as a
subprocess.  It needs ``llama-cpp-python`` and downloads a 670 MB model on first
use.

**It is optional.**  Without it a pull still scrapes, cleans and loads; it just
leaves those two columns empty for the new rows, and the loader's ``COALESCE``
means a later run can fill them in without disturbing anything else.  The test
suite never invokes it -- the standardizer is an injected stage and the tests
pass a double -- so CI does not install it.

``llama-cpp-python`` has no prebuilt wheel for CPython 3.13 on Windows and is
compiled from source at install time, which needs CMake and the MSVC C++ build
tools.  On a machine without them, install a prebuilt wheel for your Python
version, or run that stage on Python 3.11 or 3.12.
