load_data -- stage 4, the loader and the schema
===============================================

Owns the ``CREATE TABLE`` and the ``ON CONFLICT (p_id) DO UPDATE`` that
makes a second pull safe.  Takes records from memory as well as from a
file, which is why a fake scraper needs no temporary file.

.. automodule:: load_data
   :members:
   :member-order: bysource
   :show-inheritance:
