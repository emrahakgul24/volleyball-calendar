"""Parser and feed checks against TVF files saved in tests/fixtures (2026-27 season, 14 Nov 2026)."""

import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import build  # noqa: E402
from common import Match  # noqa: E402
import fetch_tvf  # noqa: E402
from common import pretty  # noqa: E402


def matches() -> list[Match]:
    rows, teams = fetch_tvf.parse_genel((HERE / "fixtures" / "genel_fikstur.xlsx").read_bytes())
    genel = fetch_tvf.genel_matches(rows, teams)
    avrupa = fetch_tvf.parse_avrupa((HERE / "fixtures" / "avrupa_kupalari.xls").read_bytes(), teams, 2026)
    return fetch_tvf.merge(genel, avrupa)


def by_id(ms: list[Match], mid: str) -> Match:
    return next(m for m in ms if m.id == mid)


def test_sultanlar_ligi() -> None:
    ms = [m for m in matches() if m.feed == "sultanlar"]
    assert len(ms) >= 91
    m = by_id(ms, "tvf-sul26-le004")
    assert (m.home, m.away, m.date, m.time, m.stage) == ("Vakıfbank", "Beşiktaş", "2026-10-03", "14:00", "Week 1")
    assert m.start is not None and m.start.astimezone(timezone.utc).hour == 11  # 14:00 Istanbul = 11:00 UTC
    assert len({t for m in ms for t in m.teams}) == 14


def test_european_matches_abroad_are_converted_to_turkey_time() -> None:
    ms = matches()
    lodz = by_id(ms, "tvf-2026-cev-champions-league-clvw-61")  # 18:00 in Łódź
    assert (lodz.date, lodz.time) == ("2026-11-25", "20:00")
    assert lodz.home.startswith("PGE Budowlani") and lodz.away == "Galatasaray Daikin"
    home = by_id(ms, "tvf-2026-cev-champions-league-clvw-13")  # in Istanbul, unchanged
    assert (home.date, home.time) == ("2026-11-25", "19:00")


def test_no_duplicates_between_the_two_files() -> None:
    ms = matches()
    assert len(ms) == len({m.id for m in ms})
    keys = [(m.date, m.home, m.away) for m in ms]
    assert len(keys) == len(set(keys))
    cwc = [m for m in ms if m.feed == "world"]
    assert len(cwc) == 6


def test_national_team_only_turkiye_matches() -> None:
    ms = [m for m in matches() if m.feed == "national"]
    assert ms and all("Türkiye" in (m.home, m.away) for m in ms)


def test_pretty_names() -> None:
    assert pretty("FENERBAHÇE MEDICANA", True) == "Fenerbahçe Medicana"
    assert pretty("VAKIFBANK SPOR SARAYI") == "Vakıfbank Spor Sarayı"
    assert pretty("ATINA-ZIRINEIO SPORT HALL") == "Atina-Zirineio Sport Hall"


def test_build_writes_valid_feeds(tmp_path: Path) -> None:
    ms = sorted(matches(), key=lambda m: m.sort_key)
    build.build(tmp_path, ms, datetime(2026, 11, 14, 12, tzinfo=timezone.utc))
    ics = (tmp_path / "feeds" / "sultanlar.ics").read_bytes()
    assert ics.startswith(b"BEGIN:VCALENDAR\r\n") and ics.endswith(b"END:VCALENDAR\r\n")
    assert all(len(line) <= 75 for line in ics.split(b"\r\n"))
    assert ics.count(b"BEGIN:VEVENT") == len([m for m in ms if m.feed == "sultanlar"])
    assert (tmp_path / "feeds" / "team-vakifbank.ics").exists()
    assert "Google Calendar" in (tmp_path / "index.html").read_text(encoding="utf-8")


def test_turkish_labels() -> None:
    from i18n import competition_tr, stage_tr, team_tr
    assert stage_tr("Week 9") == "9. Hafta"
    assert stage_tr("Pool C") == "C Grubu"
    assert stage_tr("Round of 32") == "Son 32"
    assert stage_tr("Playoffs - Week 3") == "Play-off - 3. Hafta"
    assert competition_tr("CEV Champions League") == "CEV Şampiyonlar Ligi"
    assert team_tr("Winner of CLVW 11/12") == "CLVW 11/12 galibi"
    assert team_tr("Vakıfbank") == "Vakıfbank"
