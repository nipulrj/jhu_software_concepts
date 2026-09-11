"""Capture ``screenshot.jpg``: evidence that robots.txt was checked before scraping.

Headless Chrome renders https://www.thegradcafe.com/robots.txt, and the real
permission decisions taken from ``scrape.py`` are stamped into a header above
the page so the image records *when* the file was checked, what the server
returned, and which paths the scraper concluded it may and may not fetch.

Run it from the ``module_2`` directory::

    python capture_robots_evidence.py

Chrome is located automatically in the usual Windows/macOS/Linux install
locations, or can be pointed at explicitly with ``--chrome``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFont

from scrape import BASE_URL, ROBOTS_URL, SURVEY_PATH, USER_AGENT, GradCafeScraper

MODULE_DIR = Path(__file__).resolve().parent
SCREENSHOT_PATH = MODULE_DIR / "screenshot.jpg"
SNAPSHOT_PATH = MODULE_DIR / "robots_txt_snapshot.txt"

# Paths whose permission status is worth recording in the evidence header:
# the two the scraper actually uses, and two it must stay away from.
CHECKED_PATHS = (SURVEY_PATH, "/result/1020480", "/profile", "/signin")

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
)

VIEWPORT = (1000, 1250)
HEADER_BACKGROUND = (17, 24, 39)
HEADER_TEXT = (243, 244, 246)
ALLOWED_COLOUR = (52, 211, 153)
BLOCKED_COLOUR = (248, 113, 113)
MUTED_TEXT = (156, 163, 175)


def _find_chrome(explicit: Optional[str] = None) -> str:
    """Locate a Chrome/Chromium binary to render the page."""
    if explicit:
        if not Path(explicit).exists():
            raise FileNotFoundError(f"Chrome not found at {explicit}")
        return explicit

    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate

    found = shutil.which("google-chrome") or shutil.which("chrome") or shutil.which("chromium")
    if found:
        return found
    raise FileNotFoundError(
        "Could not find Chrome. Pass its path with --chrome."
    )


def _capture_page(chrome: str, url: str, destination: Path) -> None:
    """Screenshot ``url`` with headless Chrome."""
    command = [
        chrome,
        "--headless",
        "--disable-gpu",
        "--hide-scrollbars",
        f"--window-size={VIEWPORT[0]},{VIEWPORT[1]}",
        f"--screenshot={destination}",
        url,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if not destination.exists():
        raise RuntimeError(
            f"Chrome did not produce a screenshot.\n{result.stdout}\n{result.stderr}"
        )


def _http_status(url: str) -> str:
    """Report the status line the server returns for robots.txt."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return f"HTTP {response.status} {response.reason}"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code} {exc.reason}"


def _write_snapshot(path: Path, robots_text: str, status: str) -> None:
    """Save the exact robots.txt bytes we fetched, with provenance on top.

    The screenshot proves the file was read, but an image cannot be grepped or
    diffed, and the live file changes over time. Keeping the text alongside it
    means the rules the scraper was actually written against stay inspectable.
    """
    checked_at = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    header = (
        f"# Snapshot of {ROBOTS_URL}\n"
        f"# Retrieved: {checked_at}\n"
        f"# Response:  {status}\n"
        f"# Retrieved by: {USER_AGENT}\n"
        "#\n"
        "# Saved verbatim below. Regenerate with: python capture_robots_evidence.py\n"
        "# " + "-" * 74 + "\n\n"
    )
    path.write_text(header + robots_text, encoding="utf-8")


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    """Load a monospace font, falling back to Pillow's built-in bitmap font."""
    names = (
        ["consolab.ttf", "DejaVuSansMono-Bold.ttf", "Menlo.ttc"]
        if bold
        else ["consola.ttf", "DejaVuSansMono.ttf", "Menlo.ttc"]
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _build_header_lines() -> List[tuple]:
    """Run the real robots check and describe it as ``(text, colour)`` lines."""
    scraper = GradCafeScraper()
    parser = scraper.check_robots(verbose=False)
    checked_at = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    lines: List[tuple] = [
        (f"robots.txt compliance check  -  {ROBOTS_URL}", HEADER_TEXT),
        (f"Checked {checked_at}   |   {_http_status(ROBOTS_URL)}", MUTED_TEXT),
        (f"Scraper user-agent: {USER_AGENT}", MUTED_TEXT),
        ("", HEADER_TEXT),
    ]

    for path in CHECKED_PATHS:
        allowed = parser.can_fetch(USER_AGENT, urllib.parse.urljoin(BASE_URL, path))
        verdict = "ALLOWED    " if allowed else "DISALLOWED "
        colour = ALLOWED_COLOUR if allowed else BLOCKED_COLOUR
        usage = "scraped" if allowed else "never requested"
        lines.append((f"  {verdict} {path:<22} ({usage})", colour))

    crawl_delay = parser.crawl_delay(USER_AGENT)
    lines.append(("", HEADER_TEXT))
    lines.append(
        (
            f"  Declared crawl-delay: {crawl_delay if crawl_delay else 'none'}"
            f"   |   Delay used by scraper: {scraper.delay:.1f}s between requests",
            MUTED_TEXT,
        )
    )
    return lines


def _compose(page_image: Image.Image, lines: List[tuple]) -> Image.Image:
    """Stack the evidence header above the rendered robots.txt page."""
    title_font = _load_font(19, bold=True)
    body_font = _load_font(15)

    padding, line_height = 22, 25
    header_height = padding * 2 + line_height * len(lines)

    canvas = Image.new(
        "RGB", (page_image.width, header_height + page_image.height), HEADER_BACKGROUND
    )
    draw = ImageDraw.Draw(canvas)

    y = padding
    for index, (text, colour) in enumerate(lines):
        if text:
            draw.text((padding, y), text, font=title_font if index == 0 else body_font, fill=colour)
        y += line_height

    # Hairline separating the evidence header from the page capture.
    draw.line(
        [(0, header_height - 1), (canvas.width, header_height - 1)],
        fill=(55, 65, 81),
        width=2,
    )
    canvas.paste(page_image, (0, header_height))
    return canvas


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chrome", default=None, help="path to the Chrome binary")
    parser.add_argument(
        "--out", type=Path, default=SCREENSHOT_PATH,
        help="output image path (default: %(default)s)",
    )
    parser.add_argument(
        "--snapshot", type=Path, default=SNAPSHOT_PATH,
        help="where to save the robots.txt text (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    chrome = _find_chrome(args.chrome)
    print(f"Rendering {ROBOTS_URL} with {chrome}", file=sys.stderr)

    # Keep the raw text next to the image: greppable, diffable, and the record
    # of which rules the scraper was written against.
    status = _http_status(ROBOTS_URL)
    _write_snapshot(args.snapshot, GradCafeScraper()._read_url(ROBOTS_URL), status)
    print(f"Wrote {args.snapshot}", file=sys.stderr)

    with tempfile.TemporaryDirectory() as workdir:
        raw_capture = Path(workdir) / "robots.png"
        _capture_page(chrome, ROBOTS_URL, raw_capture)
        composed = _compose(Image.open(raw_capture).convert("RGB"), _build_header_lines())

    args.out.parent.mkdir(parents=True, exist_ok=True)
    composed.save(args.out, "JPEG", quality=92)
    print(f"Wrote {args.out} ({composed.width}x{composed.height})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
