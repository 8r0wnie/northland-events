# Northland Events — project context

Public community events calendar for the **Duluth DMA**: Northern Minnesota,
Northwest Wisconsin, and Ironwood MI. Neutral branding — **not** tied to any
business.

This is a standalone project. It shares nothing with any other project. Run
Claude Code from this directory (`o:\Projects\Regional Event Scraper`) so it gets
its own session, memory, and history.

## Architecture (all free tier)

- **Scraper** (`scraper/`, Python 3.12+) — pulls events from the sites in
  `sources/sources.yaml`, dedups, writes `site/data/events.json`.
- **GitHub Actions** (`.github/workflows/scrape.yml`) — runs the scraper daily
  (~05:20 America/Chicago) and commits the refreshed data. Also `workflow_dispatch`.
- **Site** (`site/`) — static FullCalendar page (month/week/list + area/category
  filters) reading `events.json`. Deploys to **Cloudflare Pages** (`site` as the
  output dir, no build step).
- **Google Site** — embeds the Pages URL via iframe for a stable public address.
- Repo: https://github.com/8r0wnie/northland-events (public)

## Working on the scraper

```bash
python -m venv .venv && .venv/Scripts/activate
pip install -r scraper/requirements.txt
python -m playwright install chromium
cd scraper
python main.py list
python main.py triage --only <id,id>     # probe sources, see what yields events
python main.py scrape --only <id,id>     # write ../site/data/events.json
python main.py scrape                    # all status: active sources
cd ../site && python -m http.server 8000 # preview
```

Windows note: the scraper reconfigures stdout to UTF-8; run child processes with
`PYTHONIOENCODING=utf-8` when calling pieces directly.

## Adapters (`scraper/adapters/`, tried in order, first with events wins)

| adapter            | platform |
|--------------------|----------|
| `tribe`            | WordPress "The Events Calendar" REST API |
| `weblink`          | WebLink Connect / "Atlas" chambers (public `api-internal.weblinkconnect.com/api/Events`, `x-tenant` header) |
| `jsonld`           | schema.org Event JSON-LD (headless-render fallback, samples detail pages) |
| `ics`              | discoverable iCal feed |
| `chamberorganizer` | ChamberOrganizer (`auth.chamberwidgets.com` month POSTs) |
| `chambermaster`    | ChamberMaster / GrowthZone hosted MIC (month calendars → detail-page microdata) |

Pin an adapter per source with `platform:` in `sources.yaml`; otherwise the
generic chain runs.

**In progress:** government-site adapters. Recon findings — of 62 gov sources:
CivicPlus ≈8 (RSS feed at `/RSSFeed.aspx?ModID=58&CID=All-calendar.xml`),
Revize ≈8 (`calendar.php`), WordPress+Tribe ≈7 (use `tribe`), Weebly 1,
~15 dead/bad URLs, ~16 unknown. Plan: `civicplus` + `revize` adapters, then
activate `tribe` for the WordPress gov sites.

**Also planned:** `simpleview` (Bayfield, Visit Ashland), Perfect Duluth Day
(bespoke, Cloudflare-fronted), Explore Minnesota / Iron Range, a Google Form →
Sheet manual-add path for Facebook-only events.

**Known issues:** categorization is weak (most events land in `other` — needs
title/keyword inference). `spooner-chamber` scraped 77 locally but 0 in CI once —
possibly datacenter-IP rate limiting; watch it.

## Scope boundaries

Public data only. No credentialed logins (incl. Facebook/Instagram), no IP or
fingerprint rotation to defeat blocks. 2s per-domain crawl delay.
