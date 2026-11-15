"""Browser test: every subscribe button on the built page links to a feed that exists, in the right format.

Serves the built site over HTTP at a sub-path (like GitHub Pages' /volleyball-calendar/) and reads the links
the page's script produces. Set CHROMIUM_PATH to use a preinstalled Chromium instead of Playwright's own.
"""

import functools
import os
import threading
from collections.abc import Iterator
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from playwright.sync_api import Browser, Locator, Page, sync_playwright

import build
from test_parse import matches

PREFIX = "/volleyball-calendar/"

Site = tuple[Path, str]  # (built site directory, its URL)


def attr(loc: Locator, name: str) -> str:
    """An attribute that must be present."""
    value = loc.get_attribute(name)
    assert value is not None, f"missing {name}"
    return value


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Site]:
    root = tmp_path_factory.mktemp("www")
    out = root / PREFIX.strip("/")
    build.build(out, sorted(matches(), key=lambda m: m.sort_key), datetime(2026, 11, 14, 12, tzinfo=timezone.utc))
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield out, f"http://127.0.0.1:{server.server_address[1]}{PREFIX}"
    server.shutdown()


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        yield b
        b.close()


@pytest.fixture(scope="module")
def page(site: Site, browser: Browser) -> Iterator[Page]:
    pg = browser.new_page(locale="en-US")
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(site[1])
    yield pg
    assert not errors, errors
    pg.close()


def feed_path(site_dir: Path, https_url: str, base: str) -> Path:
    """Map a feed URL back to a file in the built site, failing if it points elsewhere."""
    assert https_url.startswith(base + "feeds/"), https_url
    path = site_dir / https_url[len(base):]
    assert path.is_file(), f"{https_url} does not exist"
    return path


def check_box(page: Page, site: Site, selector: str, feed_id: str, name: str) -> None:
    site_dir, base = site
    box = page.locator(selector)
    https = f"{base}feeds/{feed_id}.ics"
    webcal = "webcal://" + https.removeprefix("http://")

    apple = attr(box.locator('a[data-action="apple"]'), "href")
    assert apple == webcal

    google = urlparse(attr(box.locator('a[data-action="google"]'), "href"))
    assert (google.scheme, google.netloc, google.path) == ("https", "calendar.google.com", "/calendar/render")
    assert parse_qs(google.query)["cid"] == [webcal]

    outlook = urlparse(attr(box.locator('a[data-action="outlook"]'), "href"))
    assert (outlook.netloc, outlook.path) == ("outlook.live.com", "/calendar/0/addfromweb")
    q = parse_qs(outlook.query)
    assert q["url"] == [https] and q["name"] == [name]

    content = feed_path(site_dir, https, base).read_text(encoding="utf-8")
    assert content.startswith("BEGIN:VCALENDAR")
    assert f"X-WR-CALNAME:{name.replace(',', chr(92) + ',')}" in content.replace("\r\n ", "")


def test_competition_buttons(page: Page, site: Site) -> None:
    cards = page.locator(".grid .feed")
    assert cards.count() == 6
    for i in range(cards.count()):
        card = cards.nth(i)
        feed_id, name = attr(card, "data-feed"), attr(card, "data-name")
        check_box(page, site, f'.grid .feed[data-feed="{feed_id}"]', feed_id, name)


def test_team_buttons(page: Page, site: Site) -> None:
    options = page.locator("#team option")
    assert options.count() == 14
    for i in range(options.count()):
        value = attr(options.nth(i), "value")
        page.select_option("#team", value)
        check_box(page, site, ".team-picker", value, attr(options.nth(i), "data-name"))


def test_copy_link_copies_the_feed_url(page: Page, site: Site) -> None:
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.click('.feed[data-feed="sultanlar"] button[data-action="copy"]')
    copied = page.evaluate("navigator.clipboard.readText()")
    assert unquote(copied) == f"{site[1]}feeds/sultanlar.ics"


def test_ko_fi_buttons(page: Page) -> None:
    links = page.locator("a.kofi")
    assert links.count() == 2  # header and bottom box
    assert page.locator("header a.kofi").is_visible()
    for i in range(links.count()):
        link = links.nth(i)
        assert attr(link, "href") == "https://ko-fi.com/emrahakgul"
        assert attr(link, "target") == "_blank" and "noopener" in attr(link, "rel")


def test_language_picker_switches_to_turkish_and_back(site: Site, browser: Browser) -> None:
    pg = browser.new_context(locale="en-US").new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(site[1])
    assert attr(pg.locator("html"), "lang") == "en"
    assert pg.locator("h2").first.inner_text() == "Competitions"

    pg.click('.lang button[data-lang="tr"]')
    assert attr(pg.locator("html"), "lang") == "tr"
    assert pg.title() == "Türk Kadın Voleybolu Takvimi"
    assert pg.locator("h2").first.inner_text() == "Takvimler"
    assert pg.locator('.feed[data-feed="sultanlar"] .count').inner_text() == "95 maç"
    assert pg.locator('.feed[data-feed="europe"] h3').inner_text() == "Avrupa kupaları"
    assert pg.locator('#team option[value="team-aras-kargo"]').inner_text() == "Aras Kargo (19 maç)"
    assert "Hafta" in pg.locator(".match .meta").first.inner_text()
    assert (pg.locator(".day").first.text_content() or "").startswith(("Paz", "Pzt", "Sal", "Çar", "Per", "Cum", "Cmt"))
    assert "Son güncelleme" in pg.locator("footer").inner_text()
    assert attr(pg.locator('.lang button[data-lang="tr"]'), "aria-pressed") == "true"
    # Links don't depend on the language.
    assert attr(pg.locator('.feed[data-feed="all"] a.apple'), "href").endswith("/feeds/all.ics")

    pg.reload()  # the choice is remembered
    assert pg.locator("h2").first.inner_text() == "Takvimler"

    pg.click('.lang button[data-lang="en"]')
    assert pg.locator("h2").first.inner_text() == "Competitions"
    assert pg.locator('.feed[data-feed="sultanlar"] .count').inner_text() == "95 matches"
    assert pg.locator('#team option[value="team-aras-kargo"]').inner_text() == "Aras Kargo (19 matches)"
    assert "Last updated" in pg.locator("footer").inner_text()
    assert not errors, errors
    pg.context.close()


def test_turkish_browsers_and_lang_parameter_default_to_turkish(site: Site, browser: Browser) -> None:
    tr = browser.new_context(locale="tr-TR").new_page()
    tr.goto(site[1])
    assert attr(tr.locator("html"), "lang") == "tr"
    assert tr.locator("h2").first.inner_text() == "Takvimler"
    en = browser.new_context(locale="en-US").new_page()
    en.goto(site[1] + "?lang=tr")
    assert en.locator("h2").first.inner_text() == "Takvimler"
    for pg in (tr, en):
        pg.context.close()


def test_no_analytics_unless_configured(site: Site) -> None:
    assert "gc.zgo.at" not in (site[0] / "index.html").read_text(encoding="utf-8")


def test_analytics_script_when_configured(tmp_path: Path) -> None:
    build.build(tmp_path, sorted(matches(), key=lambda m: m.sort_key), datetime(2026, 11, 14, 12, tzinfo=timezone.utc),
                "my-site")
    page_html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert ('<script data-goatcounter="https://my-site.goatcounter.com/count" '
            'async src="https://gc.zgo.at/count.js"></script>') in page_html
    with pytest.raises(ValueError):
        build.analytics('x" onload="alert(1)')


def test_subscribe_buttons_are_counted(site: Site, browser: Browser) -> None:
    pg = browser.new_context(locale="en-US").new_page()
    # Stand-in for GoatCounter's script, which records what the page asks it to count.
    pg.add_init_script("window.counted = []; window.goatcounter = { count: o => window.counted.push(o) };")
    pg.route("**/*", lambda r: r.continue_() if r.request.url.startswith("http://127.0.0.1") else r.abort())
    pg.goto(site[1])
    pg.context.grant_permissions(["clipboard-read", "clipboard-write"])
    pg.click('.feed[data-feed="sultanlar"] button[data-action="copy"]')
    pg.click('.feed[data-feed="europe"] a[data-action="google"]')
    pg.select_option("#team", "team-aras-kargo")
    pg.click('.team-picker a[data-action="outlook"]')
    pg.click('.lang button[data-lang="tr"]')  # not a subscribe button
    counted = pg.evaluate("window.counted")
    assert [c["path"] for c in counted] == [
        "subscribe/copy/sultanlar", "subscribe/google/europe", "subscribe/outlook/team-aras-kargo"]
    assert all(c["event"] is True for c in counted)
    pg.context.close()
