# Module 2 — Grad Cafe Admissions Data

**Name:** Nipul Jayasekera
**JHED ID:** _TODO — fill in before submitting_

**Module:** Module 2 — Web Scraping (EN.605.256, Modern Software Concepts in Python)
**Assignment:** Scrape, clean, and LLM-standardize Grad Cafe applicant data
**Due:** See Canvas

---

## What this produces

| File | Contents |
|---|---|
| `applicant_data.json` | 30,000 cleaned applicant records (the Part 1 deliverable) |
| `llm_extend_applicant_data.json` | The same records plus `llm-generated-program` and `llm-generated-university` |
| `screenshot.jpg` | Evidence that `robots.txt` was checked before scraping |
| `data/raw_applicant_data.json` | Verbatim scraped text, before cleaning (not committed; regenerate with `scrape.py`) |

---

## Setup

Requires **Python 3.10 or later** (developed on CPython 3.13.5, Windows 11).

```bash
cd module_2
python -m pip install -r requirements.txt
```

`llama-cpp-python` is only needed for the LLM stage. It has no prebuilt wheel for
CPython 3.13 on Windows and compiles from source, which needs CMake and the MSVC
C++ build tools. If that install fails, the scraping and cleaning stages still
run fine without it.

## Reproducing the submitted files

```bash
python scrape.py --target 30000      # ~25 min, writes data/raw_applicant_data.json
python clean.py                      # writes applicant_data.json
python expand_canon_lists.py         # grows llm_hosting's canonical lists from the data
python llm_hosting/app.py --file applicant_data.json \
    --out llm_extend_applicant_data.json --json-array --workers 8
```

`scrape.py` checkpoints its pagination cursor, so an interrupted run resumes
where it stopped rather than starting over — just run it again. Use
`--no-resume` to deliberately start from the newest result.

To regenerate the robots.txt evidence image (needs Chrome installed):

```bash
python capture_robots_evidence.py
```

---

## robots.txt compliance

**How it was checked.** `scrape.py` fetches
`https://www.thegradcafe.com/robots.txt` and parses it with
`urllib.robotparser` *before* it requests any listing page, and every URL is run
through `can_fetch()` before being fetched (`_assert_allowed`). You can see the
check on its own with:

```bash
python scrape.py --robots-only
```

`screenshot.jpg` captures the full file next to the timestamp, the HTTP status,
the scraper's user-agent, and the verdicts the code actually reached.

**What the file says.** The `User-agent: *` group allows `/`, and separately
disallows the account pages (`/signin`, `/register`, `/forgot-password`,
`/reset-password`, `/confirm-password`, `/verify-email`, `/profile`). The survey
listing (`/survey/`) and individual results (`/result/…`) are permitted. There is
no `Crawl-delay`. The scraper only ever requests `/robots.txt`, `/survey/`, and
`/result/…`, and never touches a disallowed path.

**Two parser bugs that had to be fixed to check this honestly.** Python's
`urllib.robotparser` gave the wrong answer on this particular file, and the
naive version of this check would have reported `/profile` as allowed:

1. Grad Cafe declares `User-agent: *` **twice** — once in a Cloudflare-managed
   block containing only `Allow: /`, and again lower down with the site's own
   `Disallow` rules. The robots standard merges groups sharing a user-agent, but
   `RobotFileParser` stops at the first matching group, so the second group's
   rules were never seen.
2. Even merged, `RobotFileParser` resolves conflicts by **first match in file
   order**, while the standard (RFC 9309) uses the **most specific** match. The
   blanket `Allow: /` therefore beat `Disallow: /profile`.

`_merge_robots_groups()` in `scrape.py` merges the duplicate groups and re-orders
each group's rules longest-path-first, which makes the stdlib parser's first
match *be* the most specific one. After the fix, `/profile` and `/signin`
correctly evaluate to disallowed — visible in `screenshot.jpg`.

**Other politeness measures.**

- One request per second, enforced against the clock (`_sleep_between_requests`),
  and a site-declared `Crawl-delay` would raise that if one appeared.
- The user-agent identifies the scraper and gives a contact address rather than
  impersonating a browser.
- HTTP 401/403/429 raises `ScrapingBlocked` and **stops the run**. The scraper
  does not retry through a block, rotate identities, or work around any
  restriction.
- Transient network errors retry three times with exponential backoff.
- Only publicly accessible pages are read; nothing requires a login.

---

## Approach

### 1. Scraping — `scrape.py`

**urllib only; no Selenium.** The assignment anticipated Cloudflare blocking
plain HTTP and suggested a browser-based workaround. That turned out not to be
necessary here: `https://www.thegradcafe.com/survey/` is **server-rendered HTML**
and plain `urllib` retrieves it with **HTTP 200**. Every field the assignment
asks for is present in the returned markup, so a browser would add startup cost
and a driver dependency while producing the same bytes. Selenium is therefore not
used and not in `requirements.txt`. (Chrome *is* used, but only by
`capture_robots_evidence.py` to render the robots.txt screenshot.)

**URL handling with `urllib.parse`.** `_build_survey_url()` splits the base URL,
attaches the pagination query with `urlencode`, and reassembles it with
`urlunsplit`, so query encoding is never done by string concatenation.

**Pagination is cursor-based, not page-numbered.** This was the main surprise.
`?page=N` is silently ignored — pages 1, 2, 50, 500 and 4000 all return the
identical 20 rows. The listing instead pages through an opaque `cursor` token
that appears on the rendered "Next" link, which the scraper reads with
BeautifulSoup and follows. 30,000 records is 1,500 such requests.

Because each cursor is only discoverable from the previous page, the walk is
inherently sequential and cannot be parallelized — which suits the politeness
requirement anyway. The cursor is checkpointed to `data/checkpoint.json` after
every 25 pages along with the partial results, so an interrupted run resumes
instead of re-fetching.

**Parsing.** Each applicant occupies up to three sibling `<tr>` elements: a main
row (school, program + degree, date added, decision, permalink), an optional
full-width row of badges (term, American/International, GPA, GRE, GRE V, GRE AW),
and an optional full-width row holding the comment. `_group_rows()` regroups
those siblings into one logical record, and `_parse_entry()` pulls the fields out
with BeautifulSoup plus small regexes for the badges. Badge labels are matched
longest-first so `GRE V` and `GRE AW` are not swallowed by the bare `GRE` prefix.

**Getting the decision year right.** The rendered decision badge omits the year
("Accepted on Sep 09"), so the year initially had to be inferred from the "Added
on" column. Spot-checking that inference against individual result pages showed
it was correct for typical entries but wrong by a full year for back-posted ones,
and the two cases are genuinely indistinguishable from the rendered table alone.

The listing is an Inertia.js page, so the markup already downloaded *also*
carries the server's data payload in a `data-page` attribute, and that payload
spells the notification date out in full.
`_parse_embedded_decision_dates()` reads it out of the same response, which makes
the decision year exact at no extra request cost. The visible table remains the
source for every other field; the inference is kept only as a fallback.

I verified the table parsing independently against that payload: the status
parsed from the rendered HTML matched the payload's `decision` field for 20/20
rows on a page.

### 2. Cleaning — `clean.py`

`scrape.py` stores text exactly as rendered; `clean.py` normalizes it:

- dates → ISO `YYYY-MM-DD`
- GPA and GRE AW → floats; GRE Quant/Verbal → integers
- the decision badge → a canonical status (`Accepted`, `Rejected`, `Wait listed`,
  `Interview`) plus a separate decision date, also copied into
  `acceptance_date` / `rejection_date` / `waitlist_date` / `interview_date`
- term → `term`, `start_season`, `start_year`
- HTML entities decoded and stray tags stripped
- duplicate entry IDs dropped

**Missing values are consistently `null`** — never `""`, `"N/A"`, or a missing
key. Every record carries the same keys.

**Nothing applicant-provided is altered.** Unusual values (e.g. a GPA above 4.0
on a different scale) are preserved as reported rather than clamped, and the
verbatim scraped strings are kept under each record's `raw` key so any cleaned
value can be traced back to what the page said.

### 3. LLM standardization — `llm_hosting/`

Runs the provided TinyLlama-1.1B standardizer over the cleaned data, adding
`llm-generated-program` and `llm-generated-university`. `clean.py` writes the
`program` field as `"<program>, <university>"` so it matches the input shape
`app.py` expects, and the original values are preserved alongside it.

---

## Changes made to the provided `llm_hosting` files

The bundle was committed unmodified first, so every change below is reviewable
as a diff against the original.

**Compatibility fixes (needed to run at all):**

- `hf_hub_download()` was called with `force_filename` and
  `local_dir_use_symlinks`, both removed in `huggingface_hub` 1.x — a `TypeError`
  on a current install. The call now passes only the kwargs the installed version
  advertises, so it works on old and new releases.
- The canonical lists and model cache now resolve relative to `app.py` rather
  than the caller's working directory, so the script runs from anywhere.

**Throughput (needed to finish 30k rows):**

- The original CLI called the model once per row. Most rows repeat a program
  string that was already standardized, so the CLI now standardizes each
  **distinct** string once and fans that work across worker processes
  (`--workers`), mapping answers back onto every row afterwards. Per-row output
  is unchanged. Each worker holds its own llama.cpp context, so per-instance
  threads are pinned to 1 and parallelism comes from process count.
- Added `--json-array`, since the deliverable is a JSON array rather than the
  JSON Lines the original emitted.

**Correctness — the fuzzy matcher was relabelling universities:**

This was the most consequential finding. `_best_match()` used
`difflib.get_close_matches`, which scores raw character overlap, so any name
missing from `canon_universities.txt` was rewritten to whatever looked closest.
On real scraped data that silently replaced real institutions with unrelated
ones:

| Site's name | Was rewritten to |
|---|---|
| University of Michigan | University of Milan |
| Penn State University | Kent State University |
| University of Maryland | University of Mary |

The Michigan case happened because the canonical list contains *"University of
Michigan, Ann Arbor"* but not the bare *"University of Michigan"* — which appears
in 135 rows of a 14k sample.

`_best_match()` now considers several near matches and accepts one only if it
preserves a distinctive word of the original (accent-folded, with generic words
like *university* and *state* ignored, and stems compared so
`Mathematic → Mathematics` still matches). Names that cannot be matched safely
are left alone, on the principle that **unstandardized is better than wrong**.

Measured on a 300-row random sample, scoring against the university name the site
itself renders: **7 corrupted rows before the guard, 0 after.** The legitimate
abbreviation expansions are unaffected.

**Canonical lists — `expand_canon_lists.py`:**

Grad Cafe stores each result's school and programme as foreign keys (`school_id`,
`program_id`), so the names it renders come from the site's own controlled
vocabulary rather than applicant free text. That makes the scraped data a sound
source of extra canonical entries. The script counts distinct names, keeps only
those recurring at least `--min-count` (default 3) times so a stray one-off
cannot become canonical, drops placeholders like `N/A` and `Unknown`, and appends
what is missing under a marked header. Existing entries are never reordered or
removed.

---

## Systematic edge cases and remaining imperfections

Things the standardization still gets wrong, found by comparing its output
against the university name the site itself renders:

- **The tiny model introduces typos.** TinyLlama produced *"The Whartoon
  School"* from *"The Wharton School"*. Because the mangled name matches nothing
  in the canonical list, the post-processor cannot repair it. A larger model, or
  preferring the scraped university field when the model disagrees with it, would
  fix this.
- **Qualifiers get dropped.** *"University of Texas at Austin - NWP"* becomes
  *"University of Texas at Austin"*. Usually desirable, occasionally lossy.
- **Punctuation and accents vary in the source.** The site itself contains
  *"University of Wisconsin - Madison"*, *"University of Wisconsin—Madison"*,
  *"San Jose State University"* and *"San José State University"*. Fuzzy matching
  folds most of these together, but the canonical entry that wins is whichever
  form reached the list first.
- **Casing is inconsistent upstream.** Some rows store the school lowercase
  (e.g. `university of british columbia`), because the site's own database is
  inconsistent.
- **`.title()` mangles acronyms.** The post-processor title-cases before
  matching, turning `UCLA` into `Ucla` and `MIT` into `Mit`. When the model
  already returned the correct expanded name this does no harm, but it means a
  bare acronym will not match a canonical entry.
- **Applicants edit their entries.** One record changed decision from *Rejected*
  to *Wait listed* between two fetches minutes apart, so any snapshot is
  point-in-time rather than permanently reproducible.

The pipeline is intentionally re-runnable: update the canonical lists, rerun
`app.py`, and the results converge without re-scraping.

---

## Project structure

```
module_2/
  scrape.py                    scraping logic (GradCafeScraper, save_data, load_data)
  clean.py                     cleaning logic (clean_data, and the parsers it uses)
  expand_canon_lists.py        grows the canonical lists from scraped data
  capture_robots_evidence.py   regenerates screenshot.jpg with headless Chrome
  applicant_data.json          cleaned records (deliverable)
  llm_extend_applicant_data.json   cleaned records + LLM fields (deliverable)
  screenshot.jpg               robots.txt evidence
  requirements.txt
  README.md
  llm_hosting/                 provided standardizer, plus the changes listed above
  data/                        raw scrape + checkpoint (regenerable, not committed)
```

Required entry points: `scrape_data()`, `clean_data()`, `save_data()`,
`load_data()`. Internal helpers are underscore-prefixed (`_parse_entry`,
`_parse_status`, `_group_rows`, `_merge_robots_groups`, …).

---

## Known bugs and limitations

- **JHED ID is a placeholder** in this README and needs filling in before
  submission.
- **The decision-year fallback is ambiguous by construction.** When the site
  supplies no notification date, `clean.py` infers the year from the "Added on"
  date and picks the most recent year not in the future. For an entry posted long
  after the decision this can be a year early. The exact date from the page
  payload is used whenever it is available, which on the submitted data is every
  record — the fallback is dormant but retained. The un-inferred label is always
  preserved in `raw.raw_status`.
- **GRE Quant vs a bare "GRE" badge.** The listing labels the quantitative score
  just `GRE`; it is stored as `gre_quant`. If the site ever emits a combined
  total under the same label, it would land in the same field.
- **Standardization quality is capped by the 1.1B model** — see the edge cases
  above. The guard prevents wrong answers but cannot manufacture right ones.
- **The scrape is a point-in-time snapshot.** Grad Cafe receives new results
  continuously and applicants edit existing ones, so re-running produces a
  different 30,000 rows.
