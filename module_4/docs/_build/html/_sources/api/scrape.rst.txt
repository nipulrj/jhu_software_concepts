scrape -- stage 1, the scraper
==============================

Walks the public Grad Cafe survey listing, honouring ``robots.txt``,
throttling itself, and stopping rather than pushing when the site
refuses.  Its output is raw text exactly as the site rendered it;
normalizing that is :mod:`clean`'s job.

.. automodule:: scrape
   :members:
   :member-order: bysource
   :show-inheritance:
