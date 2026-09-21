db_config -- where to connect
=============================

The one place that decides which database the application talks to.
``DATABASE_URL`` is applied over the ``PG*`` variables and nothing is
cached, so a test that sets it for the length of one test redirects
psycopg and SQLAlchemy together.

.. automodule:: db_config
   :members:
   :member-order: bysource
   :show-inheritance:
