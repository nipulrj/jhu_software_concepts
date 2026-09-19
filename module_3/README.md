# Module 3 — Grad Cafe Data Analysis with PostgreSQL, SQL, SQLAlchemy and Flask

**Name:** Nipul Jayasekera
**JHED ID:** njayase1

**Module:** Module 3 — Databases (EN.605.256, Modern Software Concepts in Python)
**Assignment:** Load the Module 2 data into PostgreSQL, analyze it with SQL and
with the SQLAlchemy ORM, and serve the results from a dynamic Flask page.

---

## What this produces

| File | Contents |
|---|---|
| `load_data.py` | Creates the `applicants` table and loads the cleaned Module 2 data with `psycopg` |
| `query_data.py` | All eleven analysis questions, answered in raw SQL |
| `models.py` | SQLAlchemy `Applicant` model, `Engine` and `Session` |
| `orm_queries.py` | The same eleven questions, answered through the ORM |
| `app.py` | Flask application: the analysis page, **Pull Data** and **Update Analysis** |
| `pull_data.py` | The scrape → clean → standardize → load pipeline the button runs |
| `query_results.pdf` | Every question with its result, its SQL and an explanation |
| `limitations.pdf` | Two paragraphs on what self-reported data can and cannot support |
| `screenshots/` | Raw SQL output, ORM output, and the running web page |

---

## Setup

Requires **Python 3.10 or later** (developed on CPython 3.13.5, Windows 11) and
**PostgreSQL 16 or later** (developed against PostgreSQL 18.6).

### 1. Install PostgreSQL

Download and run the **Windows graphical installer** from
<https://www.postgresql.org/download/> — do not build from source. Accept the
defaults, keep port `5432`, and choose a password for the `postgres` superuser
when the wizard asks. Keep *Command Line Tools* selected so you get `psql`.

On macOS or Linux the packaged builds work the same way; on Debian/Ubuntu you
may need `sudo service postgresql start` afterwards.

### 2. Install the Python dependencies

```bash
cd module_3
python -m pip install -r requirements.txt
```

### 3. Create the database

```bash
createdb -U postgres gradcafe
```

If `createdb` is not on your PATH (the Windows installer does not add it), use
the full path to the `bin` directory you installed into — for example
`"C:\Program Files\PostgreSQL\18\bin\createdb" -U postgres gradcafe` — or create
it from `psql`:

```bash
psql -U postgres -c "CREATE DATABASE gradcafe"
```

### 4. Point the code at it

```bash
cp .env.example .env
```

Then edit `.env` and replace `replace-me` with the password you chose in step 1.

**`.env` is listed in `.gitignore` and must never be committed.** It is the only
place the credential lives; `db_config.py` reads it, and `load_data.py`,
`query_data.py`, `models.py` and the Flask app all connect through that one
module. Any of the settings can be overridden by a real environment variable
instead, which is what you would do in deployment:

```bash
PGPASSWORD=... PGDATABASE=gradcafe python load_data.py
```

### 5. Load the data

```bash
python load_data.py
```

That reads `llm_extend_applicant_data.json` (the 50,000-record Module 2
deliverable, committed here) and writes it to PostgreSQL in about two and a half
seconds.

---

## Running everything

```bash
python load_data.py              # load (or refresh) the applicants table
python query_data.py             # the eleven answers, via raw SQL
python query_data.py --sql       # ... each with the SQL that produced it
python query_data.py --questions 1-6   # ... or just some of them
python orm_queries.py            # the same answers, via SQLAlchemy
python orm_queries.py --sql      # ... each with the SQL SQLAlchemy generated
python orm_queries.py --compare  # check the two agree (they do, on all eleven)
python app.py                    # http://127.0.0.1:5000
python models.py                 # quick connectivity check
python pull_data.py --target 300 # pull new results without the web page
```

`--questions` takes `1-6`, `1,4,5` or a mix, which is useful for re-running one
question while working on it.

`orm_queries.py --compare` is worth running first if anything looks wrong: it
answers every question both ways and reports any disagreement, which catches a
mistake in either implementation.

---

## Database schema

One table, exactly as the assignment specifies:

| Column | Type | Source field in the Module 2 JSON |
|---|---|---|
| `p_id` | `integer` **primary key** | `entry_id` |
| `program` | `text` | `program` (`"<program>, <university>"`) |
| `comments` | `text` | `comments` |
| `date_added` | `date` | `date_added` |
| `url` | `text` | `url` |
| `status` | `text` | `applicant_status` |
| `term` | `text` | `term` |
| `us_or_international` | `text` | `applicant_type` |
| `gpa` | `double precision` | `gpa` |
| `gre` | `double precision` | `gre_quant` |
| `gre_v` | `double precision` | `gre_verbal` |
| `gre_aw` | `double precision` | `gre_aw` |
| `degree` | `text` | `degree` |
| `llm_generated_program` | `text` | `llm-generated-program` |
| `llm_generated_university` | `text` | `llm-generated-university` |

**`p_id` is the Grad Cafe result id, not a generated sequence.** That single
choice is what makes the loader safe to re-run: every insert carries
`ON CONFLICT (p_id)`, so loading the same file twice updates rows instead of
duplicating them. Verified — a second `python load_data.py` reports
`0 new, 50,000 already present` and the table still holds exactly 50,000 rows.

The conflict clause updates through `COALESCE(EXCLUDED.col, applicants.col)`
rather than overwriting outright, so a later scrape that happens to be missing a
field cannot blank out a value an earlier one captured — which matters most for
the two LLM columns, since a pull that skipped the standardizer would otherwise
erase them. A genuinely changed value still updates, and that matters too:
applicants edit their entries, and Module 2 caught one changing from *Rejected*
to *Wait listed* between two fetches minutes apart.

---

## Results

Run against the database as it stands: the committed 50,000-record dataset plus
the records a live Pull Data test added. `query_results.pdf` was produced from the
same state, so the two agree; a further pull would move both.

| # | Question | Answer |
|---|---|---|
| 1 | Fall 2026 applicant count | 33,208 |
| 2 | Percent international | 47.89% |
| 3 | Average GPA / GRE Q / GRE V / GRE AW | 3.77 / 261.45 / 161.14 / 9.17 |
| 4 | Average GPA, American, Fall 2026 | 3.79 |
| 5 | Fall 2025 acceptance percentage | 40.74% |
| 6 | Average GPA, accepted, Fall 2026 | 3.78 |
| 7 | JHU master's in Computer Science | 18 |
| 8 | CS PhD acceptances at the four universities (original fields) | 30 |
| 9 | Same, using the LLM fields | 30 (difference: +0) |
| 10 | *(my own)* Hardest of the 20 most-applied-to universities, and the GPA of its admits | Yale, 18.47%, avg GPA 3.82 |
| 11 | *(my own)* International vs American acceptance, and willingness to report a GPA | 35.39% vs 39.23%; 52.98% vs 64.78% disclose |

Two of these need a word of explanation, and both are in `query_results.pdf` and
on the web page as well.

### Question 3: the GRE Quantitative average is not a GRE Quantitative score

**261.45** is not a possible score on a scale that runs 130–170. It is
nonetheless the correct answer to the question as asked — the mean of every value
applicants supplied — and it is reported as such rather than quietly cleaned.

The GRE is scored on three scales: Verbal 130–170, Quantitative 130–170, and
Analytical Writing 0–6, with a **combined Verbal+Quantitative total reported on
260–340**. That last range is the whole explanation.

The Quantitative column here is **bimodal**, and the second mode falls in exactly
the combined-total band. The supporting analysis under Question 3 tests this
rather than asserting it, and both predictions hold:

| Check | Result |
|---|---|
| Impossible values (outside 130–170) | 2,316 of 3,769 reported |
| ...that land inside the official 260–340 total range | **2,264 — 97.75%** |
| ...of those, also reporting a separate verbal score | 1,675 |
| Their mean once verbal is subtracted | **165.26** |

If `gre` really held a section score, subtracting verbal from it would produce
nonsense. Instead it reconstructs a plausible quantitative score — within a point
of the 165.74 mean of the values that were never out of range at all. So
`gre = 323` beside `gre_v = 161` is not a typo; it is an applicant answering a
differently-worded question than the field label implies. Only **52 values
(1.4%)** resist the explanation, and those are obvious junk — `4.0`, `999`, `370`.

Analytical Writing is distorted the same way by placeholder values of `99.99` on
a scale that stops at 6.

### My two questions

**Question 10 — of the twenty universities applicants apply to most, which is
hardest to get into, and does a lower acceptance rate come with a stronger GPA
among those accepted?**

Yale is hardest at **18.47%**; Oxford easiest at **52.12%**. The interesting part
is the answer to the second half: **no**. Acceptance rate across the twenty spans
18.47%–52.12%, while the average GPA of those admitted spans only **3.71–4.00** —
and the *easiest* school in the set has the *highest* admitted GPA. Selectivity,
as this dataset measures it, barely shows up in the GPA of who gets in.

**Question 11 — do international applicants fare differently from American ones,
and are they equally willing to say what their GPA was?**

International applicants are accepted at **35.39%** against **39.23%** for
American ones, a 3.84-point gap. But the larger gap is in disclosure: **52.98%**
of international entries report a GPA against **64.78%** of American ones. The
GPAs they do report are near identical (3.76 vs 3.77) — so the averages agree
while the populations behind them do not, which is the whole problem with
self-reported data in one table.

Restricted to values each scale actually permits, the averages are **165.74** and
**4.33** — both entirely ordinary. Question 3 carries that comparison as
supporting analysis beneath its own answer, and `limitations.pdf` takes up what
it means.

### Question 9: the LLM fields change nothing *here*

Questions 8 and 9 both return 30, and they select the **identical 30 rows** — not
merely the same number of them, which I checked by comparing the two sets of
`p_id`s directly.

The reason is in how Grad Cafe stores its data. Each result's school is a foreign
key into the site's own controlled vocabulary rather than applicant free text, so
these four well-known universities already arrive with one full, correctly
spelled name — `Massachusetts Institute of Technology (MIT)`,
`Stanford University`, `Carnegie Mellon University`. Standardizing a name that is
already canonical cannot change which rows match.

The standardizer is not idle in general: it rewrites the university on **4,768 of
the 50,000 rows**, folding `University of Wisconsin - Madison` into
`University of Wisconsin–Madison` and `University of California (UCLA)` into
`University of California, Los Angeles`. None of that work happens to fall inside
this particular filter. A difference *would* appear for a school the site stores
inconsistently, or for a department recorded as `CS` where only the LLM column
spells out "Computer Science".

So the honest answer is that the LLM fields are worth having for grouping and for
messier institutions — Question 10 relies on them for exactly that reason — but
they are not a free accuracy gain on queries that already name unambiguous
universities.

---

## SQL versus SQLAlchemy

Question 5 — *what percentage of Fall 2025 entries are acceptances?* — answered
both ways.

**Raw SQL** (`query_data.py`):

```sql
SELECT ROUND(
           100.0 * COUNT(*) FILTER (WHERE status ILIKE 'accept%')
           / NULLIF(COUNT(*), 0),
           2
       ) AS fall_2025_acceptance_percent
FROM applicants
WHERE lower(trim(term)) = 'fall 2025';
```

**SQLAlchemy** (`orm_queries.py`):

```python
statement = (
    select(_percent(_count_where(IS_ACCEPTED), func.count()))
    .select_from(Applicant)
    .where(IS_FALL_2025)
)
value = session.scalar(statement)
```

where the two helpers and the predicate are defined once and reused across every
question in the file:

```python
IS_FALL_2025 = func.lower(func.trim(Applicant.term)) == "fall 2025"
IS_ACCEPTED  = Applicant.status.ilike("accept%")

def _percent(numerator, denominator):
    return func.round(cast(100.0 * numerator / func.nullif(denominator, 0), Numeric), 2)

def _count_where(condition):
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)
```

**Comparison.** The ORM's real advantage here is composition rather than
abstraction: `IS_FALL_2025` and `IS_ACCEPTED` are ordinary Python objects, so
Question 5 and Question 6 share the same acceptance predicate and cannot drift
apart, whereas the handwritten file repeats `status ILIKE 'accept%'` in five
separate statements and nothing but discipline keeps them identical — and the
model gives the column names a definition the editor can check, so
`Applicant.gre_v` is a typo I find at import time while `gre_verbal` in a SQL
string is one I find in a wrong answer. Writing SQL directly wins on control and
on legibility of the result: the raw version says `COUNT(*) FILTER (WHERE ...)`,
which is precisely what PostgreSQL will do, while the ORM emits a portable
`SUM(CASE WHEN ... THEN 1 ELSE 0 END)` because `FILTER` has no ORM spelling — so
the abstraction that buys portability also costs me the better PostgreSQL
idiom. The gap shows in the generated SQL, which is a single readable line by
hand and a nest of casts through SQLAlchemy:

```sql
SELECT round(CAST((100.0 * coalesce(sum(CASE WHEN (lower(applicants.status)
       LIKE lower('accept%')) THEN 1 ELSE 0 END), 0)) /
       CAST(nullif(count(*), 0) AS NUMERIC) AS NUMERIC), 2) AS round_1
FROM applicants WHERE lower(trim(applicants.term)) = 'fall 2025'
```

That is the same query and the same answer, but it is not what I would want to
read while debugging a plan, and the extra `CAST` layers are the ORM protecting
itself against backends that are not PostgreSQL. Neither approach is simply
better: I used the ORM where the logic repeats and raw SQL where the statement
itself is the deliverable, which is why `query_results.pdf` prints handwritten
SQL rather than generated SQL.

---

## The web page

```bash
python app.py     # http://127.0.0.1:5000
```

Every figure on the page is read through the SQLAlchemy `Applicant` model. The
route calls `orm_queries.answer_all()` rather than duplicating query logic inside
the view, and no psycopg cursor is involved in rendering the page.

**Pull Data** checks Grad Cafe for newly submitted results and adds them to the
database. It runs `pull_data.py` as a subprocess, so a scrape that takes minutes
never holds a web request open, and the page keeps working while it runs. The
button explains itself on the page rather than relying on the reader to guess.

**Update Analysis**, top right, re-queries PostgreSQL and redraws. It never
starts a scrape.

### How the two buttons stay out of each other's way

* A pull writes its PID to `.pull_data.lock`. A second Pull Data request finds
  the lock and refuses, with a message saying so — enforced **server side**, so
  it holds even if someone POSTs the route directly rather than clicking the
  disabled button. Verified by doing exactly that with `curl` mid-pull: the
  request redirected and no second process appeared.
* The lock records a PID rather than merely existing, so a pull killed part-way
  through cannot block the button forever — the next request notices the process
  is gone and clears it.
* While a pull is running, **Update Analysis** still shows current figures, since
  reading does not interfere with the writer, but it says plainly that new data
  is being retrieved and that the numbers will change again. Silently refusing
  would be worse than either.
* The page polls `/status` every three seconds while a pull is in flight, shows
  the stage it has reached (scraping → cleaning → standardizing → loading), and
  reloads itself once the pull finishes so the analysis reflects the new rows.
* New records cannot overwrite good data: the loader's `ON CONFLICT ... COALESCE`
  described above governs the pull exactly as it governs the initial load.

---

## Changes to the Module 2 code carried over here

`scrape.py`, `clean.py` and `llm_hosting/` are the Module 2 files. One fix was
needed in `scrape.py` for this module:

**`save_data` now retries its atomic replace.** `os.replace` is atomic, but on
Windows it raises `PermissionError` (WinError 5) if anything holds the
destination open for even a moment — OneDrive's sync engine and on-access virus
scanners both do, and this repository lives inside a OneDrive folder. A Pull Data
run failed there with the scraped data already safely written to the temporary
file, which is a spurious crash rather than a real error. `_replace_atomically`
retries six times with a short backoff. `clean.py` imports `save_data` from
`scrape.py`, so both writers are covered by the one fix.

---

## Project structure

```
module_3/
  db_config.py                 connection settings, read from the environment
  load_data.py                 creates the applicants table and loads it (psycopg)
  query_data.py                the eleven questions in raw SQL
  models.py                    SQLAlchemy Applicant model, Engine, Session
  orm_queries.py               the eleven questions through the ORM
  app.py                       Flask application
  pull_data.py                 scrape -> clean -> standardize -> load
  templates/
    base.html                  shell: masthead, footer, blocks
    index.html                 the analysis page
  static/
    style.css
  scrape.py                    Module 2 scraper
  clean.py                     Module 2 cleaner
  llm_hosting/                 Module 2 LLM standardizer
  applicant_data.json          Module 2 cleaned records
  llm_extend_applicant_data.json   the file load_data.py reads
  screenshots/
    01_raw_sql_output_q1-6.png       04_orm_output_q7-11.png
    02_raw_sql_output_q7-11.png      05_orm_vs_sql_compare.png
    03_orm_output_q1-6.png           06_flask_page.png
  github.txt
  query_results.pdf
  limitations.pdf
  requirements.txt
  .env.example                 committed; the real .env is not
  README.md
```

---

## How the PDFs and screenshots were produced

Both PDFs were written in Word and exported. Their figures come from a run of
`query_data.py` against the database as committed, so nothing in them was typed
from memory. If a Pull Data ever moves those numbers, the documents want
re-reading rather than re-rendering.

The screenshots are real screen captures of real windows, not text styled to look
like a terminal. Two details were worth solving to get them legible. The eleven
answers run longer than one console window, and Windows Terminal ignores both the
legacy console-resize API and a scroll key sent with `SendKeys` -- so the window
was sized in pixels through `MoveWindow`, and the shell made to wait before
printing, since output written into a short viewport stays where it was written.
The `--questions` selector then splits the run into batches that each fit. The
browser window was opened InPrivate: on a first run Edge signs itself in with the
Windows account and shows a sync dialog that covers the page *and* prints the
account's email address onto the screenshot.

The scripts that did this work are not in the submission. They were one-off
tooling, their output is committed, and nothing here needs them to run.

## Known limitations

* **The analysis is a point-in-time snapshot.** Grad Cafe receives new results
  continuously and applicants edit existing ones, so re-running Pull Data changes
  the answers. That is the intended behavior, not a defect, but it means the
  figures quoted above belong to the committed 50,000-row dataset.
* **Nothing validates the self-reported metrics, and this module does not add
  validation.** Question 3's supporting analysis measures the damage rather than
  repairing it, on the
  same principle Module 2 followed: preserve what the applicant reported and be
  explicit about what it is worth. Any downstream use should filter to plausible
  ranges itself.
* **Question 2's denominator excludes entries with no nationality given.** 1,224
  rows carry no classification; counting them would silently treat "did not say"
  as "not international".
* **The `term` field is the applicant's stated intake, not a verified one.**
  A handful of entries name terms that have already passed.
* **Pull Data walks only the newest results** (500 by default). It is designed to
  catch up on recent submissions, not to re-scrape the archive; use
  `scrape.py --target N` for that.
* **The LLM standardization quality is capped by a 1.1B-parameter model.** The
  known failure modes are cataloged in `../module_2/README.md` and are unchanged
  here — most relevantly, it occasionally picks a confidently wrong campus, which
  is invisible to any of the queries above because the wrong answer is itself a
  valid university name.
