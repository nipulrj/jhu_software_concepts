Grad Cafe Analytics
===================

A Flask application that scrapes publicly posted graduate admissions results
from `The Grad Cafe <https://www.thegradcafe.com/survey/>`_, loads them into
PostgreSQL, and answers eleven analytical questions about them on one page.

The page has two buttons.  **Pull Data** checks Grad Cafe for newly submitted
results and adds them to the database.  **Update Analysis** recomputes the
figures from whatever the database holds now.  Neither can run while a pull is
in flight, and the analysis is recomputed only when asked -- so "pull, then
update, then look" is a sequence you can observe rather than three reads of the
same live query.

.. rubric:: Where to start

:doc:`overview`
    Install it, configure it, run it, run its tests.

:doc:`architecture`
    What the web, ETL and database layers each do, and where the seams between
    them are.

:doc:`testing`
    The five markers, the stable selectors, the fixtures and the test doubles.

:doc:`api/index`
    Every module, from its own docstrings.

:doc:`operations`
    The busy-state policy, the uniqueness policy, and what the scraper will and
    will not do.

:doc:`troubleshooting`
    The errors you are most likely to meet, and what each one means.

.. toctree::
   :maxdepth: 2
   :caption: Contents
   :hidden:

   overview
   architecture
   api/index
   testing
   operations
   troubleshooting

At a glance
-----------

==========================  ====================================================
Web                         Flask, one page, an application factory
Data access                 psycopg 3 for the loader and the raw-SQL analyses,
                            SQLAlchemy 2.0 for the ORM analyses the page reads
Database                    PostgreSQL 18, one ``applicants`` table
ETL                         ``scrape`` |rarr| ``clean`` |rarr| standardizer
                            |rarr| ``load_data``
Tests                       489, all marked, 100% statement coverage of
                            ``src/``, under ten seconds
CI                          GitHub Actions, PostgreSQL service container
==========================  ====================================================

.. |rarr| unicode:: U+2192

Indices
-------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
