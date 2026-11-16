"""Build the static site: one .ics feed per competition, per team and combined, plus the landing page.

Usage: python scripts/build.py [--out site]
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).parent))
from common import DATA, FEEDS, ISTANBUL, ROOT, Match, load, slugify  # noqa: E402
from i18n import FEEDS_TR, TEAM_DESC_TR, competition_tr, stage_tr, team_tr  # noqa: E402

class Feed(TypedDict):
    id: str
    name: str
    desc: str
    name_tr: str
    desc_tr: str
    kind: str  # "all", "competition" or "team"
    count: int


class BuildResult(TypedDict):
    feeds: int
    matches: int


PRODID = "-//volleyball-calendar//Turkish women's volleyball//EN"
DURATION = timedelta(hours=2)


# --- iCalendar ---------------------------------------------------------------

def esc(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def fold(line: str) -> str:
    """Fold to 75-octet lines as RFC 5545 requires, without splitting a UTF-8 character."""
    out: list[str] = []
    current = b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(current) + len(b) > (75 if not out else 74):
            out.append(current.decode("utf-8"))
            current = b""
        current += b
    out.append(current.decode("utf-8"))
    return "\r\n ".join(out)


def utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def summary(m: Match, with_competition: bool) -> str:
    score = m.result.split(" ")[0] if m.result else ""
    core = f"{m.home} {score} {m.away}" if score else f"{m.home} - {m.away}"
    return f"{core} ({m.competition})" if with_competition else core


def description(m: Match) -> str:
    lines = [" · ".join(x for x in (m.competition, m.stage) if x)]
    if m.result:
        lines.append(f"Result: {m.result}")
    if m.tv:
        lines.append(f"TV: {m.tv}")
    if not m.time:
        lines.append("Start time not announced yet.")
    lines.append("Times are kept up to date from the Turkish Volleyball Federation (tvf.org.tr) fixtures.")
    return "\n".join(lines)


def vevent(m: Match, stamp: str, with_competition: bool) -> list[str]:
    lines = ["BEGIN:VEVENT", f"UID:{m.id}@volleyball-calendar", f"DTSTAMP:{stamp}"]
    if m.start:
        lines += [f"DTSTART:{utc(m.start)}", f"DTEND:{utc(m.start + DURATION)}"]
    else:
        d = date.fromisoformat(m.date)
        lines += [f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}",
                  "TRANSP:TRANSPARENT"]
    lines.append(f"SUMMARY:{esc(summary(m, with_competition))}")
    location = ", ".join(x for x in (m.venue, m.city) if x)
    if location:
        lines.append(f"LOCATION:{esc(location)}")
    lines.append(f"DESCRIPTION:{esc(description(m))}")
    lines.append(f"CATEGORIES:{esc(m.competition)}")
    lines.append("END:VEVENT")
    return lines


def calendar(name: str, desc: str, matches: list[Match], stamp: str, with_competition: bool) -> str:
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{esc(name)}", f"X-WR-CALDESC:{esc(desc)}", "X-WR-TIMEZONE:Europe/Istanbul",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H",
    ]
    for m in matches:
        lines += vevent(m, stamp, with_competition)
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"


# --- site --------------------------------------------------------------------

def build(out: Path, matches: list[Match], now: datetime, goatcounter: str = "") -> BuildResult:
    if out.exists():
        shutil.rmtree(out)
    (out / "feeds").mkdir(parents=True)
    stamp = utc(now)

    feeds: list[Feed] = []

    def write(fid: str, name: str, desc: str, ms: list[Match], kind: str, mixed: bool) -> None:
        (out / "feeds" / f"{fid}.ics").write_text(calendar(name, desc, ms, stamp, mixed), encoding="utf-8",
                                                  newline="")
        name_tr, desc_tr = FEEDS_TR.get(fid, (name, TEAM_DESC_TR.format(team=name)))
        feeds.append({"id": fid, "name": name, "desc": desc, "name_tr": name_tr, "desc_tr": desc_tr,
                      "kind": kind, "count": len(ms)})

    write("all", "Turkish women's volleyball", "Sultanlar Ligi, European cups, Club World Championship, "
          "Türkiye national team and Turkish cups in one calendar.", matches, "all", True)
    for fid, (name, desc) in FEEDS.items():
        write(fid, name, desc, [m for m in matches if m.feed == fid], "competition", fid != "sultanlar")

    teams = sorted({t for m in matches if m.feed == "sultanlar" for t in m.teams}, key=slugify)
    for team in teams:
        ms = [m for m in matches if team in m.teams]
        write(f"team-{slugify(team)}", team, f"Every {team} match: league, cups and Europe.", ms, "team", True)

    upcoming = [m for m in matches if m.date >= now.astimezone(ISTANBUL).date().isoformat()][:40]
    page = render_page(feeds, upcoming, now, goatcounter)
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / ".nojekyll").write_text("")
    (out / "feeds.json").write_text(json.dumps(feeds, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"feeds": len(feeds), "matches": len(matches)}


def render_page(feeds: list[Feed], upcoming: list[Match], now: datetime, goatcounter: str = "") -> str:
    """Fill the template. Every generated text carries its Turkish version in data-tr; the page's script
    swaps between the two when the reader picks a language."""
    template = (ROOT / "site_template" / "index.html").read_text(encoding="utf-8")
    e = html.escape

    def t(en: str, tr: str, tag: str = "span", cls: str = "") -> str:
        c = f' class="{cls}"' if cls else ""
        return f'<{tag}{c} data-tr="{e(tr)}">{e(en)}</{tag}>'

    def card(f: Feed) -> str:
        return f"""
      <article class="feed" data-feed="{e(f['id'])}" data-name="{e(f['name'])}">
        <div class="feed-head">
          {t(f['name'], f['name_tr'], 'h3')}
          {t(f"{f['count']} matches", f"{f['count']} maç", 'span', 'count')}
        </div>
        {t(f['desc'], f['desc_tr'], 'p')}
        <div class="actions">
          <a class="btn google" data-action="google" href="#" data-tr="Google Takvim">Google Calendar</a>
          <a class="btn apple" data-action="apple" href="#" data-tr="Apple Takvim">Apple Calendar</a>
          <a class="btn outlook" data-action="outlook" href="#">Outlook</a>
          <button class="btn copy" data-action="copy" type="button" data-tr="Bağlantıyı kopyala">Copy link</button>
        </div>
      </article>"""

    main = [f for f in feeds if f["kind"] in ("all", "competition")]
    teams = [f for f in feeds if f["kind"] == "team"]
    team_options = "\n".join(
        f'<option value="{e(f["id"])}" data-name="{e(f["name"])}" '
        f'data-tr="{e(f["name"])} ({f["count"]} maç)">{e(f["name"])} ({f["count"]} matches)</option>'
        for f in teams)

    rows = []
    last_day = None
    for m in upcoming:
        d = date.fromisoformat(m.date)
        if d != last_day:
            rows.append(f'<li class="day" data-date="{d.isoformat()}">{d:%a %d %b %Y}</li>')
            last_day = d
        meta_en = " · ".join(x for x in (m.competition, m.stage, m.tv) if x)
        meta_tr = " · ".join(x for x in (competition_tr(m.competition), stage_tr(m.stage), m.tv) if x)
        time = e(m.time) if m.time else t("TBA", "Saat belli değil")
        rows.append(f"""<li class="match">
          <span class="time">{time}</span>
          <span class="teams">{t(m.home, team_tr(m.home))} <span class="vs">vs</span> {t(m.away, team_tr(m.away))}</span>
          {t(meta_en, meta_tr, 'span', 'meta')}
        </li>""")
    empty = f'<li class="empty">{t("No upcoming matches published yet.", "Yaklaşan maçlar henüz açıklanmadı.")}</li>'
    updated = now.astimezone(ISTANBUL)
    stamp = f'<time data-datetime="{updated.isoformat()}">{updated:%d %b %Y, %H:%M}</time>'

    return (template
            .replace("{{FEED_CARDS}}", "\n".join(card(f) for f in main))
            .replace("{{TEAM_OPTIONS}}", team_options)
            .replace("{{UPCOMING}}", "\n".join(rows) or empty)
            .replace("{{UPDATED_TR}}", e(stamp))  # inside an attribute, so escaped
            .replace("{{UPDATED}}", stamp)
            .replace("{{ANALYTICS}}", analytics(goatcounter)))


def analytics(code: str) -> str:
    """GoatCounter's script tag for the site code `code` (https://<code>.goatcounter.com), or nothing if unset.

    GoatCounter sets no cookies and stores no personal data, so the page needs no consent banner."""
    if not code:
        return ""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", code):
        raise ValueError(f"not a GoatCounter site code: {code!r}")
    return (f'<script data-goatcounter="https://{code}.goatcounter.com/count" '
            'async src="https://gc.zgo.at/count.js"></script>')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "site"))
    args = ap.parse_args()
    # data/manual.json holds hand-added matches (same format); an entry with a scraped match's id replaces it.
    by_id = {m.id: m for m in load(DATA / "matches.json") + load(DATA / "manual.json")}
    # Visitor counting is on when the GOATCOUNTER_CODE repository variable is set (see README).
    goatcounter = os.environ.get("GOATCOUNTER_CODE", "").strip().lower()
    result = build(Path(args.out), sorted(by_id.values(), key=lambda m: m.sort_key), datetime.now(timezone.utc),
                   goatcounter)
    print(f"built {result['feeds']} feeds from {result['matches']} matches -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
