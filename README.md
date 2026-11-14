# Turkish women's volleyball calendar

Subscribable calendars for Turkish women's volleyball, hosted on GitHub Pages:

**https://emrahakgul24.github.io/volleyball-calendar/**

| Feed | What's in it |
| --- | --- |
| `all.ics` | Everything below in one calendar |
| `sultanlar.ics` | Every Sultanlar Ligi match |
| `europe.ics` | Turkish clubs in the CEV Champions League, CEV Cup and Challenge Cup |
| `world.ics` | Turkish clubs at the FIVB Club World Championship |
| `national.ics` | Türkiye women's national team (VNL, European / World Championships, Olympics, …) |
| `cups.ics` | Kupa Voley and the Super Cup |
| `team-<club>.ics` | One per Sultanlar Ligi club (league, cups and Europe) |

The landing page has one-click **Google Calendar**, **Apple Calendar** and **Outlook** buttons for each feed,
plus a copyable link for any other calendar app. It's in English and Turkish: readers pick with the EN / TR
switch in the header, Turkish-language browsers get Turkish by default, and `?lang=tr` links straight to Turkish.
Page texts live in `site_template/index.html` (`data-tr` attributes), generated labels in `scripts/i18n.py`. Feed URLs look like
`https://emrahakgul24.github.io/volleyball-calendar/feeds/sultanlar.ics` (use `webcal://` instead of `https://`
to subscribe from Apple devices).

## Where the data comes from

The Turkish Volleyball Federation publishes two spreadsheets on [tvf.org.tr/takvim](https://tvf.org.tr/takvim)
and keeps them current during the season:

* **Genel fikstür** (`*_genel_fikstur.xlsx`): every domestic match with time, venue and TV channel, plus national
  team and some international club matches.
* **Avrupa kupaları** (`*_avrupa_kupalari.xls`): all European and world club matches of Turkish clubs.

`scripts/fetch_tvf.py` finds the current files on the TVF site, keeps women's matches, merges the two files
without duplicates and writes `data/matches.json`. Times are stored in Turkey time. The European cups file lists
matches abroad in local time, as CEV does, so those are converted using the host country's time zone.

`scripts/build.py` turns `data/matches.json` into the `.ics` feeds and the landing page in `site/`. Events are
two hours long; a match with no announced time is an all-day event until TVF publishes the time. Each match keeps
the same event ID across refreshes, so time changes update the existing event instead of creating a new one.

## Automation

`.github/workflows/update.yml` runs every 3 hours and on every push to `main`:

1. type-checks the code (`mypy --strict`) and runs the tests: parsers on saved copies of the TVF files (`tests/fixtures`), the generated feeds, and the
   subscribe buttons in a real browser (each must link to an existing feed in Google / Apple / Outlook format),
2. downloads the latest TVF files; if that fails, the last saved data is used and an issue labelled
   `refresh-failed` is opened (so the repo owner gets an email). It closes itself after the next good refresh,
3. commits `data/matches.json` when fixtures changed,
4. builds the feeds and deploys them to GitHub Pages.

**One-time setup:** in the repository's *Settings → Pages*, set *Source* to **GitHub Actions**.

## Adding or correcting a match by hand

Add entries to `data/manual.json` in the same format as `data/matches.json`. An entry with the same `id` as a
fetched match replaces it; a new `id` adds a match (for example a national team fixture that TVF hasn't listed yet).

## Running locally

```sh
pip install -r requirements.txt -r requirements-dev.txt
python -m playwright install chromium
python -m mypy                     # strict type check of scripts/ and tests/
python -m pytest tests
python scripts/fetch_tvf.py        # needs access to tvf.org.tr
python scripts/build.py            # writes site/
python -m http.server -d site 8000
```

Unofficial; not affiliated with TVF, CEV or FIVB.
