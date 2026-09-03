# Northland Events

A public community events calendar for the **Duluth DMA** — Northern Minnesota,
Northwest Wisconsin, and Ironwood, MI.

- **Scraper** (`scraper/`) — Python. Pulls events from ~90 tourism, chamber,
  government, and news sites listed in `sources/sources.yaml`. Runs daily via
  GitHub Actions.
- **Site** (`site/`) — static month/week calendar (FullCalendar) that reads
  `site/data/events.json`. Deployed to Cloudflare Pages; embedded in a Google
  Site for a stable public URL.

## How it fits together

```
GitHub Actions (daily cron)
  └─ python scraper/main.py scrape
       ├─ reads sources/sources.yaml
       ├─ per source: tries adapters (Tribe API → JSON-LD → iCal → bespoke)
       ├─ dedups across sources by (title, date, city)
       └─ writes site/data/events.json  ── committed back to the repo
                                            │
Cloudflare Pages ─ auto-deploys site/ on push ─ https://northland-events.pages.dev
                                            │
Google Site ─ <iframe> embed of the Pages URL ─ stable public address
```

## Local development

```bash
python -m venv .venv
.venv/Scripts/activate                 # Windows;  source .venv/bin/activate elsewhere
pip install -r scraper/requirements.txt
python -m playwright install chromium

cd scraper
python main.py list                    # show the source registry
python main.py triage --only visit-duluth,ely-chamber   # probe sources, see what yields events
python main.py triage --write          # flip working sources to status: active
python main.py scrape                  # run all active sources -> ../site/data/events.json
python main.py scrape --only visit-duluth
```

Preview the site:

```bash
cd site && python -m http.server 8000   # open http://localhost:8000
```

## Source registry (`sources/sources.yaml`)

One entry per site. Key fields:

| field        | meaning |
|--------------|---------|
| `status`     | `todo` (needs triage) · `active` (scraped daily) · `no-calendar` · `blocked` · `broken` |
| `events_url` | the calendar page, once found (blank = scraper starts at the site root) |
| `platform`   | detected CMS (`wordpress-tribe`, `chambermaster`, `civicplus`, …) — pins the adapter |

## Adapters (`scraper/adapters/`)

Tried in order; first one to return events wins.

| adapter            | covers |
|--------------------|--------|
| `tribe`            | WordPress "The Events Calendar" REST API (`/wp-json/tribe/events/v1/events`) |
| `weblink`          | WebLink Connect ("Atlas") chambers — public `api-internal.weblinkconnect.com/api/Events` with an `x-tenant` header |
| `civicplus`        | CivicPlus / CivicEngage municipal sites — combined calendar RSS feed |
| `revize`           | Revize municipal CMS — FullCalendar JSON handler (`calendar_data_handler.php`), expands recurring rules |
| `jsonld`           | any page embedding schema.org `Event` JSON-LD (headless render fallback, then samples detail pages) |
| `ics`              | a discoverable iCal / `.ics` feed |
| `chamberorganizer` | ChamberOrganizer chambers — `auth.chamberwidgets.com/cn-api/org/calendar/events` month POSTs |
| `chambermaster`    | ChamberMaster / GrowthZone hosted MIC — walks month calendars, parses detail-page microdata |

Planned: `simpleview` (CVBs), `govoffice` (some municipal sites),
`perfectduluthday` (bespoke — Cloudflare-fronted).

## Scope boundaries

Public data only. No credentialed logins (incl. Facebook/Instagram), no IP or
fingerprint rotation to defeat blocks. Per-domain crawl delay is 2s. Events the
scraper can't reach are added through a Google Form → Sheet (planned).
