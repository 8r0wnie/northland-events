# Architecture

How a daily run turns ~135 active sources into the published calendar, and
every file that pipeline reads or writes. Keep this in sync with
`scraper/main.py` and `.github/workflows/scrape.yml`.

## Pipeline (`python main.py scrape`)

```
sources/sources.yaml (status: active)
  └─ per source: adapters (pinned `platform:` chain "a,b" or the generic chain)
       ├─ keep events in [now − 1 day, now + 200 days]       HORIZON_DAYS
       ├─ trim_frequent: a title listed 20+ times → next 60 days only
       ├─ NOT_PUBLIC: drop closures and staff-only entries ("NWLS Closed")
       ├─ non-empty → store in source_cache.json
       └─ empty     → reuse cached events if ≤ 6 days old     CACHE_MAX_AGE_DAYS
                      (≤ 10 days for `refresh: local`)        LOCAL_CACHE_MAX_AGE_DAYS
  └─ Event.normalize()          models.py   clean text, infer category
  └─ group_events + classify    verify.py   fuzzy dedup, tier rules, decisions.csv
  └─ source history + promote   main.py     potential -> source after 5/7 producing runs
  └─ classify_tabling           tabling.py  PBS North tabling flag (title only)
  └─ recurrence                 recurrence.py  archive + annual view (+ owner decisions) + stats
  └─ write site/data/*.json, scraper/output/last_run.json
```

`--only id,id` runs the same scrape and prints a summary, but returns before
writing anything under `site/data/` (it does still update `source_cache.json`).

Robustness: each adapter call is wrapped (`scrape_source`), so an exception
in one adapter falls through to the next and never ends the run. Fetch
errors such as redirect loops, bad schemes and broken HTTP/2 are absorbed in
`fetch._request`. A host that resets HTTP/2 streams is retried over HTTP/1.1
and remembered.

**Relay (`refresh: relay`, owner decision 2026-10-12):** in CI, requests to those
sources' hosts go through the site's Cloudflare Worker (`/api/relay`), both
`fetch._request` and the Playwright page route (`fetch._install_relay_route`).
See `docs/DEPLOY.md` §10. Cached events carry forward 10 days for them.

`refresh-local` (legacy, unused since 2026-10-12) is `--only` for every `refresh: local` source. Those sites 403
GitHub's datacenter IPs. The owner's machine runs it
(`scripts/refresh_local.ps1`) and pushes `source_cache.json`, and the daily CI
run then carries those events forward. That is ordinary access from a home
connection, not IP rotation (Rule 1). In CI (`CI` env var set) those sources
aren't requested at all, only carried forward, so we don't keep knocking on
sites that refuse datacenter IPs.

## Recurring series (project rule)

A weekly walking club expanded over 200 days would bury one-off events, so
**high-frequency series show only their next 60 days**, rolling forward each
run. Two places enforce it:

- `ics` knows the RRULE: DAILY/WEEKLY series stop at `FREQUENT_SERIES_DAYS`
  (60), MONTHLY/YEARLY run the full window, and each series has at most 60
  occurrences.
- `main.trim_frequent` covers every other adapter, including platforms that
  pre-expand recurrence (DPCalendar, Revize, My Calendar, WebCalendar). If one
  source lists a title 20+ times (`FREQUENT_MIN_COUNT`), only occurrences in
  the next 60 days are kept.

Change both together. The recurrence tracker's weekly-meetup guard is
separate (see below).

## Data files

| file | written by | read by | committed by CI | notes |
|------|-----------|---------|-----------------|-------|
| `sources/sources.yaml` | hand, `srcedit.py`, `triage --write` (both line-level, keep comments) | scraper | no (hand commits) | source of truth for sources |
| `site/data/events.json` | `scrape` | `index.html`, `tabling.html` | yes | rolling window, overwritten each run |
| `site/data/review_queue.json` | `scrape` | `review.html` | yes | pending low-trust items |
| `moderation/decisions.csv` | admin page (Worker API), owner only | `verify.py` | no (Worker commits) | `key,decision,title,date,source,note,decided_at` |
| `moderation/annual_decisions.csv` | admin page (Worker API), owner only | `recurrence.py` | no (Worker commits) | `key,decision(confirm/reject),title,decided_at` |
| `site/data/sources.json` | `scrape` | `admin.html` | yes | every registry entry + tier + last 7 runs |
| `scraper/output/source_history.json` | `scrape` | `scrape` (auto-promotion) | yes | per source, last 14 runs: events, queued, carried |
| `site/data/decisions.csv` | `scrape` (copy of the above) | `review.html` | yes | read-only published copy |
| `site/data/event_history.json` | `recurrence.update_history` | `recurrence` | yes | **never trimmed**, the only long-term memory |
| `site/data/recurring_events.json` | `recurrence.build_recurring_view` | `recurring.html` | yes | `confirmed` / `likely` series, regenerated |
| `site/data/scrape_stats_history.json` | `recurrence.append_stats_snapshot` | `recurring.html` | yes | one row per day, capped at 730 |
| `scraper/output/source_cache.json` | `scrape`, `refresh-local` (owner's machine) | `scrape` | yes | last-good events per source; how `refresh: local` sources reach CI |
| `scraper/output/last_run.json` | `scrape` | humans / health check | yes | counts + per-source detail |

If you add a file CI should keep, add it to the `git add` line in
`.github/workflows/scrape.yml` — otherwise it's lost after every run.

## Event schema (`events.json` → `events[]`)

From `models.Event.to_dict()` plus fields added downstream:

| field | meaning |
|-------|---------|
| `source_id` | primary source id |
| `title`, `description` | cleaned text |
| `start`, `end` | ISO local (America/Chicago) datetimes, no offset |
| `all_day` | bool |
| `venue`, `address`, `city`, `state` (MN/WI/MI), `area` | `area` is the filter label, inherited from the source unless the adapter overrides |
| `url`, `image`, `price` | `price` is free-form |
| `category` | one of `community`, `sports-rec`, `arts`, `family`, `food-drink`, `music`, `holiday`, `education`, `other` |
| `scraped_at` | UTC ISO timestamp |
| `fingerprint` | sha1 of normalized title + start date + city, 16 hex chars |
| `verification` | `multi-source`, `trusted-single`, or `reviewed` |
| `sources`, `source_count` | `[{name,url}]` of every source in the merged group |
| `tabling`, `tabling_reasons` | PBS North flag — only `tabling.html` uses it |
| `inferred` | `true` when the `posts` adapter read the event out of prose; drives review (below) |

Top level: `generated`, `count`, `areas`, `categories`, `events`.

## Categorization (`models.py`)

Adapters set a category when the platform gives one (Tribe's are mapped and
left alone). Events left `other`, or at the coarse gov default `community`,
go through `infer_category(title, desc, fallback)`: keyword table
`_CATEGORY_RULES`, title checked before description. Add keywords there, not
in adapters.

## Verification & review queue (`verify.py`)

- **Same event:** rapidfuzz `token_set_ratio` ≥ 78 on normalized titles
  (`TITLE_RATIO`), start dates within ±1 day, and venues not clearly different
  (venue ratio ≥ 45 when both are present).
- **Tier** (`registry.Source.effective_tier`, set from the admin page):
  - **trusted**: its own structured events publish on their own.
  - **source**: needs a second, independent website (or your approval).
  - **potential**: every event goes to review, and it never counts as a
    witness.
  Without an explicit `tier:`, the legacy rule applies: `trust:` if set, else
  `source` for `category: social`/`aggregator`, and `trusted` for the rest.
- **Auto-promotion** (`main.promote_sources`): a potential source with
  `autopromote: yes` (every source added from the admin page) becomes a
  *source* once it has produced events on 5 of its last 7 runs (runs where
  events were only carried forward don't count). A manual tier change on the
  admin page clears `autopromote`, so your choice is never overridden.
- **Inferred events:** the `posts` adapter sets `inferred: true` on events it
  read out of prose. A source vouches only for what it published as
  structured data, so `MergedEvent.inferred_sources` tracks sources whose
  only contribution to a cluster was inferred.
- **Confirmed (published):** a trusted source whose contribution was not
  inferred, or 2+ distinct *websites* among trusted/source tiers. Two registry entries on one domain,
  such as a fair page and a festival page on hurleywi.com, count as one
  witness (`verify._site`).
- **Held:** everything else, with a reason shown on the admin page: "potential
  source", "read from page text", or "single source, not yet corroborated". Written to `review_queue.json` and a blank row added to
  `moderation/decisions.csv`, keyed by fingerprint.
- `decision=approve` → published with `verification: "reviewed"`;
  `reject` → dropped permanently; blank → stays queued.

Decisions are made on `/admin` (see `docs/DEPLOY.md` §9 and
`moderation/README.md`). The queue file carries every contributing source and
the reason.

## Recurrence & trends (`recurrence.py`)

`events.json` forgets everything outside its window. Each run, every published
event is filed into `event_history.json` under a **series key** (normalized
title with years stripped, plus area). Occurrences whose dates fall within 21
days of each other count as one edition.

- **Confirmed annual:** seen in 2+ editions spaced 300–400 days apart. Weekly
  or monthly meetups can't qualify regardless of title.
- **Likely annual:** a single edition whose title says so ("45th Annual…").
- `recurring_events.json` is the UI-ready view, with a predicted next date.
- `scrape_stats_history.json` holds one daily snapshot (raw, merged,
  published, queued, active sources, category mix) for the trend charts.

The long comment block in `recurrence.py` is the authoritative description of
the clustering logic — update it with any change.

## Tabling flag (`tabling.py`)

Scope set by PBS North: fairs/festivals/community celebrations,
expos/health fairs/resource days, parades/large public gatherings. Farmers
markets are excluded on purpose. Matching uses the **title only**, because
descriptions mention venue names like "Bayfront Festival Park". It only tags
events. It never filters or changes the public calendar.

## Site (`site/`, static, no build)

| page | audience | data | gate |
|------|----------|------|------|
| `index.html` | public | `events.json` | none — FullCalendar 6.1.15 (jsDelivr), month/week/list, area + category filters, search |
| `admin.html` | owner | `review_queue.json`, `recurring_events.json`, `sources.json` + `/api/*` | server-side passphrase (Worker secret) |
| `review.html` | — | redirect to `admin` | — |
| `tabling.html` | PBS North | `events.json` (`tabling`) | passphrase, `noindex` |
| `recurring.html` | owner | `recurring_events.json`, `scrape_stats_history.json` | passphrase, `noindex` |

`tabling.html` and `recurring.html` gate on a client-side SHA-256 (`PASS_HASH`
in each file). `admin.html` is different: the Worker checks the passphrase
server-side on every API call. They keep
casual viewers out, not determined ones: the JSON files are public. Current
passphrases and how to change them: `docs/DEPLOY.md`.

## Time handling (project rule)

Every `Event.start`/`end` is a **naive America/Chicago wall time**.

- `models.parse_dt` converts any offset or `Z` to Central before dropping
  tzinfo. Until 2026-10-11 it only dropped it, which put every UTC-stamped
  event 5-6 hours late (ChamberMaster microdata, JSON-LD).
- `models.from_epoch` converts Unix time. A bare `datetime.fromtimestamp()`
  uses the machine zone: Central on the owner's PC, UTC in CI.
- `ics._dt` converts aware iCal values the same way.
- A site that publishes wrong offsets gets a per-source `timefix:` (only
  `utc-wall` so far), never a global hack.
- `tests/test_posts.py::test_time_zones` pins this behavior. Run the tests
  with `TZ=UTC` to imitate CI.

## Fetching (`fetch.py`)

- 2s minimum between requests to the same host (`CRAWL_DELAY`).
- Retries `403/429/500/502/503/504` and connection errors twice, with backoff,
  jitter and a different User-Agent on retry. Other statuses fail fast.
- One shared headless Chromium per run (`fetch.browser()` /
  `fetch.playwright_session()`). Playwright's sync API allows a single
  instance per process. Adapters that use it are pin-only.
- A host that tears down HTTP/2 streams is retried over HTTP/1.1
  (`_h1_hosts`). Redirect loops fail fast instead of raising.
