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
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        yield b
        b.close()


@pytest.fixture(scope="module")
def page(site, browser):
    pg = browser.new_page(locale="en-US")
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(site[1])
    yield pg
    assert not errors, errors
    pg.close()


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


def test_ko_fi_buttons(page):
    links = page.locator("a.kofi")
    assert links.count() == 2  # header and bottom box
    assert page.locator("header a.kofi").is_visible()
    for i in range(links.count()):
        link = links.nth(i)
        assert link.get_attribute("href") == "https://ko-fi.com/emrahakgul"
        assert link.get_attribute("target") == "_blank" and "noopener" in link.get_attribute("rel")


def test_language_picker_switches_to_turkish_and_back(site, browser):
    pg = browser.new_context(locale="en-US").new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(site[1])
    assert pg.locator("html").get_attribute("lang") == "en"
    assert pg.locator("h2").first.inner_text() == "Competitions"

    pg.click('.lang button[data-lang="tr"]')
    assert pg.locator("html").get_attribute("lang") == "tr"
    assert pg.title() == "Türk Kadın Voleybolu Takvimi"
    assert pg.locator("h2").first.inner_text() == "Takvimler"
    assert pg.locator('.feed[data-feed="sultanlar"] .count').inner_text() == "95 maç"
    assert pg.locator('.feed[data-feed="europe"] h3').inner_text() == "Avrupa kupaları"
    assert "Hafta" in pg.locator(".match .meta").first.inner_text()
    assert pg.locator(".day").first.text_content().startswith(("Paz", "Pzt", "Sal", "Çar", "Per", "Cum", "Cmt"))
    assert "Son güncelleme" in pg.locator("footer").inner_text()
    assert pg.locator('.lang button[data-lang="tr"]').get_attribute("aria-pressed") == "true"
    # Links don't depend on the language.
    assert pg.locator('.feed[data-feed="all"] a.apple').get_attribute("href").endswith("/feeds/all.ics")

    pg.reload()  # the choice is remembered
    assert pg.locator("h2").first.inner_text() == "Takvimler"

    pg.click('.lang button[data-lang="en"]')
    assert pg.locator("h2").first.inner_text() == "Competitions"
    assert pg.locator('.feed[data-feed="sultanlar"] .count').inner_text() == "95 matches"
    assert "Last updated" in pg.locator("footer").inner_text()
    assert not errors, errors
    pg.context.close()


def test_turkish_browsers_and_lang_parameter_default_to_turkish(site, browser):
    tr = browser.new_context(locale="tr-TR").new_page()
    tr.goto(site[1])
    assert tr.locator("html").get_attribute("lang") == "tr"
    assert tr.locator("h2").first.inner_text() == "Takvimler"
    en = browser.new_context(locale="en-US").new_page()
    en.goto(site[1] + "?lang=tr")
    assert en.locator("h2").first.inner_text() == "Takvimler"
    for pg in (tr, en):
        pg.context.close()
