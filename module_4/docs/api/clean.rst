clean -- stage 2, the cleaner
=============================

Turns raw scraped text into typed fields: ISO dates, numeric scores,
canonical decision labels, and one consistent representation of a
missing value.  The original strings are kept under each record's
``raw`` key, so any cleaned value can be traced back to what the page
actually said.

.. automodule:: clean
   :members:
   :member-order: bysource
   :show-inheritance:
