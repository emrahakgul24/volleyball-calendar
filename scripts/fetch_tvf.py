"""Download the Turkish Volleyball Federation (TVF) fixture spreadsheets and turn them into data/matches.json.

Sources (both published and kept up to date by TVF on https://tvf.org.tr/takvim):
  * Genel fikstür (.xlsx): every domestic match plus national team and some international club matches.
  * Avrupa kupaları (.xls): every European / world club match of Turkish clubs.

Times in both files are Turkey time (Europe/Istanbul).

Usage:
  python scripts/fetch_tvf.py                      # download from tvf.org.tr
  python scripts/fetch_tvf.py --genel X.xlsx --avrupa Y.xls   # parse local files
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import urllib.request
from collections import Counter
from datetime import date, datetime, time
from pathlib import Path
from typing import TypedDict
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import openpyxl
import xlrd

sys.path.insert(0, str(Path(__file__).parent))
from common import DATA, ISTANBUL, Match, load, pretty, save, slugify, squash, tr_upper  # noqa: E402

BASE = "https://tvf.org.tr"
UA = "Mozilla/5.0 (compatible; volleyball-calendar; +https://github.com/emrahakgul24/volleyball-calendar)"

# National team competition codes in the LİG column.
NATIONAL = {
    "VNL": "Volleyball Nations League",
    "WC": "World Championship",
    "EC": "European Championship",
    "OG": "Olympic Games",
    "OL": "Olympic Games",
    "OQT": "Olympic Qualification",
    "MG": "Mediterranean Games",
    "BC": "Balkan Cup",
    "EGL": "European Golden League",
    "ISG": "Islamic Solidarity Games",
    "EOG": "European Games",
}
CLUB = {
    "VSL": ("sultanlar", "Sultanlar Ligi"),
    "ASKV": ("cups", "Kupa Voley"),
    "ASŞK": ("cups", "Super Cup"),
    "CL": ("europe", "CEV Champions League"),
    "CC": ("europe", "CEV Cup"),
    "CH": ("europe", "CEV Challenge Cup"),
    "CWC": ("world", "Club World Championship"),
}

STAGES = [
    (r"^LE$", ""),
    (r"^GRUP$|^GROUP$", "Group"),
    (r"^(\d+)\.\s*RAUND$|^(\d+)\.\s*ROUND$", "Round {n}"),
    (r"^1/2 FİNAL$|^YARI FİNAL$", "Semifinal"),
    (r"^1/4 FİNAL$|^ÇEYREK FİNAL$", "Quarterfinal"),
    (r"^1/8 FİNAL$|^SON 16$", "Round of 16"),
    (r"^1/16 FİNAL$|^SON 32$", "Round of 32"),
    (r"^1/32 FİNAL$", "Round of 64"),
    (r"^FİNAL$|^FINAL$", "Final"),
    (r"^3\.\s*/\s*4\.?$|^3\.LÜK$|^ÜÇÜNCÜLÜK$", "3rd place match"),
    (r"^PLAY ?-?OFF$|^PO$", "Playoffs"),
    (r"^PLAY ?-?OUT$", "Playout"),
    (r"^ELEME$", "Qualification"),
]


def stage_name(kind: str) -> str:
    k = tr_upper(squash(kind))
    for pattern, label in STAGES:
        m = re.match(pattern, k)
        if m:
            n = next((g for g in m.groups() if g), "") if m.groups() else ""
            return label.format(n=n)
    return pretty(kind)


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "tr-TR,tr;q=0.9"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data: bytes = resp.read()
    return data


def season_start(today: date | None = None) -> int:
    today = today or date.today()
    return today.year if today.month >= 7 else today.year - 1


def discover_urls() -> tuple[str, str]:
    """Find the current spreadsheet links on the TVF calendar page, with a fallback to the usual paths."""
    y = season_start()
    genel = f"{BASE}/_dosyalar/Lig_Sezon_Arsivi/Genel_Fiksturler/{y}-{y + 1}_genel_fikstur.xlsx"
    avrupa = f"{BASE}/_dosyalar/ddosyalar/{y}-{y + 1}/{y}-{y + 1}_avrupa_kupalari.xls"
    try:
        html = fetch(f"{BASE}/takvim").decode("utf-8", "replace")
        if m := re.search(r'href="([^"]*genel_fikstur\.xlsx)"', html, re.I):
            genel = urljoin(BASE, m.group(1))
        if m := re.search(r'href="([^"]*avrupa_kupalari\.xlsx?)"', html, re.I):
            avrupa = urljoin(BASE, m.group(1))
    except Exception as exc:  # the fallback paths are still worth trying
        print(f"warning: could not read {BASE}/takvim: {exc}", file=sys.stderr)
    return genel, avrupa


# --- team names --------------------------------------------------------------

class Teams:
    """Maps the many spellings of a Turkish club to one display name, keyed by its first word."""

    def __init__(self) -> None:
        self.names: dict[str, Counter[str]] = {}

    @staticmethod
    def key(name: str) -> str:
        return tr_upper(squash(name)).split(" ")[0] if name else ""

    def add(self, name: str) -> None:
        self.names.setdefault(self.key(name), Counter())[squash(name)] += 1

    def canonical(self, name: str) -> str | None:
        c = self.names.get(self.key(name))
        return pretty(c.most_common(1)[0][0], turkish=True) if c else None


def placeholder(name: str) -> str | None:
    """'3.ROUND CLVW 11/12 MAÇINI KAZANAN TAKIM' -> 'Winner of CLVW 11/12'."""
    m = re.search(r"([A-ZÇĞİÖŞÜ]+\s*[\d/ ]+)\s*MAÇINI\s+(KAZANAN|KAYBEDEN)", tr_upper(name))
    if not m:
        return None
    return f"{'Winner' if m.group(2) == 'KAZANAN' else 'Loser'} of {squash(m.group(1))}"


def team_display(name: str, teams: Teams) -> str:
    return placeholder(name) or teams.canonical(name) or pretty(name, turkish=False)


# --- genel fikstür -------------------------------------------------------------

# One spreadsheet row keyed by column name ("date", "home", ...). Cell values are whatever openpyxl
# returns (str, datetime, time, numbers or None), so they are narrowed where used.
Row = dict[str, object]

def parse_genel(content: bytes) -> tuple[list[Row], Teams]:
    ws = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True).worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = [tr_upper(squash(h)) for h in next(rows)]

    def col(*names: str) -> int:
        for n in names:
            if n in header:
                return header.index(n)
        raise KeyError(f"column {names} not found in {header}")

    c = {
        "date": col("TARİH"), "tv": col("TV"), "city": col("İL (ÜLKE*)", "İL"),
        "venue": col("SALON (ŞEHİR*)", "SALON"), "time": col("SAAT"), "home": col("A TAKIMI"),
        "away": col("B TAKIMI"), "result": col("SONUÇ"), "league": col("LİG"), "gender": col("E-K"),
        "kind": col("TÜR"), "group": col("GRUP"), "week": col("HAFTA"), "no": col("NO"),
        "scope": col("MT/KULÜP"),
    }
    out: list[Row] = []
    teams = Teams()
    for row in rows:
        r: Row = {k: row[i] if i < len(row) else None for k, i in c.items()}
        if tr_upper(squash(r["gender"])) != "K" or not isinstance(r["date"], (datetime, date)):
            continue
        r["league"] = tr_upper(squash(r["league"]))
        if r["league"] == "VSL":
            teams.add(squash(r["home"]))
            teams.add(squash(r["away"]))
        out.append(r)
    return out, teams


def genel_matches(rows: list[Row], teams: Teams) -> list[Match]:
    matches: list[Match] = []
    for r in rows:
        code, national = squash(r["league"]), tr_upper(squash(r["scope"])) == "MT"
        home, away = squash(r["home"]), squash(r["away"])
        if national:
            if "TÜRKİYE" not in (tr_upper(home), tr_upper(away)):
                continue
            feed, competition = "national", NATIONAL.get(code, code)
        elif code in CLUB:
            feed, competition = CLUB[code]
        else:
            continue

        raw_date = r["date"]
        assert isinstance(raw_date, date)  # parse_genel only keeps rows with a date
        d = raw_date.date() if isinstance(raw_date, datetime) else raw_date
        t: object = r["time"]
        if isinstance(t, datetime):
            t = t.time()
        if isinstance(t, str):
            if "İPTAL" in tr_upper(t) or "IPTAL" in t.upper():
                continue  # cancelled
            m = re.match(r"(\d{1,2})[:.](\d{2})", t)
            t = time(int(m.group(1)), int(m.group(2))) if m else None
        hhmm = t.strftime("%H:%M") if isinstance(t, time) else None

        week, group = squash(r["week"]), squash(r["group"])
        stage = stage_name(squash(r["kind"]))
        if code == "VSL" and week:
            stage = f"{stage} - Week {week}" if stage else f"Week {week}"
        elif group and stage in ("", "Group") or (code == "CL" and group):
            # Pool phase. In Kupa Voley the group is a number, in CEV and FIVB events a letter.
            stage = f"{'Pool' if feed in ('europe', 'world') else 'Group'} {group}"

        if national:
            home_d, away_d, club_teams = pretty(home, True), pretty(away, True), list[str]()
        else:
            home_d, away_d = team_display(home, teams), team_display(away, teams)
            club_teams = [n for n in (teams.canonical(home), teams.canonical(away)) if n]

        no = squash(r["no"])
        if no and re.match(r"^[A-Z]{3}\d{2}-", no):
            mid = f"tvf-{slugify(no)}"
        else:
            mid = f"tvf-{feed}-{d.isoformat()}-{slugify(home)}-{slugify(away)}"
        city = squash(r["city"])
        matches.append(Match(
            id=mid, feed=feed, competition=competition, stage=stage,
            home=home_d, away=away_d, date=d.isoformat(), time=hhmm,
            venue=pretty(squash(r["venue"])), city=pretty(city, True),
            tv=squash(r["tv"]), result=squash(r["result"]), teams=club_teams,
        ))
    return matches


# --- avrupa kupaları -----------------------------------------------------------

# The European cups file gives CEV matches abroad in the venue's local time (as CEV publishes them),
# so those are converted to Turkey time. Club World Championship rows are already in Turkey time.
COUNTRY_TZ = {
    "ALMANYA": "Europe/Berlin", "ARNAVUTLUK": "Europe/Tirane", "AVUSTURYA": "Europe/Vienna",
    "AZERBAYCAN": "Asia/Baku", "BELARUS": "Europe/Minsk", "BELÇİKA": "Europe/Brussels",
    "BOSNA HERSEK": "Europe/Sarajevo", "BULGARİSTAN": "Europe/Sofia", "ÇEKYA": "Europe/Prague",
    "ÇEK CUMHURİYETİ": "Europe/Prague", "DANİMARKA": "Europe/Copenhagen", "ESTONYA": "Europe/Tallinn",
    "FİNLANDİYA": "Europe/Helsinki", "FRANSA": "Europe/Paris", "GÜRCİSTAN": "Asia/Tbilisi",
    "HIRVATİSTAN": "Europe/Zagreb", "HOLLANDA": "Europe/Amsterdam", "İNGİLTERE": "Europe/London",
    "İSPANYA": "Europe/Madrid", "İSRAİL": "Asia/Jerusalem", "İSVEÇ": "Europe/Stockholm",
    "İSVİÇRE": "Europe/Zurich", "İTALYA": "Europe/Rome", "KARADAĞ": "Europe/Podgorica",
    "KIBRIS": "Asia/Nicosia", "KOSOVA": "Europe/Belgrade", "KUZEY MAKEDONYA": "Europe/Skopje",
    "LETONYA": "Europe/Riga", "LİTVANYA": "Europe/Vilnius", "LÜKSEMBURG": "Europe/Luxembourg",
    "MACARİSTAN": "Europe/Budapest", "MOLDOVA": "Europe/Chisinau", "NORVEÇ": "Europe/Oslo",
    "POLONYA": "Europe/Warsaw", "PORTEKİZ": "Europe/Lisbon", "ROMANYA": "Europe/Bucharest",
    "RUSYA": "Europe/Moscow", "SIRBİSTAN": "Europe/Belgrade", "SLOVAKYA": "Europe/Bratislava",
    "SLOVENYA": "Europe/Ljubljana", "UKRAYNA": "Europe/Kyiv", "YUNANİSTAN": "Europe/Athens",
}


def local_to_turkey(d: date, hhmm: str, country: str) -> tuple[date, str]:
    tz = COUNTRY_TZ.get(tr_upper(squash(country)))
    if not tz:
        print(f"warning: no time zone known for {country!r}; leaving {d} {hhmm} unconverted", file=sys.stderr)
        return d, hhmm
    local = datetime.fromisoformat(f"{d.isoformat()}T{hhmm}").replace(tzinfo=ZoneInfo(tz))
    tr = local.astimezone(ISTANBUL)
    return tr.date(), tr.strftime("%H:%M")

Cell = str | float  # xlrd gives text, or a float for numbers and times of day


class AvrupaRecord(TypedDict):
    date: str
    no: Cell
    place: list[str]
    time: Cell
    teams: str
    comp: str
    result: str


def parse_avrupa(content: bytes, teams: Teams, season: int) -> list[Match]:
    sheet = xlrd.open_workbook(file_contents=content).sheet_by_index(0)
    rows: list[list[Cell]] = [[v if isinstance(v, float) else squash(v) for v in sheet.row_values(i)]
                              for i in range(sheet.nrows)]

    def cell(r: list[Cell], i: int) -> Cell:
        return r[i] if i < len(r) else ""

    records: list[AvrupaRecord] = []
    current: AvrupaRecord | None = None
    for r in rows[1:]:
        first = cell(r, 0)
        if isinstance(first, str) and re.match(r"^\d{1,2}\.\d{1,2}\.\d{4}$", first) or (
                isinstance(first, str) and "BELLİ" in tr_upper(first)):
            current = {"date": first, "no": cell(r, 1), "place": [squash(cell(r, 2))], "time": cell(r, 3),
                       "teams": squash(cell(r, 4)), "comp": "", "result": squash(cell(r, 7))}
            records.append(current)
        elif current is not None:
            if cell(r, 2):
                current["place"].append(squash(cell(r, 2)))
            if cell(r, 4) and not current["comp"]:
                current["comp"] = squash(cell(r, 4))
            if cell(r, 7) and not current["result"]:
                current["result"] = squash(cell(r, 7))

    matches: list[Match] = []
    for rec in records:
        comp = tr_upper(rec["comp"])
        if "KADIN" not in comp or "BELLİ" in tr_upper(rec["date"]):
            continue
        if "ŞAMPİYONLAR LİGİ" in comp:
            feed, competition = "europe", "CEV Champions League"
        elif "CHALLENGE" in comp:
            feed, competition = "europe", "CEV Challenge Cup"
        elif "KUPASI" in comp and ("CEV" in comp or "VOLEYBOL" in comp) and "BALKAN" not in comp:
            feed, competition = "europe", "CEV Cup"
        elif "DÜNYA" in comp:
            feed, competition = "world", "Club World Championship"
        elif "BALKAN" in comp:
            feed, competition = "europe", "Balkan Cup"
        else:
            feed, competition = "europe", pretty(rec["comp"])

        stage = ""
        if m := re.search(r"\b([A-H])\s+GRUBU", comp):
            stage = f"Pool {m.group(1)}"
        elif m := re.search(r"(1/\d+ FİNAL|ÇEYREK FİNAL|YARI FİNAL|FİNAL|\d\.\s*ROUND)", comp):
            stage = stage_name(m.group(1))

        home, _, away = rec["teams"].partition("-")
        d = datetime.strptime(rec["date"], "%d.%m.%Y").date()
        hhmm: str | None = None
        t = rec["time"]
        if isinstance(t, float) and 0 <= t < 1:
            minutes = round(t * 24 * 60)
            hhmm = f"{minutes // 60:02d}:{minutes % 60:02d}"
        place = squash(" ".join(p for p in rec["place"] if p))
        is_hall = any(w in tr_upper(place) for w in ("SALON", "SARAY", "ARENA", "HALL", "KOMPLEKS"))
        if hhmm and feed == "europe" and not is_hall:
            d, hhmm = local_to_turkey(d, hhmm, place)
        no = rec["no"] if isinstance(rec["no"], str) else str(int(rec["no"]))
        matches.append(Match(
            id=f"tvf-{season}-{slugify(competition)}-{slugify(no)}",
            feed=feed, competition=competition, stage=stage,
            home=team_display(home, teams), away=team_display(away, teams),
            date=d.isoformat(), time=hhmm,
            venue=pretty(place) if is_hall else "", city="" if is_hall else pretty(place, True),
            result=squash(rec["result"]),
            teams=[n for n in (teams.canonical(home), teams.canonical(away)) if n],
        ))
    return matches


def merge(genel: list[Match], avrupa: list[Match]) -> list[Match]:
    """Combine the two files' international club matches, keeping one copy of each match.

    The European cups file mirrors CEV's schedule and lists every match, so its date and time win. The
    general file adds TV channels, results and opponents' country codes. The two occasionally disagree
    on the date by a day, so a twin is the same competition and Turkish club within one day.
    """
    out = [m for m in genel if m.feed not in ("europe", "world")]
    intl = [m for m in genel if m.feed in ("europe", "world")]

    def gap(a: Match, g: Match) -> tuple[int, bool]:
        days = abs((date.fromisoformat(a.date) - date.fromisoformat(g.date)).days)
        return (days, a.time != g.time)

    for a in avrupa:
        candidates = [g for g in intl if g.competition == a.competition and set(g.teams) & set(a.teams)
                      and gap(a, g)[0] <= 1]
        twin = min(candidates, key=lambda g: gap(a, g), default=None)
        if twin:
            intl.remove(twin)
            a.home, a.away = twin.home, twin.away
            a.tv = twin.tv or a.tv
            a.result = twin.result or a.result
            a.venue = a.venue or twin.venue
            a.city = twin.city or a.city
            a.stage = a.stage or twin.stage
        out.append(a)
    return out + intl


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--genel", help="local genel fikstür .xlsx")
    ap.add_argument("--avrupa", help="local avrupa kupaları .xls")
    ap.add_argument("--out", default=str(DATA / "matches.json"))
    args = ap.parse_args()

    genel_url, avrupa_url = discover_urls() if not (args.genel and args.avrupa) else ("", "")
    genel_bytes = open(args.genel, "rb").read() if args.genel else fetch(genel_url)
    rows, teams = parse_genel(genel_bytes)
    genel = genel_matches(rows, teams)

    avrupa = []
    try:
        avrupa_bytes = open(args.avrupa, "rb").read() if args.avrupa else fetch(avrupa_url)
        m = re.search(r"(20\d\d)-20\d\d", args.avrupa or avrupa_url)
        avrupa = parse_avrupa(avrupa_bytes, teams, int(m.group(1)) if m else season_start())
    except Exception as exc:
        print(f"warning: European cups file skipped: {exc}", file=sys.stderr)

    matches = merge(genel, avrupa)
    if len([m for m in matches if m.feed == "sultanlar"]) < 7:
        print("error: fewer than 7 Sultanlar Ligi matches parsed; keeping the previous data", file=sys.stderr)
        return 1

    # Keep matches from earlier refreshes that dropped out of the files (e.g. a new season's file
    # replacing the old one), so past events don't vanish from people's calendars.
    out_path = Path(args.out)
    fresh = {m.id for m in matches}
    first_new = min(m.date for m in matches)
    kept = [m for m in load(out_path) if m.id not in fresh and m.date < first_new]
    save(out_path, matches + kept)
    by_feed = Counter(m.feed for m in matches)
    print(f"saved {len(matches)} matches ({dict(by_feed)}), kept {len(kept)} older ones -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
