Operational notes
=================

The decisions a person running this needs to know about, and why each one is
the way it is.

Busy-state policy
-----------------

**One pull at a time, and the second request is told so.**

While a pull is in flight, both ``POST /pull-data`` and ``POST /update-analysis``
answer ``409`` with ``{"busy": true}`` and do nothing.  Gating the pull is
obvious -- a second scrape would ask Grad Cafe for the same results twice over.
Gating the update is the less obvious half: the figures *could* be recomputed
mid-pull, and they would be arithmetically correct, but they would be drawn from
a half-loaded table and published as final.  Refusing and saying why is more
honest than a number that quietly changes again ninety seconds later.

**Where the flag lives.**  Behind :class:`pull_data.PullState`, which has two
implementations:

:class:`pull_data.FileState`
    What the running application uses.  A file holding the pulling process's
    id, plus a small JSON status document written atomically.  It has to be a
    file rather than a variable because the pull is a **separate process**, and
    because a Flask restart must not forget that a scrape is still running.
:class:`pull_data.MemoryState`
    For a pull that runs in-process, which is how the tests run it.  The flag
    flips the instant :meth:`~pull_data.MemoryState.begin` is called.

**A crashed pull does not disable the button forever.**  The lock holds a
process id, and :func:`pull_data.running_pid` checks whether that process is
still alive before believing it; a lock left by a killed process is cleared
rather than trusted.  A lock holding nonsense is discarded the same way.

Where the answer cannot be determined -- ``tasklist`` missing on Windows, say --
:func:`pull_data.pid_is_alive` reports *alive*.  Refusing to start a second
scrape is the safer of the two errors.

**The narrow race.**  Between the route checking the flag and the child process
claiming it, there is a window in which a second request would see "not busy".
The child claims the lock as its first act, so the window is milliseconds, and
the pipeline checks again on entry: a runner that finds the flag already taken
raises :class:`pull_data.PullInProgress`, which the route turns into the same
``409``.  Being busy is not a failure, so it never becomes a ``500``.

Uniqueness and idempotency
--------------------------

**The uniqueness key is ``p_id``, the Grad Cafe result id.**

Grad Cafe gives every posted result a permanent numeric id, which the scraper
keeps as ``entry_id`` and the loader stores as the primary key.  That choice is
the whole idempotency strategy:

* Inserts carry ``ON CONFLICT (p_id) DO UPDATE``, so loading the same entry
  twice refreshes one row instead of adding a second.
* A pull walks the *newest* results, so consecutive pulls overlap heavily by
  design.  That overlap is harmless and expected.
* **Pressing Pull Data twice is safe.**  The row count does not move, and the
  pull reports ``0 inserted`` rather than claiming work it did not do.

**A refresh never replaces a known value with NULL.**  Every column in the
``DO UPDATE`` is wrapped in ``COALESCE(EXCLUDED.col, applicants.col)``.  A later
scrape of the same entry can be missing a field the first one captured -- the
two LLM columns especially, if the standardizer was skipped that run -- and
overwriting a good value with nothing would be a silent data loss.  A genuine
change still lands: an applicant who edits "Rejected" to "Wait listed" updates
the row.

**Duplicates inside one batch are collapsed first.**  PostgreSQL refuses a
statement that touches the same row twice, so ``ON CONFLICT`` cannot help there;
:func:`load_data.build_rows` de-duplicates on ``p_id`` before the insert,
keeping the last occurrence.

**A record with no result id is dropped.**  Not stored under a generated key:
an entry that cannot be recognized on a later run would be inserted again by
every subsequent pull.

Scraping policy
---------------

The scraper is written to be a well-behaved visitor, and several of its rules
cost it speed on purpose.

**It reads robots.txt first, and obeys it properly.**  Grad Cafe declares
``User-agent: *`` twice -- once in a Cloudflare-managed block that only says
``Allow: /``, and again further down with the site's own ``Disallow`` rules.
The robots standard says groups sharing a user-agent are merged, but
``urllib.robotparser`` stops at the first matching group, which would make the
disallowed paths look permitted.  :func:`scrape._merge_robots_groups` merges
them first, and re-sorts each group longest-path-first so that
``Disallow: /profile`` outranks the blanket ``Allow: /``.

**It identifies itself honestly.**  The ``User-Agent`` names the course project
and carries a contact address, rather than impersonating a browser.

**It throttles, and a site-declared crawl delay raises ours.**  One second
between requests by default; if ``robots.txt`` asks for longer, the scraper
takes the longer figure.  It never takes a shorter one.

**401, 403 and 429 stop the scrape.**  Those mean the site is actively refusing
us.  The scraper raises :class:`scrape.ScrapingBlocked` after a *single*
attempt, keeps whatever it has already collected, and makes no attempt to work
around the restriction.  Transient failures -- a 500, a dropped connection -- are
retried with exponential backoff.

**It cannot spin forever.**  Five consecutive pages yielding no new entries
stops the run, as does a page with no "Next" link.

**It checkpoints.**  The pagination cursor and the collected rows are saved
periodically and again in a ``finally`` block, so an interrupted run resumes
rather than starting over.  A pull deliberately starts *without* the checkpoint,
which is what makes it walk the newest results instead of resuming a long
scrape.

Data notes
----------

Two things about the stored data are worth knowing before quoting a number off
the page.

**Some reported values are impossible on their own scale, and are stored
anyway.**  The GRE reports Verbal and Quantitative on 130-170 each and a
combined total on 260-340; the Quantitative column here is bimodal, and the
second mode falls in exactly that combined band -- those applicants typed their
total into the box the site labels only "GRE".  Analytical Writing is inflated
the same way by placeholder values of 99.99 on a scale that stops at 6.  Nothing
is clamped or corrected on the way in.  Question 3 measures the distortion and
shows each average both with and without the out-of-range values, which is more
useful than hiding them.

**An acceptance rate computed from Grad Cafe is not an admissions rate.**  It is
the share of people who chose to post about a school and reported being
admitted, and both halves of that are self-selected.  The ordering across
schools is still informative, because every school is subject to the same
posting behaviour; the levels are not comparable to a published rate.

Both caveats are rendered beside the answers they apply to, not buried here.
