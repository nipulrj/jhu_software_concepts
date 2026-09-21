Troubleshooting
===============

The errors you are most likely to meet, what each one means, and what to do.

Setting up
----------

``ModuleNotFoundError: No module named 'db_config'``
    You are running a file from the wrong directory.  ``src/`` is a source root:
    the modules import each other by bare name, and Python puts a script's own
    directory on ``sys.path`` only when it runs that script.  Use
    ``python src/flask_app.py`` from ``module_4``, not ``python flask_app.py``
    from somewhere else.  The tests and Sphinx each add ``src/`` themselves.

``psycopg.OperationalError: connection to server ... failed``
    PostgreSQL is not running, or is not where the configuration says.  The
    error names the server it tried -- ``user@host:port/database`` -- so compare
    that with your ``.env``.  On Windows the service is called
    ``postgresql-x64-18``; start it from Services, or::

        pg_ctl -D "D:\PostgreSQL\18\data" start

``psycopg.errors.UndefinedTable: relation "applicants" does not exist``
    Nothing has been loaded yet.  The loader creates the table, so::

        python src/load_data.py --file ../module_3/module_2/llm_extend_applicant_data.json

    or just press **Pull Data** on the page.

``FATAL: database "gradcafe" does not exist``
    Create it: ``createdb gradcafe``, or
    ``psql -U postgres -c "CREATE DATABASE gradcafe"``.

``psql`` or ``createdb`` is not recognized
    The PostgreSQL ``bin`` directory is not on your ``PATH`` -- the default on
    Windows.  Either add it, or call the executable by full path::

        "D:\PostgreSQL\18\bin\psql.exe" -U postgres -c "CREATE DATABASE gradcafe"

``FATAL: password authentication failed for user "postgres"``
    ``.env`` does not hold the password you set when you installed PostgreSQL.
    It is a real credential, so it is never committed; ``.env.example`` shows
    the shape.

Running the tests
-----------------

``TEST_DATABASE_URL is not set``
    The ``db`` and ``integration`` tests need a scratch database, because they
    truncate and drop tables.  Create one and point the variable at it::

        createdb gradcafe_test

    then set ``TEST_DATABASE_URL`` in ``.env``.  The tests deliberately refuse
    to fall back to ``DATABASE_URL``.

``TEST_DATABASE_URL and DATABASE_URL both name 'gradcafe'``
    The same guard, from the other side.  Point them at two different
    databases; the whole point is that a mistyped variable cannot empty your
    real table.

``Coverage failure: total of NN is less than fail-under=100``
    Expected when you run part of the suite: ``pytest.ini`` measures all of
    ``src`` however much of it you exercised.  Add ``--no-cov`` while
    iterating::

        python -m pytest module_4/tests/test_buttons.py --no-cov

    If a *full* run reports it, the ``Missing`` column names the lines.

``No data was collected`` / every file shows 0%
    You ran pytest from inside ``module_4``.  The ``--cov=module_4/src`` path is
    relative to the working directory, so run from the repository root:
    ``python -m pytest module_4``.

A ``db`` test fails with ``UndefinedTable``
    The ``empty_database`` fixture builds the schema, so this usually means the
    test asked for ``db_connection`` rather than ``empty_database``.

The tests pass but the page is empty
    The tests use a scratch database.  An empty application database renders
    ``0`` and ``n/a`` everywhere, which is correct -- load some data.

In CI
-----

``Connection refused`` on the first database test
    The service container was reached before the server inside it was ready.
    The workflow's ``--health-cmd pg_isready`` is what prevents this; if you
    copy the job, copy the health check with it.

Dependency install takes several minutes, or fails building a wheel
    ``llama-cpp-python`` is being compiled from source.  CI installs
    ``requirements.txt`` with it filtered out -- the standardizer is an injected
    subprocess the suite always replaces, so nothing in the tests calls it.

The docs step fails on a warning
    Deliberate: the build runs with ``-W``, so a broken cross-reference or a
    module autodoc can no longer import is a failure rather than a line of log
    nobody reads.  Reproduce it locally with::

        python -m sphinx -W --keep-going -b html module_4/docs module_4/docs/_build/html

Running the application
-----------------------

The page says "Could not read the database"
    The application could not query PostgreSQL.  The banner names the server it
    tried.  The page still renders, deliberately -- an explanation beats a stack
    trace in the browser.

Pull Data does nothing
    Check ``GET /status``.  If ``running`` is ``true``, a pull is already in
    flight and the button is correctly refusing; if it is ``false``, look in the
    status ``message``, which carries the last failure.

The figures did not change after a pull
    They are not meant to.  ``/analysis`` renders a held snapshot; press
    **Update Analysis** to recompute it.  See :doc:`architecture`.

A pull finishes but the LLM columns are empty
    ``llama-cpp-python`` is not installed, or the model could not be downloaded.
    This is a supported state: the rows load without those two columns, and a
    later run fills them in without disturbing anything else.

Pull Data stays disabled after a crash
    It should not: the lock holds a process id, and a lock left by a dead
    process is cleared the next time it is read.  If it persists, delete
    ``module_4/.pull_data.lock``.

``PermissionError: [WinError 5]`` while saving scraped data
    OneDrive's sync engine or an on-access virus scanner grabbed the file
    mid-rename.  The scraper already retries this briefly, which turns the
    spurious failure back into the atomic replace it was meant to be.  Seeing it
    raised means the lock did not clear in about four seconds -- close whatever
    has the file open.
