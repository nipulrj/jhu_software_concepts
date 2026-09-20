"""The scraper, with the network replaced by a page of HTML held in memory.

Marked ``integration``: the first stage of the ETL flow the pull runs.

**No test in this file makes an HTTP request.**  There is exactly one place the
scraper touches the network -- :meth:`GradCafeScraper._read_url` -- and every
test here replaces either that method or the ``urlopen`` inside it.  The delays
the scraper takes between requests are patched out too, so a run that would
take minutes against the real site takes milliseconds here.

The HTML fixtures reproduce the listing's real shape: one result spread over
three sibling rows (the main row, a row of badges, a row holding the comment),
the permalink the result id is read from, the Inertia ``data-page`` payload the
full decision dates come from, and the "Next" link carrying the pagination
cursor.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest
from bs4 import BeautifulSoup

import scrape

pytestmark = pytest.mark.integration


# ----------------------------------------------------------------------
# HTML fixtures
# ----------------------------------------------------------------------
def result_rows(
    entry_id: int = 900001,
    university: str = "Johns Hopkins University",
    program: str = "Computer Science",
    degree: str = "Masters",
    date_added: str = "Sep 11, 2026",
    status: str = "Accepted on Mar 03",
    badges: str = (
        "<div><div>Fall 2026</div><div>American</div><div>GPA 3.90</div>"
        "<div>GRE 168</div><div>GRE V 162</div><div>GRE AW 4.50</div></div>"
    ),
    comment: str = "Funded offer, very happy.",
) -> str:
    """One result as the listing renders it: a main row plus its detail rows."""
    markup = (
        "<tr>"
        "<td>{university}</td>"
        '<td><span>{program}</span><svg></svg><span>{degree}</span></td>'
        "<td>{date_added}</td>"
        "<td>{status}</td>"
        '<td><a href="/result/{entry_id}">See More</a></td>'
        "</tr>"
    ).format(
        university=university,
        program=program,
        degree=degree,
        date_added=date_added,
        status=status,
        entry_id=entry_id,
    )
    if badges:
        markup += '<tr><td colspan="5">{0}</td></tr>'.format(badges)
    if comment:
        markup += '<tr><td colspan="5"><p>{0}</p></td></tr>'.format(comment)
    return markup


def listing_page(
    rows: str = None,
    cursor: str = "next-cursor-token",
    decision_dates: dict = None,
) -> str:
    """A whole listing page: the table, the data payload and the Next link."""
    rows = result_rows() if rows is None else rows
    payload = {
        "props": {
            "results": {
                "data": [
                    {"id": entry_id, "date_of_notification": date}
                    for entry_id, date in (decision_dates or {}).items()
                ]
            }
        }
    }
    attribute = json.dumps(payload).replace('"', "&quot;")
    next_link = (
        '<a href="/survey/?cursor={0}">Next</a>'.format(cursor) if cursor else ""
    )
    return (
        '<html><body><div data-page="{payload}"></div>'
        "<table><tbody>{rows}</tbody></table>"
        "{next_link}</body></html>"
    ).format(payload=attribute, rows=rows, next_link=next_link)


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


ROBOTS_TXT = """
# Cloudflare-managed block
User-agent: *
Allow: /

Sitemap: https://www.thegradcafe.com/sitemap.xml

User-agent: *
User-agent: BadBot
Disallow: /profile
Disallow: /signin
Crawl-delay: 2
"""


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Remove every delay the scraper would take.

    The scraper is polite by design -- a second between requests, exponential
    backoff on a retry.  A test should not spend that time, and patching it out
    is not the same as a test that sleeps: nothing here waits for anything.
    """
    monkeypatch.setattr(scrape.time, "sleep", lambda _seconds: None)


@pytest.fixture
def scraper() -> scrape.GradCafeScraper:
    """A scraper with no delay and, until a test says otherwise, no transport."""
    return scrape.GradCafeScraper(delay=0.0)


@pytest.fixture
def scratch_paths(monkeypatch, tmp_path):
    """Point the scraper's output and checkpoint at a temporary directory."""
    monkeypatch.setattr(scrape, "RAW_DATA_PATH", tmp_path / "raw.json")
    monkeypatch.setattr(scrape, "CHECKPOINT_PATH", tmp_path / "checkpoint.json")
    return tmp_path


# ----------------------------------------------------------------------
# robots.txt
# ----------------------------------------------------------------------
def test_repeated_user_agent_groups_are_merged():
    """Grad Cafe declares ``User-agent: *`` twice, and both groups must apply.

    ``urllib.robotparser`` stops at the first matching group, which would make
    the site's own Disallow rules look permitted -- so the groups are merged
    before it ever sees them.
    """
    merged = scrape._merge_robots_groups(ROBOTS_TXT)

    star = merged.index("User-agent: *")
    group = merged[star + 1 : merged.index("", star)]
    assert "disallow: /profile" in group
    assert "allow: /" in group


def test_rules_are_sorted_most_specific_first():
    """The standard resolves conflicts by specificity; robotparser takes the first.

    Sorting longest-path-first makes robotparser's first match *be* the most
    specific one, so ``Disallow: /profile`` outranks the blanket ``Allow: /``.
    """
    merged = scrape._merge_robots_groups(ROBOTS_TXT)
    star = merged.index("User-agent: *")
    group = merged[star + 1 : merged.index("", star)]

    assert group.index("disallow: /profile") < group.index("allow: /")


def test_consecutive_user_agents_share_the_rules_below_them():
    """``User-agent: *`` and ``User-agent: BadBot`` on adjacent lines, one group."""
    merged = scrape._merge_robots_groups(ROBOTS_TXT)

    bad = merged.index("User-agent: BadBot")
    assert "disallow: /profile" in merged[bad + 1 : merged.index("", bad)]


def test_sitemaps_are_preserved_and_comments_dropped():
    merged = scrape._merge_robots_groups(ROBOTS_TXT)

    assert "Sitemap: https://www.thegradcafe.com/sitemap.xml" in merged
    assert not any(line.startswith("#") for line in merged)


def test_unknown_directives_are_ignored():
    """Anything that is not a rule, a sitemap or an agent is not carried over."""
    merged = scrape._merge_robots_groups(
        "User-agent: *\nDisallow: /x\nHost: example.com\nnot a directive\n"
    )

    assert "Host: example.com" not in merged
    assert merged == ["User-agent: *", "disallow: /x", ""]


def test_checking_robots_reports_what_it_found(scraper, monkeypatch, capsys):
    """The compliance check prints its evidence, which is the point of it."""
    monkeypatch.setattr(scraper, "_read_url", lambda _url: ROBOTS_TXT)

    parser = scraper.check_robots()

    printed = capsys.readouterr().err
    assert "[robots] fetched" in printed
    assert "can_fetch(/survey/) -> True" in printed
    assert "can_fetch(/profile) -> False" in printed
    assert parser.can_fetch(scraper.user_agent, scrape.BASE_URL + "/survey/") is True


def test_a_site_declared_crawl_delay_is_obeyed(scraper, monkeypatch, capsys):
    """Two seconds is slower than our one, so ours is raised to match."""
    monkeypatch.setattr(scraper, "_read_url", lambda _url: ROBOTS_TXT)
    scraper.delay = 1.0

    scraper.check_robots()

    assert scraper.delay == 2.0
    assert "raising request delay to 2.0s" in capsys.readouterr().err


def test_our_own_delay_wins_when_it_is_slower(scraper, monkeypatch):
    """Being politer than asked is allowed; being faster is not."""
    monkeypatch.setattr(scraper, "_read_url", lambda _url: ROBOTS_TXT)
    scraper.delay = 5.0

    scraper.check_robots(verbose=False)

    assert scraper.delay == 5.0


def test_a_disallowed_url_is_refused(scraper, monkeypatch):
    """The check is enforced, not merely reported."""
    monkeypatch.setattr(scraper, "_read_url", lambda _url: ROBOTS_TXT)

    with pytest.raises(scrape.ScrapingBlocked, match="robots.txt disallows"):
        scraper._assert_allowed(scrape.BASE_URL + "/profile")


def test_robots_is_fetched_before_the_first_page(scraper, monkeypatch):
    """A scraper asked to fetch without checking first checks first."""
    reads = []

    def read(url):
        reads.append(url)
        return ROBOTS_TXT

    monkeypatch.setattr(scraper, "_read_url", read)

    scraper._assert_allowed(scrape.BASE_URL + "/survey/")

    assert reads == [scrape.ROBOTS_URL]


def test_read_robots_text_returns_the_file(monkeypatch):
    """``--robots-only`` prints the file itself as evidence."""
    monkeypatch.setattr(
        scrape.GradCafeScraper, "_read_url", lambda _self, _url: ROBOTS_TXT
    )

    assert scrape._read_robots_text() == ROBOTS_TXT


# ----------------------------------------------------------------------
# URLs and HTTP
# ----------------------------------------------------------------------
def test_the_survey_url_is_built_from_parts(scraper):
    assert scraper._build_survey_url() == "https://www.thegradcafe.com/survey/"


def test_a_cursor_becomes_a_query_parameter(scraper):
    """The site ignores ``?page=N``; pagination is by opaque cursor."""
    url = scraper._build_survey_url("abc/123")

    assert url == "https://www.thegradcafe.com/survey/?cursor=abc%2F123"


def test_read_url_decodes_the_response(scraper, monkeypatch):
    """One GET, decoded with the charset the response declares."""

    class Response:
        headers = type("H", (), {"get_content_charset": lambda self: "utf-8"})()

        def read(self):
            return "Hawai‘i".encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    captured = {}

    def urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["agent"] = request.get_header("User-agent")
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    assert scraper._read_url("https://example.test/x") == "Hawai‘i"
    assert captured["url"] == "https://example.test/x"
    assert "JHU-EN605256" in captured["agent"], "the scraper identifies itself"


def test_read_url_falls_back_to_utf8(scraper, monkeypatch):
    """A response that declares no charset is decoded as UTF-8."""

    class Response:
        headers = type("H", (), {"get_content_charset": lambda self: None})()

        def read(self):
            return b"plain"

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Response())

    assert scraper._read_url("https://example.test/x") == "plain"


def test_fetching_throttles_between_requests(scraper, monkeypatch):
    """The scraper never issues back-to-back requests."""
    slept = []
    monkeypatch.setattr(scrape.time, "sleep", lambda seconds: slept.append(seconds))
    monkeypatch.setattr(scraper, "_assert_allowed", lambda _url: None)
    monkeypatch.setattr(scraper, "_read_url", lambda _url: "<html></html>")
    scraper.delay = 10.0
    scraper._last_request_time = scrape.time.monotonic()

    scraper._fetch("https://example.test/x")

    assert slept and slept[0] > 0


def test_a_successful_fetch_counts_the_page(scraper, monkeypatch):
    monkeypatch.setattr(scraper, "_assert_allowed", lambda _url: None)
    monkeypatch.setattr(scraper, "_read_url", lambda _url: "<html>ok</html>")

    assert scraper._fetch("https://example.test/x") == "<html>ok</html>"
    assert scraper.pages_fetched == 1


@pytest.mark.parametrize("code", [401, 403, 429])
def test_a_refusal_stops_the_scrape_immediately(scraper, monkeypatch, code):
    """401, 403 and 429 mean the site is refusing us.

    The scraper stops rather than retrying, and never tries to work around the
    restriction.
    """
    attempts = []

    def refuse(url):
        attempts.append(url)
        raise urllib.error.HTTPError(url, code, "no", {}, None)

    monkeypatch.setattr(scraper, "_assert_allowed", lambda _url: None)
    monkeypatch.setattr(scraper, "_read_url", refuse)

    with pytest.raises(scrape.ScrapingBlocked, match=str(code)):
        scraper._fetch("https://example.test/x")

    assert len(attempts) == 1, "a refusal must not be retried"


def test_a_transient_http_error_is_retried(scraper, monkeypatch, capsys):
    """A 500 is worth trying again; the third attempt succeeds here."""
    attempts = {"n": 0}

    def flaky(url):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise urllib.error.HTTPError(url, 500, "server error", {}, None)
        return "<html>ok</html>"

    monkeypatch.setattr(scraper, "_assert_allowed", lambda _url: None)
    monkeypatch.setattr(scraper, "_read_url", flaky)

    assert scraper._fetch("https://example.test/x") == "<html>ok</html>"
    assert attempts["n"] == 3
    assert "retrying in" in capsys.readouterr().err


def test_a_network_error_is_retried_then_given_up_on(scraper, monkeypatch):
    """After ``max_retries`` the scraper stops, saying what went wrong."""
    attempts = {"n": 0}

    def broken(_url):
        attempts["n"] += 1
        raise urllib.error.URLError("name resolution failed")

    monkeypatch.setattr(scraper, "_assert_allowed", lambda _url: None)
    monkeypatch.setattr(scraper, "_read_url", broken)
    scraper.max_retries = 2

    with pytest.raises(scrape.ScrapingBlocked, match="Giving up"):
        scraper._fetch("https://example.test/x")

    assert attempts["n"] == 2


# ----------------------------------------------------------------------
# Parsing one page
# ----------------------------------------------------------------------
def test_text_collapses_whitespace(scraper):
    node = soup_of("<td>  Computer\n   Science  </td>").find("td")

    assert scraper._text(node) == "Computer Science"


def test_text_of_nothing_is_empty(scraper):
    assert scraper._text(None) == ""


def test_a_page_yields_its_entries_and_the_next_cursor(scraper):
    entries, cursor = scraper._parse_page(listing_page())

    assert cursor == "next-cursor-token"
    assert len(entries) == 1

    entry = entries[0]
    assert entry["entry_id"] == 900001
    assert entry["url"] == "https://www.thegradcafe.com/result/900001"
    assert entry["raw_university"] == "Johns Hopkins University"
    assert entry["raw_program"] == "Computer Science"
    assert entry["raw_degree"] == "Masters"
    assert entry["raw_date_added"] == "Sep 11, 2026"
    assert entry["raw_status"] == "Accepted on Mar 03"
    assert entry["raw_term"] == "Fall 2026"
    assert entry["raw_applicant_type"] == "American"
    assert entry["raw_gpa"] == "3.90"
    assert entry["raw_gre"] == "168"
    assert entry["raw_gre_v"] == "162"
    assert entry["raw_gre_aw"] == "4.50"
    assert entry["raw_comments"] == "Funded offer, very happy."


def test_a_page_with_no_table_yields_nothing(scraper):
    """An error page or a changed layout is empty, not a crash."""
    assert scraper._parse_page("<html><body>Nothing here</body></html>") == ([], None)


def test_a_table_with_no_body_yields_nothing(scraper):
    assert scraper._parse_page("<html><table></table></html>") == ([], None)


def test_rows_that_are_not_results_are_skipped(scraper):
    """A layout row inside the body is stepped over, not turned into a record."""
    rows = "<tr><td>spacer</td><td></td></tr>" + result_rows(900001)

    entries, _ = scraper._parse_page(listing_page(rows=rows))

    assert [entry["entry_id"] for entry in entries] == [900001]


def test_several_results_on_one_page(scraper):
    rows = result_rows(900001) + result_rows(900002, university="Yale University")

    entries, _ = scraper._parse_page(listing_page(rows=rows))

    assert [entry["entry_id"] for entry in entries] == [900001, 900002]


def test_the_embedded_payload_supplies_exact_decision_dates(scraper):
    """The page already carries the full date; no second request is needed."""
    entries, _ = scraper._parse_page(
        listing_page(decision_dates={900001: "2026-03-03T09:12:00Z"})
    )

    assert entries[0]["raw_decision_date"] == "2026-03-03"


def test_a_missing_payload_leaves_the_decision_date_empty(scraper):
    """``clean.py`` then infers the year, which is the documented fallback."""
    entries, _ = scraper._parse_page(
        "<html><table><tbody>{0}</tbody></table></html>".format(result_rows())
    )

    assert entries[0]["raw_decision_date"] == ""


@pytest.mark.parametrize(
    "attribute",
    [
        "not json at all",
        json.dumps({"props": {}}),
        json.dumps({"props": {"results": "not a dict"}}),
    ],
)
def test_a_malformed_payload_is_ignored(scraper, attribute):
    """Malformed, missing or the wrong shape -- all fall back the same way."""
    page = soup_of('<div data-page="{0}"></div>'.format(attribute.replace('"', "&quot;")))

    assert scraper._parse_embedded_decision_dates(page) == {}


def test_payload_entries_that_are_not_records_are_skipped(scraper):
    """A stray value in the results array does not stop the rest being read."""
    payload = json.dumps(
        {
            "props": {
                "results": {
                    "data": [
                        "junk",
                        {"id": 1, "date_of_notification": "2026-03-03T00:00:00Z"},
                        {"id": 2, "date_of_notification": None},
                        {"id": 3, "date_of_notification": "short"},
                    ]
                }
            }
        }
    )
    page = soup_of('<div data-page="{0}"></div>'.format(payload.replace('"', "&quot;")))

    assert scraper._parse_embedded_decision_dates(page) == {1: "2026-03-03"}


# ----------------------------------------------------------------------
# Row grouping and entry extraction
# ----------------------------------------------------------------------
def test_detail_rows_are_grouped_with_their_main_row(scraper):
    body = soup_of(
        "<table><tbody>{0}{1}</tbody></table>".format(
            result_rows(900001), result_rows(900002)
        )
    ).find("tbody")

    groups = list(scraper._group_rows(body))

    assert len(groups) == 2
    assert all(len(details) == 2 for _main, details in groups)


def test_a_detail_row_with_no_main_row_before_it_starts_a_group(scraper):
    """A stray full-width row at the top of the table is not silently attached."""
    body = soup_of(
        '<table><tbody><tr><td colspan="5">stray</td></tr>{0}</tbody></table>'.format(
            result_rows()
        )
    ).find("tbody")

    groups = list(scraper._group_rows(body))

    assert len(groups) == 2


def test_a_row_with_too_few_cells_is_not_a_result(scraper):
    row = soup_of("<table><tr><td>one</td><td>two</td></tr></table>").find("tr")

    assert scraper._parse_entry(row, []) is None


def test_a_row_with_no_school_or_program_is_not_a_result(scraper):
    """A layout artifact, not a result."""
    row = soup_of(
        "<table><tr><td></td><td></td><td>Sep 11, 2026</td><td>Accepted</td></tr></table>"
    ).find("tr")

    assert scraper._parse_entry(row, []) is None


def test_the_program_cell_splits_into_program_and_degree(scraper):
    cell = soup_of("<td><span>Computer Science</span><span>PhD</span></td>").find("td")

    assert scraper._parse_program_cell(cell) == ("Computer Science", "PhD")


def test_a_program_cell_with_one_span_has_no_degree(scraper):
    cell = soup_of("<td><span>Computer Science</span></td>").find("td")

    assert scraper._parse_program_cell(cell) == ("Computer Science", "")


def test_a_program_cell_with_no_spans_falls_back_to_its_text(scraper):
    """Kept as a fallback in case the markup changes again."""
    cell = soup_of("<td>Computer Science</td>").find("td")

    assert scraper._parse_program_cell(cell) == ("Computer Science", "")


def test_the_result_link_supplies_the_entry_id(scraper):
    row = soup_of('<tr><a href="/result/900001">See More</a></tr>').find("tr")

    url, entry_id = scraper._parse_result_link(row)

    assert url == "https://www.thegradcafe.com/result/900001"
    assert entry_id == 900001


def test_a_row_with_no_result_link_has_no_id(scraper):
    """Without a permanent id a record cannot be de-duplicated later."""
    row = soup_of('<tr><a href="/survey/">Back</a></tr>').find("tr")

    assert scraper._parse_result_link(row) == ("", None)


# ----------------------------------------------------------------------
# Badges
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, field, value",
    [
        ("GPA 3.90", "raw_gpa", "3.90"),
        ("GRE 168", "raw_gre", "168"),
        ("GRE Q 168", "raw_gre", "168"),
        ("GRE V 162", "raw_gre_v", "162"),
        ("GRE AW 4.50", "raw_gre_aw", "4.50"),
        ("gre aw 4.50", "raw_gre_aw", "4.50"),
    ],
)
def test_metric_badges_are_routed_to_their_field(text, field, value):
    """"GRE V" and "GRE AW" must win over the bare "GRE" prefix."""
    entry = {"raw_status": "", field: ""}

    scrape.GradCafeScraper._apply_badge(entry, text)

    assert entry[field] == value


def test_a_metric_label_is_normalized_before_it_is_matched():
    """Whitespace inside a label is collapsed, so "GRE  Q" is still "GRE Q"."""
    entry = {"raw_status": "", "raw_gre": ""}

    scrape.GradCafeScraper._apply_badge(entry, "GRE  Q 168")

    assert entry["raw_gre"] == "168"
    assert entry["raw_status"] == ""


@pytest.mark.parametrize("text", ["Fall 2026", "spring 2025"])
def test_a_season_badge_becomes_the_term(text):
    entry = {"raw_term": "", "raw_status": ""}

    scrape.GradCafeScraper._apply_badge(entry, text)

    assert entry["raw_term"] == text


@pytest.mark.parametrize("text", ["American", "International"])
def test_a_nationality_badge_becomes_the_applicant_type(text):
    entry = {"raw_applicant_type": "", "raw_status": ""}

    scrape.GradCafeScraper._apply_badge(entry, text)

    assert entry["raw_applicant_type"] == text


def test_an_unrecognized_badge_fills_a_missing_status():
    """The decision badge is repeated for small screens; it fills a gap only."""
    entry = {"raw_status": ""}

    scrape.GradCafeScraper._apply_badge(entry, "Accepted on Mar 03")

    assert entry["raw_status"] == "Accepted on Mar 03"


def test_an_unrecognized_badge_never_overwrites_a_status():
    entry = {"raw_status": "Accepted on Mar 03"}

    scrape.GradCafeScraper._apply_badge(entry, "Something else")

    assert entry["raw_status"] == "Accepted on Mar 03"


def test_an_empty_badge_is_ignored():
    entry = {"raw_status": ""}

    scrape.GradCafeScraper._apply_badge(entry, "")

    assert entry == {"raw_status": ""}


def test_a_comment_row_becomes_the_comment(scraper):
    entry = {"raw_comments": "", "raw_status": ""}
    row = soup_of("<tr><td><p>Waited four months.</p></td></tr>").find("tr")

    scraper._apply_detail_row(entry, row)

    assert entry["raw_comments"] == "Waited four months."


def test_an_empty_comment_row_leaves_the_comment_alone(scraper):
    entry = {"raw_comments": "kept", "raw_status": ""}
    row = soup_of("<tr><td><p>  </p></td></tr>").find("tr")

    scraper._apply_detail_row(entry, row)

    assert entry["raw_comments"] == "kept"


def test_the_badge_wrapper_div_is_skipped(scraper):
    """Only leaf divs carry a value; the flex wrapper around them does not."""
    entry = {"raw_term": "", "raw_status": "", "raw_gpa": ""}
    row = soup_of(
        "<tr><td><div><div>Fall 2026</div><div>GPA 3.90</div></div></td></tr>"
    ).find("tr")

    scraper._apply_detail_row(entry, row)

    assert entry["raw_term"] == "Fall 2026"
    assert entry["raw_gpa"] == "3.90"
    assert entry["raw_status"] == "", "the wrapper's combined text must not leak in"


# ----------------------------------------------------------------------
# Pagination
# ----------------------------------------------------------------------
def test_the_next_cursor_is_read_off_the_next_link(scraper):
    page = soup_of('<a href="/survey/?cursor=abc123">Next</a>')

    assert scraper._parse_next_cursor(page) == "abc123"


def test_a_page_with_no_next_link_has_no_cursor(scraper):
    page = soup_of('<a href="/survey/">Back to start</a>')

    assert scraper._parse_next_cursor(page) is None


def test_a_cursor_link_that_is_not_next_is_ignored(scraper):
    """"Previous" also carries a cursor, and following it would go backwards."""
    page = soup_of('<a href="/survey/?cursor=old">Previous</a>')

    assert scraper._parse_next_cursor(page) is None


def test_a_next_link_with_an_empty_cursor_is_ignored(scraper):
    page = soup_of('<a href="/survey/?cursor=">Next</a>')

    assert scraper._parse_next_cursor(page) is None


# ----------------------------------------------------------------------
# The driver
# ----------------------------------------------------------------------
def make_scraper(monkeypatch, pages):
    """A scraper that serves ``pages`` in order and never touches the network."""
    scraper = scrape.GradCafeScraper(delay=0.0)
    monkeypatch.setattr(scraper, "check_robots", lambda verbose=True: None)
    monkeypatch.setattr(scraper, "_assert_allowed", lambda _url: None)
    served = iter(pages)

    def fetch(_url):
        scraper.pages_fetched += 1
        return next(served, listing_page(rows="", cursor=None))

    monkeypatch.setattr(scraper, "_fetch", fetch)
    return scraper


def test_a_scrape_walks_pages_until_the_target_is_met(monkeypatch, scratch_paths):
    pages = [
        listing_page(rows=result_rows(900001), cursor="page-2"),
        listing_page(rows=result_rows(900002), cursor="page-3"),
        listing_page(rows=result_rows(900003), cursor="page-4"),
    ]
    scraper = make_scraper(monkeypatch, pages)

    entries = scraper.scrape_data(target_entries=2, resume=False)

    assert [entry["entry_id"] for entry in entries] == [900001, 900002]


def test_a_scrape_stops_at_the_last_page(monkeypatch, scratch_paths, capsys):
    """No Next link means there is nothing more to walk."""
    scraper = make_scraper(
        monkeypatch, [listing_page(rows=result_rows(900001), cursor=None)]
    )

    entries = scraper.scrape_data(target_entries=100, resume=False)

    assert len(entries) == 1
    assert "reached the last page" in capsys.readouterr().err


def test_a_scrape_stops_after_five_pages_with_nothing_new(
    monkeypatch, scratch_paths, capsys
):
    """A pagination loop must not spin silently forever."""
    repeated = listing_page(rows=result_rows(900001), cursor="same-cursor")
    scraper = make_scraper(monkeypatch, [repeated] * 20)

    entries = scraper.scrape_data(target_entries=100, resume=False)

    assert len(entries) == 1
    assert "five pages yielded no new entries" in capsys.readouterr().err


def test_a_blocked_scrape_keeps_what_it_already_collected(
    monkeypatch, scratch_paths, capsys
):
    """Being refused mid-run is a halt, not a loss."""
    scraper = make_scraper(
        monkeypatch, [listing_page(rows=result_rows(900001), cursor="page-2")]
    )
    real_fetch = scraper._fetch
    calls = {"n": 0}

    def fetch(url):
        calls["n"] += 1
        if calls["n"] > 1:
            raise scrape.ScrapingBlocked("HTTP 429")
        return real_fetch(url)

    monkeypatch.setattr(scraper, "_fetch", fetch)

    entries = scraper.scrape_data(target_entries=100, resume=False)

    assert len(entries) == 1
    assert "[halt] HTTP 429" in capsys.readouterr().err
    assert json.loads(scrape.RAW_DATA_PATH.read_text(encoding="utf-8"))


def test_an_interrupted_scrape_saves_what_it_has(monkeypatch, scratch_paths, capsys):
    """Ctrl-C leaves the collected entries on disk and the cursor checkpointed."""
    scraper = make_scraper(monkeypatch, [])

    def interrupt(_url):
        raise KeyboardInterrupt

    monkeypatch.setattr(scraper, "_fetch", interrupt)

    assert scraper.scrape_data(target_entries=10, resume=False) == []
    assert scrape.RAW_DATA_PATH.exists()
    assert scrape.CHECKPOINT_PATH.exists()


def test_a_scrape_resumes_from_saved_progress(monkeypatch, scratch_paths, capsys):
    """Saved entries are kept and the saved cursor is where the walk restarts."""
    scrape.save_data([{"entry_id": 900001}], scrape.RAW_DATA_PATH)
    scrape._save_checkpoint("saved-cursor")
    requested = []

    scraper = scrape.GradCafeScraper(delay=0.0)
    monkeypatch.setattr(scraper, "check_robots", lambda verbose=True: None)

    def fetch(url):
        requested.append(url)
        return listing_page(rows=result_rows(900002), cursor=None)

    monkeypatch.setattr(scraper, "_fetch", fetch)

    entries = scraper.scrape_data(target_entries=100, resume=True)

    assert [entry["entry_id"] for entry in entries] == [900001, 900002]
    assert "cursor=saved-cursor" in requested[0]
    assert "continuing from 1 saved entries" in capsys.readouterr().err


def test_progress_is_reported_and_saved_periodically(
    monkeypatch, scratch_paths, capsys
):
    """Every ``progress_every`` pages the run checkpoints itself."""
    pages = [
        listing_page(rows=result_rows(900000 + n), cursor="page-{0}".format(n))
        for n in range(1, 5)
    ]
    scraper = make_scraper(monkeypatch, pages)

    scraper.scrape_data(target_entries=3, resume=False, progress_every=1)

    printed = capsys.readouterr().err
    assert "[progress]" in printed
    assert "entries/s" in printed
    assert json.loads(scrape.CHECKPOINT_PATH.read_text(encoding="utf-8"))["cursor"]


def test_progress_survives_a_zero_length_run(scraper, capsys):
    """Dividing by an elapsed time of zero would be the obvious way to crash."""
    scraper._report_progress(0, 10, scrape.time.monotonic() + 1)

    assert "0/10 entries" in capsys.readouterr().err


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------
def test_rows_round_trip_through_a_file(tmp_path):
    path = tmp_path / "rows.json"
    rows = [{"entry_id": 1, "raw_university": "Hawai‘i"}]

    scrape.save_data(rows, path)

    assert scrape.load_data(path) == rows


def test_loading_a_missing_file_gives_nothing(tmp_path):
    assert scrape.load_data(tmp_path / "absent.json") == []


def test_loading_a_file_that_is_not_a_list_gives_nothing(tmp_path):
    path = tmp_path / "object.json"
    path.write_text('{"not": "a list"}', encoding="utf-8")

    assert scrape.load_data(path) == []


def test_a_locked_destination_is_retried(tmp_path, monkeypatch):
    """OneDrive and virus scanners grab a file the instant it is written.

    ``os.replace`` is atomic but raises WinError 5 while anything else holds
    the destination open, and the rename succeeds a moment later -- so a brief
    retry turns a spurious crash back into the atomic replace it was meant to be.
    """
    monkeypatch.setattr(scrape.time, "sleep", lambda _s: None)
    attempts = {"n": 0}
    real_replace = scrape.Path.replace

    def flaky(self, target):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise PermissionError(5, "being used by another process")
        return real_replace(self, target)

    monkeypatch.setattr(scrape.Path, "replace", flaky)

    scrape.save_data([{"entry_id": 1}], tmp_path / "rows.json")

    assert attempts["n"] == 3
    assert scrape.load_data(tmp_path / "rows.json") == [{"entry_id": 1}]


def test_a_permanently_locked_destination_finally_raises(tmp_path, monkeypatch):
    """The retry is brief, not infinite: a real lock still surfaces."""
    monkeypatch.setattr(scrape.time, "sleep", lambda _s: None)

    def always_locked(self, target):
        raise PermissionError(5, "being used by another process")

    monkeypatch.setattr(scrape.Path, "replace", always_locked)

    with pytest.raises(PermissionError):
        scrape.save_data([{"entry_id": 1}], tmp_path / "rows.json")


def test_the_checkpoint_round_trips(scratch_paths):
    scrape._save_checkpoint("abc123")

    assert scrape._load_checkpoint() == "abc123"


def test_a_missing_checkpoint_is_none(scratch_paths):
    assert scrape._load_checkpoint() is None


def test_a_corrupt_checkpoint_is_none(scratch_paths):
    """A truncated checkpoint starts the walk from the newest result."""
    scrape.CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    scrape.CHECKPOINT_PATH.write_text("{ not json", encoding="utf-8")

    assert scrape._load_checkpoint() is None


# ----------------------------------------------------------------------
# python scrape.py
# ----------------------------------------------------------------------
def test_main_runs_a_scrape(monkeypatch, scratch_paths, capsys):
    monkeypatch.setattr(
        scrape.GradCafeScraper,
        "scrape_data",
        lambda self, target_entries, resume: [{"entry_id": 1}] * target_entries,
    )

    assert scrape.main(["--target", "3", "--delay", "0"]) == 0
    assert "Collected 3 entries" in capsys.readouterr().err


def test_main_writes_to_an_alternative_destination(
    monkeypatch, scratch_paths, tmp_path
):
    """``--out`` saves a second copy where the caller asked for it."""
    monkeypatch.setattr(
        scrape.GradCafeScraper,
        "scrape_data",
        lambda self, target_entries, resume: [{"entry_id": 1}],
    )
    out = tmp_path / "elsewhere.json"

    assert scrape.main(["--out", str(out)]) == 0
    assert scrape.load_data(out) == [{"entry_id": 1}]


def test_main_can_start_again_from_the_newest_result(monkeypatch, scratch_paths):
    """``--no-resume`` is what the Pull Data button relies on."""
    seen = {}
    monkeypatch.setattr(
        scrape.GradCafeScraper,
        "scrape_data",
        lambda self, target_entries, resume: seen.update(resume=resume) or [],
    )

    scrape.main(["--no-resume"])

    assert seen["resume"] is False


def test_main_can_print_the_robots_check_and_stop(monkeypatch, capsys):
    """``--robots-only`` is how the compliance evidence was produced."""
    monkeypatch.setattr(
        scrape.GradCafeScraper, "_read_url", lambda _self, _url: ROBOTS_TXT
    )
    started = []
    monkeypatch.setattr(
        scrape.GradCafeScraper,
        "scrape_data",
        lambda *a, **k: started.append(True) or [],
    )

    assert scrape.main(["--robots-only"]) == 0
    assert "User-agent: *" in capsys.readouterr().out
    assert started == [], "--robots-only must not scrape"
