"""Shared bits: the match model, feed definitions, and Turkish-aware text helpers."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ISTANBUL = ZoneInfo("Europe/Istanbul")

# Feed id -> (name, description). Order here is the order on the landing page.
FEEDS = {
    "sultanlar": ("Sultanlar Ligi", "Every match of the Turkish women's top division."),
    "europe": ("European cups", "Turkish clubs in the CEV Champions League, CEV Cup and Challenge Cup."),
    "world": ("Club World Championship", "Turkish clubs at the FIVB Club World Championship."),
    "national": ("Türkiye national team", "A Milli Kadın Takımı: VNL, European and World Championships, Olympics and more."),
    "cups": ("Turkish cups", "Kupa Voley and the Super Cup (Şampiyonlar Kupası)."),
}


@dataclass
class Match:
    id: str                 # stable across refreshes, used as the calendar event UID
    feed: str               # one of FEEDS
    competition: str        # e.g. "Sultanlar Ligi", "CEV Champions League"
    stage: str              # e.g. "Week 9", "Pool D", "Quarterfinal"
    home: str
    away: str
    date: str               # YYYY-MM-DD (Turkey time)
    time: str | None        # HH:MM (Turkey time); None when not announced yet
    venue: str = ""
    city: str = ""          # city or, for matches abroad, the country
    tv: str = ""
    result: str = ""
    teams: list[str] = field(default_factory=list)  # Turkish clubs playing, for team feeds

    @property
    def start(self) -> datetime | None:
        if not self.time:
            return None
        return datetime.fromisoformat(f"{self.date}T{self.time}").replace(tzinfo=ISTANBUL)

    @property
    def sort_key(self) -> tuple:
        return (self.date, self.time or "99:99", self.id)


def load(path: Path) -> list[Match]:
    if not path.exists():
        return []
    return [Match(**raw) for raw in json.loads(path.read_text(encoding="utf-8"))]


def save(path: Path, matches: list[Match]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    matches = sorted(matches, key=lambda m: m.sort_key)
    path.write_text(
        json.dumps([asdict(m) for m in matches], ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )


# --- text helpers -----------------------------------------------------------

_ASCII = str.maketrans("ıİşŞğĞüÜöÖçÇ", "iIsSgGuUoOcC")


def slugify(text: str) -> str:
    text = text.translate(_ASCII)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def squash(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def tr_lower(text: str) -> str:
    return text.replace("I", "ı").replace("İ", "i").lower()


def tr_upper(text: str) -> str:
    return text.replace("i", "İ").replace("ı", "I").upper()


# Short tokens that should stay upper case (club prefixes, acronyms).
_KEEP_UPPER = {"TVF", "TED", "İBA", "TRT", "VC", "SC", "SK", "VB", "HSC", "DOC", "CSM", "OK",
               "KV", "AO", "PGE", "NEC", "ABD", "UNI", "VFB", "CS", "CSO", "C.S.O.", "C.S.M.", "MKE",
               "BLD.", "T.E.B.", "II", "III", "ZOK", "RC", "AZS", "LKS", "MKS", "VK", "OK."}


# Brand / foreign words that appear in Turkish names but must not get a dotless ı.
_LATIN_WORDS = {"MEDICANA": "Medicana", "DAIKIN": "Daikin", "HDI": "HDI", "SINNADA": "Sinnada",
                "HOTELS": "Hotels", "PERON": "Peron", "YUPPO": "Yuppo", "SIGORTA": "Sigorta",
                "ARENA": "Arena", "HALL": "Hall", "CITY": "City", "CENTER": "Center", "PARK": "Park"}
_TR_HINTS = re.compile(r"[ÇĞİÖŞ]|\b(SPOR|SALONU?|BELEDİYE\w*|VOLEYBOL|SARAYI|KULÜBÜ)\b")


def looks_turkish(text: str) -> bool:
    return bool(_TR_HINTS.search(tr_upper(text)))


def pretty(text: str, turkish: bool | None = None) -> str:
    """'FENERBAHÇE MEDICANA' -> 'Fenerbahçe Medicana', keeping acronyms and '(SRB)' codes.

    turkish=None guesses from the text whether Turkish casing rules (I -> ı) apply.
    """
    text = squash(text)
    if not text:
        return text
    if turkish is None:
        turkish = looks_turkish(text)
    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.islower() for c in letters) > len(letters) / 3:
        return text  # already mixed case, trust it
    out = []
    for word in text.split(" "):
        if word.upper() in _LATIN_WORDS:
            out.append(_LATIN_WORDS[word.upper()])
            continue
        if word.startswith("(") or word.upper() in _KEEP_UPPER or any(ch.isdigit() for ch in word):
            out.append(word)
            continue
        parts = []
        for part in re.split(r"([-/.'])", word):
            if not part or not part[0].isalpha():
                parts.append(part)
            elif turkish:
                parts.append(tr_upper(part[0]) + tr_lower(part[1:]))
            else:
                parts.append(part[0].upper() + part[1:].lower())
        out.append("".join(parts))
    return " ".join(out)
