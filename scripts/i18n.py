"""Turkish versions of the names and labels the build generates (the page's fixed text is in the template)."""

from __future__ import annotations

import re

FEEDS_TR = {
    "all": ("Türk kadın voleybolu", "Sultanlar Ligi, Avrupa kupaları, Kulüpler Dünya Şampiyonası, "
            "A Milli Kadın Takımı ve Türkiye kupaları tek takvimde."),
    "sultanlar": ("Sultanlar Ligi", "Kadın voleybolunda en üst ligin tüm maçları."),
    "europe": ("Avrupa kupaları", "Türk takımlarının CEV Şampiyonlar Ligi, CEV Kupası ve CEV Challenge Kupası maçları."),
    "world": ("Kulüpler Dünya Şampiyonası", "Türk takımlarının FIVB Kulüpler Dünya Şampiyonası maçları."),
    "national": ("A Milli Kadın Takımı", "Milletler Ligi, Avrupa ve Dünya Şampiyonaları, Olimpiyatlar ve daha fazlası."),
    "cups": ("Türkiye kupaları", "Kupa Voley ve Şampiyonlar Kupası."),
}

TEAM_DESC_TR = "{team} takımının ligde, Türkiye kupalarında ve Avrupa kupalarında oynadığı tüm maçlar."

COMPETITIONS_TR = {
    "CEV Champions League": "CEV Şampiyonlar Ligi",
    "CEV Cup": "CEV Kupası",
    "CEV Challenge Cup": "CEV Challenge Kupası",
    "Club World Championship": "Kulüpler Dünya Şampiyonası",
    "Super Cup": "Şampiyonlar Kupası",
    "Volleyball Nations League": "Milletler Ligi",
    "World Championship": "Dünya Şampiyonası",
    "European Championship": "Avrupa Şampiyonası",
    "Olympic Games": "Olimpiyat Oyunları",
    "Olympic Qualification": "Olimpiyat Elemeleri",
    "Mediterranean Games": "Akdeniz Oyunları",
    "Balkan Cup": "Balkan Kupası",
    "European Golden League": "Avrupa Altın Ligi",
    "Islamic Solidarity Games": "İslami Dayanışma Oyunları",
    "European Games": "Avrupa Oyunları",
}

_STAGES_TR = [
    (r"^Week (\d+)$", r"\1. Hafta"),
    (r"^(?:Pool|Group) (\w+)$", r"\1 Grubu"),
    (r"^Round (\d+)$", r"\1. Tur"),
    (r"^Round of (\d+)$", r"Son \1"),
    (r"^Quarterfinal$", "Çeyrek final"),
    (r"^Semifinal$", "Yarı final"),
    (r"^Final$", "Final"),
    (r"^3rd place match$", "Üçüncülük maçı"),
    (r"^Playoffs$", "Play-off"),
    (r"^Playout$", "Play-out"),
    (r"^Qualification$", "Eleme"),
]


def competition_tr(name: str) -> str:
    return COMPETITIONS_TR.get(name, name)


def stage_tr(stage: str) -> str:
    """'Playoffs - Week 3' -> 'Play-off - 3. Hafta'; unknown parts are left as they are."""
    parts = []
    for part in stage.split(" - "):
        for pattern, repl in _STAGES_TR:
            if re.match(pattern, part):
                part = re.sub(pattern, repl, part)
                break
        parts.append(part)
    return " - ".join(parts)


def team_tr(name: str) -> str:
    """Placeholders such as 'Winner of CLVW 11/12' -> 'CLVW 11/12 galibi'."""
    m = re.match(r"^(Winner|Loser) of (.+)$", name)
    if not m:
        return name
    return f"{m.group(2)} {'galibi' if m.group(1) == 'Winner' else 'mağlubu'}"
