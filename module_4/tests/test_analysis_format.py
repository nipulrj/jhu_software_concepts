"""How analysis is labelled and how its numbers are rounded.

Marked ``analysis``.  Two properties are under test:

* every rendered figure carries an ``Answer:`` label, so no number on the page
  is ambiguous about what it answers; and
* every percentage is shown with exactly two decimal places.

The second is checked twice over -- once on the formatting helper that produces
percentages, and once on the fully rendered page, by pulling out every
percentage-looking token and requiring each to have two decimals.  The page-wide
sweep is the one that matters: a helper that rounds correctly is no use if some
other line on the page formats a percentage by hand.
"""

from __future__ import annotations

import re

import pytest
from bs4 import BeautifulSoup

import conftest
import query_data
from doubles import PERCENT_PATTERN, TWO_DECIMAL_PERCENT

pytestmark = pytest.mark.analysis


def page_text(client) -> str:
    """The analysis page as visible text, with the markup stripped out.

    Tags are dropped so that an attribute value -- a ``data-`` field, a CSS
    class -- cannot be mistaken for a rendered figure.
    """
    html = client.get("/analysis").get_data(as_text=True)
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)


# ----------------------------------------------------------------------
# Labels
# ----------------------------------------------------------------------
def test_page_carries_an_answer_label(client):
    """At least one ``Answer:`` label, as the assignment requires."""
    assert "Answer:" in page_text(client)


def test_every_rendered_figure_is_labelled(client):
    """*Each* answer line is labelled, not merely the first one.

    Counted against the canned analysis rather than a fixed number, so adding a
    question to the fixture cannot quietly leave its answer unlabelled.
    """
    expected = sum(len(result.answer_lines) for result in conftest.CANNED_RESULTS)

    assert page_text(client).count("Answer:") == expected


def test_answer_labels_sit_beside_their_answers(client):
    """The label introduces the figure rather than floating loose in the page."""
    html = client.get("/analysis").get_data(as_text=True)
    page = BeautifulSoup(html, "html.parser")

    for line in page.select("p.answer-line"):
        text = line.get_text(" ", strip=True)
        assert text.startswith("Answer:")
        assert len(text) > len("Answer:"), "a label with no answer after it"


def test_each_question_block_carries_its_number_and_question(client):
    """Every answer is attributable: the number and the question are both shown."""
    html = client.get("/analysis").get_data(as_text=True)
    page = BeautifulSoup(html, "html.parser")

    for result in conftest.CANNED_RESULTS:
        block = page.select_one('[data-testid="question-{0}"]'.format(result.number))
        heading = block.find("h3").get_text(" ", strip=True)
        assert "Question {0}".format(result.number) in heading
        assert result.question in heading


# ----------------------------------------------------------------------
# Two-decimal percentages
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "value, expected",
    [
        (39.2837, "39.28%"),
        (39.2, "39.20%"),
        (40, "40.00%"),
        (0, "0.00%"),
        (100, "100.00%"),
        ("27.5", "27.50%"),
        (None, "n/a"),
    ],
)
def test_percentages_are_formatted_to_two_decimals(value, expected):
    """The helper every percentage on the page goes through."""
    assert query_data.fmt_pct(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [(3.6, "3.60"), (3.617, "3.62"), (162, "162.00"), (None, "n/a")],
)
def test_averages_are_formatted_to_two_decimals(value, expected):
    """Averages are rounded the same way, so the page reads consistently."""
    assert query_data.fmt_avg(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [(1234, "1,234"), (0, "0"), (50006, "50,006"), (None, "n/a")],
)
def test_counts_are_whole_numbers_with_separators(value, expected):
    """Counts are whole numbers -- a count with decimals would be a bug."""
    assert query_data.fmt_count(value) == expected


@pytest.mark.parametrize(
    "value, expected", [(0, "+0"), (12, "+12"), (-4, "-4"), (None, "n/a")]
)
def test_differences_carry_their_sign(value, expected):
    """Question 9's difference is signed, so "no difference" reads as +0."""
    assert query_data.fmt_signed(value) == expected


def test_every_percentage_on_the_page_has_two_decimals(client):
    """The property that matters: the *page*, not just the helper.

    Every percentage-looking token is extracted with a loose pattern and then
    required to match the strict one, so a percentage formatted by hand
    somewhere else in the template fails here.
    """
    text = page_text(client)
    percentages = PERCENT_PATTERN.findall(text)

    assert percentages, "no percentage on the page: this test would pass vacuously"
    wrong = [p for p in percentages if not TWO_DECIMAL_PERCENT.fullmatch(p)]
    assert not wrong, "percentages not shown to two decimals: {0}".format(wrong)


def test_the_percentage_sweep_can_fail(make_app):
    """The sweep above catches a badly formatted percentage.

    A test that only ever sees correct data proves nothing about its own
    pattern, so here is the negative: a question whose answer rounds to one
    decimal, which the sweep must reject.
    """
    sloppy = [conftest.make_question(2, "Badly rounded?", ["Percent: 39.3%"])]
    client = make_app(analysis_provider=lambda: sloppy).test_client()

    percentages = PERCENT_PATTERN.findall(page_text(client))

    assert percentages == ["39.3%"]
    assert not TWO_DECIMAL_PERCENT.fullmatch(percentages[0])


@pytest.mark.parametrize("value", [0.005, 12.344, 12.345, 99.999, 1 / 3])
def test_formatted_percentages_always_match_the_strict_pattern(value):
    """Whatever the input, the helper's output satisfies the page's rule."""
    assert TWO_DECIMAL_PERCENT.fullmatch(query_data.fmt_pct(value))


def test_percentages_inside_tables_are_formatted_too(make_app):
    """A percentage in a result table is held to the same rule as one in a line."""
    result = conftest.make_question(
        10,
        "Which university is hardest?",
        ["Hardest: Stanford University at 18.47%"],
        table={
            "columns": ["University", "Acceptance rate"],
            "rows": [["Stanford University", query_data.fmt_pct(18.4712)]],
        },
        original=True,
    )
    client = make_app(analysis_provider=lambda: [result]).test_client()

    percentages = PERCENT_PATTERN.findall(page_text(client))

    assert "18.47%" in percentages
    assert all(TWO_DECIMAL_PERCENT.fullmatch(p) for p in percentages)


# ----------------------------------------------------------------------
# The shape the template consumes
# ----------------------------------------------------------------------
def test_analysis_dict_is_keyed_by_question_number():
    """:func:`query_data.analysis_dict` indexes answers for the template."""
    indexed = query_data.analysis_dict(conftest.CANNED_RESULTS)

    assert set(indexed) == {result.number for result in conftest.CANNED_RESULTS}


def test_analysis_dict_entries_carry_every_key_the_template_reads():
    """Each entry has the full set of keys, so the template never sees a gap."""
    indexed = query_data.analysis_dict(conftest.CANNED_RESULTS)

    for entry in indexed.values():
        assert set(entry) == set(query_data.ANALYSIS_KEYS)


def test_question_answer_joins_its_lines():
    """``answer`` is the answer lines as one string, for the console and the PDF."""
    result = conftest.make_question(1, "How many?", ["First line", "Second line"])

    assert result.answer == "First line\nSecond line"
    assert result.as_dict()["answer"] == result.answer


def test_analysis_dict_of_nothing_is_empty():
    """No answers indexes to no entries rather than raising."""
    assert query_data.analysis_dict([]) == {}


# ----------------------------------------------------------------------
# The patterns themselves
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("Percent international: 39.28%", ["39.28%"]),
        ("from 18.47% to 64.78%", ["18.47%", "64.78%"]),
        ("100.00% and 0.00%", ["100.00%", "0.00%"]),
        ("a gap of 1,234.56%", ["1,234.56%"]),
        ("no percentages here", []),
    ],
)
def test_percent_pattern_finds_percentages(text, expected):
    """The loose pattern finds what it should, so the sweep cannot miss one."""
    assert PERCENT_PATTERN.findall(text) == expected


@pytest.mark.parametrize("token", ["39.28%", "0.00%", "1,234.56%", "-4.50%"])
def test_strict_pattern_accepts_two_decimals(token):
    assert TWO_DECIMAL_PERCENT.fullmatch(token)


@pytest.mark.parametrize("token", ["39%", "39.2%", "39.283%", "39.%"])
def test_strict_pattern_rejects_anything_else(token):
    assert not TWO_DECIMAL_PERCENT.fullmatch(token)


def test_page_text_strips_markup(client):
    """The helper these tests read the page through returns text, not tags."""
    text = page_text(client)

    assert "<" not in text
    assert not re.search(r"data-testid", text)
