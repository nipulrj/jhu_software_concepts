pull_data -- the pull pipeline
==============================

The four ETL stages, the busy flag behind them, and the detached child
process a real pull runs in.  Every stage is an argument to
:func:`pull_data.run_pipeline`, which is what lets the suite run a whole
pull against records held in memory.

.. automodule:: pull_data
   :members:
   :member-order: bysource
   :show-inheritance:
