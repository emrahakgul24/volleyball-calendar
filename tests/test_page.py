"""Browser test: every subscribe button on the built page links to a feed that exists, in the right format.

Serves the built site over HTTP at a sub-path (like GitHub Pages' /volleyball-calendar/) and reads the links
the page's script produces. Set CHROMIUM_PATH to use a preinstalled Chromium instead of Playwright's own.
"""

import functools
import os
import threading
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from playwright.sync_api import sync_playwright

import build
from test_parse import matches

PREFIX = "/volleyball-calendar/"


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("www")
    out = root / PREFIX.strip("/")
    build.build(out, sorted(matches(), key=lambda m: m.sort_key), datetime(2026, 11, 14, 12, tzinfo=timezone.utc))
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield out, f"http://127.0.0.1:{server.server_address[1]}{PREFIX}"
    server.shutdown()


@pytest.fixture(scope="module")
def page(site):
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        pg = browser.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(site[1])
        yield pg
        assert not errors, errors
        browser.close()


def feed_path(site_dir, https_url, base):
    """Map a feed URL back to a file in the built site, failing if it points elsewhere."""
    assert https_url.startswith(base + "feeds/"), https_url
    path = site_dir / https_url[len(base):]
    assert path.is_file(), f"{https_url} does not exist"
    return path


def check_box(page, site, selector, feed_id, name):
    site_dir, base = site
    box = page.locator(selector)
    https = f"{base}feeds/{feed_id}.ics"
    webcal = "webcal://" + https.removeprefix("http://")

    apple = box.locator('a[data-action="apple"]').get_attribute("href")
    assert apple == webcal

    google = urlparse(box.locator('a[data-action="google"]').get_attribute("href"))
    assert (google.scheme, google.netloc, google.path) == ("https", "calendar.google.com", "/calendar/render")
    assert parse_qs(google.query)["cid"] == [webcal]

    outlook = urlparse(box.locator('a[data-action="outlook"]').get_attribute("href"))
    assert (outlook.netloc, outlook.path) == ("outlook.live.com", "/calendar/0/addfromweb")
    q = parse_qs(outlook.query)
    assert q["url"] == [https] and q["name"] == [name]

    content = feed_path(site_dir, https, base).read_text(encoding="utf-8")
    assert content.startswith("BEGIN:VCALENDAR")
    assert f"X-WR-CALNAME:{name.replace(',', chr(92) + ',')}" in content.replace("\r\n ", "")


def test_competition_buttons(page, site):
    cards = page.locator(".grid .feed")
    assert cards.count() == 6
    for i in range(cards.count()):
        card = cards.nth(i)
        feed_id, name = card.get_attribute("data-feed"), card.get_attribute("data-name")
        check_box(page, site, f'.grid .feed[data-feed="{feed_id}"]', feed_id, name)


def test_team_buttons(page, site):
    options = page.locator("#team option")
    assert options.count() == 14
    for i in range(options.count()):
        value = options.nth(i).get_attribute("value")
        page.select_option("#team", value)
        check_box(page, site, ".team-picker", value, options.nth(i).get_attribute("data-name"))


def test_copy_link_copies_the_feed_url(page, site):
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.click('.feed[data-feed="sultanlar"] button[data-action="copy"]')
    copied = page.evaluate("navigator.clipboard.readText()")
    assert unquote(copied) == f"{site[1]}feeds/sultanlar.ics"
