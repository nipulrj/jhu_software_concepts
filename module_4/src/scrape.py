"""Scrape publicly listed graduate admissions results from The Grad Cafe.

The survey listing at https://www.thegradcafe.com/survey/ is a server-rendered
HTML table.  Every field this assignment needs is present in that markup, so the
scraper fetches pages with ``urllib`` and pulls the values out with
BeautifulSoup plus a few small regexes.

Pagination is cursor based: the site ignores ``?page=N`` and instead exposes an
opaque ``cursor`` token on the rendered "Next" link.  The scraper therefore walks
the listing one page at a time, following that link, and checkpoints the cursor
so an interrupted run can resume instead of starting over.

Output of this module is *raw* text as rendered by the site.  Normalizing it is
the job of ``clean.py``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from bs4 import BeautifulSoup

BASE_URL = "https://www.thegradcafe.com"
SURVEY_PATH = "/survey/"
ROBOTS_URL = urllib.parse.urljoin(BASE_URL, "/robots.txt")

# Identifies the scraper honestly rather than impersonating a browser, and gives
# a site administrator a contact address if they want this traffic to stop.
USER_AGENT = (
    "JHU-EN605256-CourseProject/1.0 (graduate coursework scraper; "
    "contact: nipulrj@gmail.com)"
)

DEFAULT_DELAY_SECONDS = 1.0
DEFAULT_TARGET_ENTRIES = 30_000

# As above: generated files go to module_4/data/, never into src/.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RAW_DATA_PATH = DATA_DIR / "raw_applicant_data.json"
CHECKPOINT_PATH = DATA_DIR / "checkpoint.json"

# Badge text such as "GRE V 158" or "GPA 3.57".  Longest labels are listed first
# so that "GRE V" and "GRE AW" win over the bare "GRE" prefix.
_METRIC_BADGE_RE = re.compile(
    r"^(?P<label>GRE\s+AW|GRE\s+V|GRE\s+Q|GRE|GPA)\s*(?P<value>[\d.]+)$",
    re.IGNORECASE,
)
_SEASON_BADGE_RE = re.compile(r"^(Fall|Spring|Summer|Winter)\s+\d{4}$", re.IGNORECASE)
_APPLICANT_TYPE_BADGE_RE = re.compile(r"^(American|International)$", re.IGNORECASE)
_RESULT_HREF_RE = re.compile(r"^/result/(\d+)")


class ScrapingBlocked(RuntimeError):
    """Raised when the site rejects our traffic and we must stop scraping."""


def _merge_robots_groups(robots_text: str) -> List[str]:
    """Collapse repeated ``User-agent`` groups into one group per agent.

    Grad Cafe's robots.txt declares ``User-agent: *`` twice - once in a
    Cloudflare-managed block that only says ``Allow: /``, and again further down
    with the site's own ``Disallow`` rules for the account pages.  The robots
    standard says groups sharing a user-agent are merged, but
    ``urllib.robotparser`` stops at the first matching group, which would make
    the disallowed paths look permitted.  Merging the groups first keeps the
    permission check honest.

    Within a group the rules are also re-ordered longest-path-first.  The robots
    standard resolves conflicts by the most specific match, while
    ``urllib.robotparser`` simply takes the first rule that matches; sorting by
    descending path length makes its first match *be* the most specific one, so
    ``Disallow: /profile`` correctly outranks the blanket ``Allow: /``.
    """
    rules_by_agent: Dict[str, List[Tuple[str, str]]] = {}
    other_lines: List[str] = []
    current_agents: List[str] = []
    previous_was_agent = False

    for raw_line in robots_text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue

        field, value = (part.strip() for part in line.split(":", 1))
        field_lower = field.lower()

        if field_lower == "user-agent":
            # Consecutive user-agent lines share the rules that follow them.
            if not previous_was_agent:
                current_agents = []
            current_agents.append(value)
            rules_by_agent.setdefault(value, [])
            previous_was_agent = True
            continue

        previous_was_agent = False
        if field_lower in {"allow", "disallow", "crawl-delay", "request-rate"}:
            for agent in current_agents:
                rules_by_agent[agent].append((field_lower, value))
        elif field_lower == "sitemap":
            other_lines.append(f"{field}: {value}")

    def specificity(rule: Tuple[str, str]) -> Tuple[int, int, int]:
        """Sort key: non-path directives first, then longest path, Allow winning ties."""
        field_lower, value = rule
        if field_lower not in {"allow", "disallow"}:
            return (0, 0, 0)
        return (1, -len(value), 0 if field_lower == "allow" else 1)

    merged: List[str] = []
    for agent, rules in rules_by_agent.items():
        merged.append(f"User-agent: {agent}")
        for field_lower, value in sorted(rules, key=specificity):
            merged.append(f"{field_lower}: {value}")
        merged.append("")
    merged.extend(other_lines)
    return merged


class GradCafeScraper:
    """Polite, resumable scraper for the public Grad Cafe survey listing."""

    def __init__(
        self,
        delay: float = DEFAULT_DELAY_SECONDS,
        user_agent: str = USER_AGENT,
        timeout: int = 30,
        max_retries: int = 3,
    ) -> None:
        self.delay = delay
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_retries = max_retries
        self._robots: Optional[urllib.robotparser.RobotFileParser] = None
        self._last_request_time = 0.0
        self.pages_fetched = 0

    # ------------------------------------------------------------------
    # robots.txt compliance
    # ------------------------------------------------------------------
    def check_robots(self, verbose: bool = True) -> urllib.robotparser.RobotFileParser:
        """Download and parse robots.txt before any listing page is requested."""
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(ROBOTS_URL)
        parser.parse(_merge_robots_groups(self._read_url(ROBOTS_URL)))
        self._robots = parser

        if verbose:
            print("[robots] fetched " + ROBOTS_URL, file=sys.stderr)
            print(
                "[robots] crawl-delay for our agent: "
                + str(parser.crawl_delay(self.user_agent)),
                file=sys.stderr,
            )
            for path in (SURVEY_PATH, "/result/1020480", "/profile", "/signin"):
                allowed = parser.can_fetch(
                    self.user_agent, urllib.parse.urljoin(BASE_URL, path)
                )
                print(f"[robots] can_fetch({path}) -> {allowed}", file=sys.stderr)

        # Respect a site-declared crawl delay whenever it is slower than ours.
        crawl_delay = parser.crawl_delay(self.user_agent)
        if crawl_delay is not None and float(crawl_delay) > self.delay:
            self.delay = float(crawl_delay)
            if verbose:
                print(f"[robots] raising request delay to {self.delay}s", file=sys.stderr)
        return parser

    def _assert_allowed(self, url: str) -> None:
        """Refuse to fetch a URL that robots.txt disallows for our user agent."""
        if self._robots is None:
            self.check_robots(verbose=False)
        assert self._robots is not None
        if not self._robots.can_fetch(self.user_agent, url):
            raise ScrapingBlocked("robots.txt disallows fetching " + url)

    # ------------------------------------------------------------------
    # URL construction / HTTP
    # ------------------------------------------------------------------
    def _build_survey_url(self, cursor: Optional[str] = None) -> str:
        """Assemble a survey listing URL with ``urllib.parse``."""
        parts = urllib.parse.urlsplit(urllib.parse.urljoin(BASE_URL, SURVEY_PATH))
        query = urllib.parse.urlencode({"cursor": cursor}) if cursor else ""
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, query, "")
        )

    def _sleep_between_requests(self) -> None:
        """Throttle so we never issue back-to-back requests."""
        remaining = self.delay - (time.monotonic() - self._last_request_time)
        if remaining > 0:
            time.sleep(remaining)

    def _read_url(self, url: str) -> str:
        """Single HTTP GET returning decoded text (no retry, no throttling)."""
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")

    def _fetch(self, url: str) -> str:
        """Fetch a page politely, retrying transient errors and stopping on blocks."""
        self._assert_allowed(url)

        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            self._sleep_between_requests()
            self._last_request_time = time.monotonic()
            try:
                html = self._read_url(url)
                self.pages_fetched += 1
                return html
            except urllib.error.HTTPError as exc:
                # 401/403/429 mean the site is actively refusing us: stop rather
                # than hammer it, and never try to work around the restriction.
                if exc.code in (401, 403, 429):
                    raise ScrapingBlocked(
                        f"Grad Cafe returned HTTP {exc.code} for {url}. Stopping so "
                        "we do not push against a block or rate limit."
                    ) from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc

            backoff = self.delay * (2 ** attempt)
            print(
                f"[warn] {type(last_error).__name__} on {url} "
                f"(attempt {attempt}/{self.max_retries}); retrying in {backoff:.1f}s",
                file=sys.stderr,
            )
            time.sleep(backoff)

        raise ScrapingBlocked(f"Giving up on {url}: {last_error}")

    # ------------------------------------------------------------------
    # HTML parsing
    # ------------------------------------------------------------------
    @staticmethod
    def _text(node: Any) -> str:
        """Collapse an element's visible text to a single normalized line."""
        if node is None:
            return ""
        return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()

    def _parse_page(self, html: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Return the entries on one listing page plus the next cursor token."""
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        body = table.find("tbody") if table is not None else None
        if body is None:
            return [], None

        # The visible table omits the year on the decision badge ("Accepted on
        # Sep 09"), so pull the full notification dates off the same page.
        decision_dates = self._parse_embedded_decision_dates(soup)

        entries = []
        for main_row, detail_rows in self._group_rows(body):
            entry = self._parse_entry(main_row, detail_rows)
            if entry is None:
                continue
            entry["raw_decision_date"] = decision_dates.get(entry["entry_id"], "")
            entries.append(entry)
        return entries, self._parse_next_cursor(soup)

    @staticmethod
    def _parse_embedded_decision_dates(soup: BeautifulSoup) -> Dict[Optional[int], str]:
        """Map entry id to full ``YYYY-MM-DD`` decision date for this page.

        The listing is an Inertia page: the same markup we already downloaded
        carries the server's data payload in a ``data-page`` attribute, and that
        payload spells the notification date out in full.  Reading it here keeps
        the decision year exact without a second request per result, and without
        having to guess the year from the "Added on" column.

        Returns an empty map if the attribute is missing or malformed, in which
        case ``clean.py`` falls back to inferring the year.
        """
        holder = soup.find(attrs={"data-page": True})
        if holder is None:
            return {}
        try:
            payload = json.loads(holder["data-page"])
            rows = payload["props"]["results"]["data"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return {}

        dates: Dict[Optional[int], str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            notified = row.get("date_of_notification")
            if isinstance(notified, str) and len(notified) >= 10:
                dates[row.get("id")] = notified[:10]
        return dates

    @staticmethod
    def _group_rows(tbody: Any) -> Iterator[Tuple[Any, List[Any]]]:
        """Group each result's main ``<tr>`` with its badge/comment ``<tr>``s.

        One applicant occupies up to three sibling rows: the main row carrying the
        school/program/date/decision cells, an optional full-width row of badges
        (term, applicant type, GRE/GPA), and an optional full-width row holding
        the free-text comment.
        """
        current_main: Optional[Any] = None
        current_details: List[Any] = []

        for row in tbody.find_all("tr", recursive=False):
            cells = row.find_all("td", recursive=False)
            is_detail_row = len(cells) == 1 and cells[0].has_attr("colspan")

            if is_detail_row and current_main is not None:
                current_details.append(row)
                continue

            if current_main is not None:
                yield current_main, current_details
            current_main, current_details = row, []

        if current_main is not None:
            yield current_main, current_details

    def _parse_entry(
        self, main_row: Any, detail_rows: List[Any]
    ) -> Optional[Dict[str, Any]]:
        """Extract one applicant record from its group of table rows."""
        cells = main_row.find_all("td", recursive=False)
        if len(cells) < 4:
            return None

        university = self._text(cells[0])
        program_name, degree = self._parse_program_cell(cells[1])

        # A row with neither school nor program is a layout artifact, not a result.
        if not university and not program_name:
            return None

        entry_url, entry_id = self._parse_result_link(main_row)
        entry: Dict[str, Any] = {
            "entry_id": entry_id,
            "url": entry_url,
            "raw_university": university,
            "raw_program": program_name,
            "raw_degree": degree,
            "raw_date_added": self._text(cells[2]),
            "raw_status": self._text(cells[3]),
            "raw_decision_date": "",
            "raw_term": "",
            "raw_applicant_type": "",
            "raw_gpa": "",
            "raw_gre": "",
            "raw_gre_v": "",
            "raw_gre_aw": "",
            "raw_comments": "",
        }
        for detail_row in detail_rows:
            self._apply_detail_row(entry, detail_row)
        return entry

    def _parse_program_cell(self, cell: Any) -> Tuple[str, str]:
        """Split the program cell into ``(program, degree)``.

        The cell renders as ``<span>Program</span> * <span>Degree</span>`` where
        the separator is an inline SVG bullet, so the two spans are read directly.
        """
        spans = cell.find_all("span", recursive=True)
        if len(spans) >= 2:
            return self._text(spans[0]), self._text(spans[-1])
        if len(spans) == 1:
            return self._text(spans[0]), ""
        # Fall back to the flattened cell text if the markup ever changes.
        return self._text(cell), ""

    @staticmethod
    def _parse_result_link(row: Any) -> Tuple[str, Optional[int]]:
        """Find the permalink to the individual applicant entry."""
        for anchor in row.find_all("a", href=True):
            match = _RESULT_HREF_RE.match(anchor["href"])
            if match:
                return urllib.parse.urljoin(BASE_URL, match.group(0)), int(match.group(1))
        return "", None

    def _apply_detail_row(self, entry: Dict[str, Any], row: Any) -> None:
        """Fold a badge row or a comment row into the entry being built."""
        paragraph = row.find("p")
        if paragraph is not None:
            comment = self._text(paragraph)
            if comment:
                entry["raw_comments"] = comment
            return

        for badge in row.find_all("div"):
            # Only leaf badges carry a single value; skip the flex wrapper div.
            if badge.find("div") is not None:
                continue
            self._apply_badge(entry, self._text(badge))

    @staticmethod
    def _apply_badge(entry: Dict[str, Any], text: str) -> None:
        """Route one badge's text into the right field."""
        if not text:
            return

        metric = _METRIC_BADGE_RE.match(text)
        if metric:
            label = re.sub(r"\s+", " ", metric.group("label")).upper()
            field = {
                "GPA": "raw_gpa",
                "GRE": "raw_gre",
                "GRE Q": "raw_gre",
                "GRE V": "raw_gre_v",
                "GRE AW": "raw_gre_aw",
            }.get(label)
            if field:
                entry[field] = metric.group("value")
            return

        if _SEASON_BADGE_RE.match(text):
            entry["raw_term"] = text
            return

        if _APPLICANT_TYPE_BADGE_RE.match(text):
            entry["raw_applicant_type"] = text
            return

        # The decision badge is repeated here for small screens; the main row
        # already captured it, so this only fills a gap.
        if not entry["raw_status"]:
            entry["raw_status"] = text

    @staticmethod
    def _parse_next_cursor(soup: BeautifulSoup) -> Optional[str]:
        """Read the cursor token off the rendered "Next" pagination link."""
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if "cursor=" not in href:
                continue
            if "next" not in anchor.get_text(strip=True).lower():
                continue
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
            cursor = query.get("cursor", [None])[0]
            if cursor:
                return cursor
        return None

    # ------------------------------------------------------------------
    # Driver
    # ------------------------------------------------------------------
    def scrape_data(
        self,
        target_entries: int = DEFAULT_TARGET_ENTRIES,
        resume: bool = True,
        progress_every: int = 25,
    ) -> List[Dict[str, Any]]:
        """Walk the listing until ``target_entries`` unique results are collected."""
        self.check_robots()

        entries: List[Dict[str, Any]] = []
        seen_ids: Set[int] = set()
        cursor: Optional[str] = None

        if resume:
            entries = load_data(RAW_DATA_PATH)
            seen_ids = {e["entry_id"] for e in entries if e.get("entry_id")}
            cursor = _load_checkpoint()
            if entries:
                print(
                    f"[resume] continuing from {len(entries):,} saved entries",
                    file=sys.stderr,
                )

        started = time.monotonic()
        pages_without_new_data = 0

        try:
            while len(entries) < target_entries:
                page_entries, next_cursor = self._parse_page(
                    self._fetch(self._build_survey_url(cursor))
                )

                new_entries = [
                    e
                    for e in page_entries
                    if e.get("entry_id") and e["entry_id"] not in seen_ids
                ]
                for entry in new_entries:
                    seen_ids.add(entry["entry_id"])
                entries.extend(new_entries)

                # Guard against a pagination loop spinning silently forever.
                pages_without_new_data = 0 if new_entries else pages_without_new_data + 1
                if pages_without_new_data >= 5:
                    print("[stop] five pages yielded no new entries", file=sys.stderr)
                    break

                if next_cursor is None:
                    print("[stop] reached the last page of results", file=sys.stderr)
                    break
                cursor = next_cursor

                if self.pages_fetched % progress_every == 0:
                    self._report_progress(len(entries), target_entries, started)
                    save_data(entries, RAW_DATA_PATH)
                    _save_checkpoint(cursor)
        except (KeyboardInterrupt, ScrapingBlocked) as exc:
            print(f"\n[halt] {exc}", file=sys.stderr)
        finally:
            save_data(entries, RAW_DATA_PATH)
            _save_checkpoint(cursor)
            self._report_progress(len(entries), target_entries, started)

        return entries

    def _report_progress(self, collected: int, target: int, started: float) -> None:
        """Print a one-line progress summary to stderr."""
        elapsed = time.monotonic() - started
        rate = collected / elapsed if elapsed > 0 else 0.0
        print(
            f"[progress] {collected:,}/{target:,} entries | "
            f"{self.pages_fetched:,} pages | {elapsed / 60:.1f} min | "
            f"{rate:.1f} entries/s",
            file=sys.stderr,
        )


# ----------------------------------------------------------------------
# Persistence helpers
# ----------------------------------------------------------------------
# os.replace is atomic, but on Windows it raises PermissionError (WinError 5)
# whenever anything else holds the destination open even momentarily -- OneDrive's
# sync engine and on-access virus scanners both grab a file the instant it is
# written, and this repository lives inside a OneDrive folder. The rename
# succeeds a fraction of a second later, so retrying briefly turns a spurious
# crash back into the atomic replace it was always meant to be. Added in Module 3
# after a Pull Data run died here with the scraped data already safely on disk.
_REPLACE_ATTEMPTS = 6
_REPLACE_BACKOFF_SECONDS = 0.25


def _replace_atomically(temp_path: Path, path: Path) -> None:
    """Rename ``temp_path`` over ``path``, retrying a transient Windows lock."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            temp_path.replace(path)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_BACKOFF_SECONDS * (attempt + 1))


def save_data(rows: List[Dict[str, Any]], path: Path = RAW_DATA_PATH) -> None:
    """Write rows to a JSON file, replacing the target atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)
    _replace_atomically(temp_path, path)


def load_data(path: Path = RAW_DATA_PATH) -> List[Dict[str, Any]]:
    """Load rows from a JSON file, returning ``[]`` when it does not exist."""
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, list) else []


def _save_checkpoint(cursor: Optional[str]) -> None:
    """Remember the next cursor so an interrupted run can resume."""
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CHECKPOINT_PATH.open("w", encoding="utf-8") as handle:
        json.dump({"cursor": cursor, "saved_at": time.time()}, handle)


def _load_checkpoint() -> Optional[str]:
    """Read the saved cursor, if any."""
    if not CHECKPOINT_PATH.exists():
        return None
    try:
        with CHECKPOINT_PATH.open("r", encoding="utf-8") as handle:
            return json.load(handle).get("cursor")
    except (json.JSONDecodeError, OSError):
        return None


def _read_robots_text() -> str:
    """Fetch robots.txt as plain text (used by ``--robots-only`` for evidence)."""
    return GradCafeScraper()._read_url(ROBOTS_URL)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrape public Grad Cafe admissions results."
    )
    parser.add_argument(
        "--target",
        type=int,
        default=DEFAULT_TARGET_ENTRIES,
        help="number of applicant entries to collect (default: %(default)s)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="minimum seconds between requests (default: %(default)s)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=RAW_DATA_PATH,
        help="where to write the raw scraped JSON (default: %(default)s)",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="ignore saved progress and start again from the newest result",
    )
    parser.add_argument(
        "--robots-only",
        action="store_true",
        help="print the robots.txt compliance check and exit without scraping",
    )
    args = parser.parse_args(argv)

    scraper = GradCafeScraper(delay=args.delay)

    if args.robots_only:
        scraper.check_robots()
        print(_read_robots_text())
        return 0

    entries = scraper.scrape_data(target_entries=args.target, resume=not args.no_resume)
    if args.out != RAW_DATA_PATH:
        save_data(entries, args.out)
    print(f"Collected {len(entries):,} entries -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    # Excluded from coverage rather than exercised: running this line means
    # re-executing the module under a second name, which would define a
    # second copy of everything in it. The main() it dispatches to is
    # called directly by the tests, which is where the behaviour lives.
    raise SystemExit(main())
