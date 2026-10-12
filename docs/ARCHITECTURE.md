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

**A series is never one long block.** Some platforms give a series' whole
span (first class to last class) as start and end, and the calendar drew a
weekly class as a week-long bar (Chair Yoga, Bayfield, 2026-10-12). So:

- Adapters expand the recurrence into dated occurrences. `simpleview` parses
  its recurrence text (daily, weekly or every N weeks on given days, monthly
  on the Nth weekday, monthly on a day) and otherwise uses only the dates the
  site lists.
- `Event.normalize` is the backstop for every adapter. A timed event that
  spans more than 6 days (`SERIES_SPAN`) and whose title or description says
  it repeats (`_SERIES_TEXT`: recurring, weekly, every Monday…) keeps only its
  first occurrence. Genuine multi-day runs (Bentleyville, exhibits, festival
  weeks) don't carry repeat wording and keep their span.

Cases are in `tests/test_series.py`.

## Data files

| file | written by | read by | committed by CI | notes |
|------|-----------|---------|-----------------|-------|
| `sources/sources.yaml` | hand, `srcedit.py`, `triage --write` (both line-level, keep comments) | scraper | no (hand commits) | source of truth for sources |
| `site/data/events.json` | `scrape` | `index.html`, `tabling.html` | yes | rolling window, overwritten each run |
| `site/data/review_queue.json` | `scrape` | `review.html` | yes | pending low-trust items |
| `moderation/decisions.csv` | admin page (Worker API), owner only | `verify.py` | no (Worker commits) | `key,decision,title,date,source,note,decided_at` |
| `moderation/annual_decisions.csv` | admin page (Worker API), owner only | `recurrence.py` | no (Worker commits) | `key,decision(confirm/reject),title,decided_at` |
| `site/data/sources.json` | `scrape` | `admin.html` | yes | every registry entry + tier + last 7 runs |
| `moderation/corrections.csv` | admin page (Worker API), owner only | `feedback.py` | no (Worker commits) | resolved community reports, one row per field (see below) |
| `moderation/category_rules.csv` | admin page (Worker API), owner only | `feedback.py` | no (Worker commits) | `key,decision(off),decided_at`: learned rules the owner switched off |
| `site/data/category_learning.json` | `scrape` (`feedback.build_learning`) | `admin.html` | yes | learned rules, near-misses, most common mistakes; regenerated |
| KV `REPORTS` (Cloudflare) | `POST /api/report` | `admin.html` via `/api/reports` | never in git | open reports `open:<id>`; resolved `done:<id>` expire after 180 days |
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
| `corrected` | `true` when an owner-approved community correction changed the event |

Top level: `generated`, `count`, `areas`, `categories`, `events`.

## Community reports and category learning (`feedback.py`)

1. A visitor uses **Report a problem** on an event. `POST /api/report`
   checks the event's `fingerprint` is published and stores the report, with
   a snapshot of the event, in KV (`docs/DEPLOY.md` §11).
2. On `/admin` → Reports, reports are grouped by event and problem. The
   owner chooses **Apply fix** (a prefilled, editable fix), **Can't verify**,
   or **Reject** (the calendar was right). The Worker commits one row per
   field to `moderation/corrections.csv`, with `key` = `<report id>:<field>`.
   - Fields: `category`, `when`, `venue`, `city`, `state`, `title`, `url`,
     `hide`.
   - `when` is `2026-10-14T10:00/2026-10-14T11:00`. A bare date means all
     day, and `date/date` is a multi-day all-day event.
3. Every scrape: `load_corrections` collects the `apply` rows by fingerprint,
   and `apply_corrections` changes the published dict. `hide` drops the
   event, and `corrected: true` marks it. The fingerprint is kept, so the fix
   sticks until the source changes the event's title, day or city. Undo on
   `/admin` deletes the row.
4. **Learning.** `build_learning` treats each applied category fix as a
   mistake (`was` → `value`), counted once per event, and each rejected
   category report as a confirmation.
   - Features are the title's words (minus stopwords) and `source:<id>`.
   - A rule `<feature>: <from> → <to>` switches on when at least
     `LEARN_MIN_SUPPORT` (3) events agree and at least 75% of the evidence
     for that feature and category points the same way.
   - `apply_learned` runs on every merged event before the owner's own
     fixes. It only changes events currently in `from`, and the strongest
     rule wins.
   - The owner can switch a rule off (`category_rules.csv`).
   - `site/data/category_learning.json` feeds the admin panel: rules (with
     how many events each changed), near-misses, the most common mistakes,
     and mistakes by source.

Learning only uses owner verdicts, never raw reports, so a flood of bad
reports can't retrain the categorizer. Cases are in `tests/test_feedback.py`.

## Description cleanup (`models.clean_description`)

Every adapter's description goes through it, from `Event.normalize`, so the
fix lives in one place:
- URL-encoded text (`The%20RBC%20…`) is decoded. That needs 3+ `%XX`
  sequences and almost no spaces, so "50% off" stays as it is.
- HTML becomes text with `<style>`/`<script>` *contents* dropped. Reif
  Center's page builder embeds CSS in the description, and stripping tags
  alone used to keep it.
- Leftover CSS rule blobs and entities are removed.
- Page boilerplate *labels* are removed: SPONSORED BY, KNOW BEFORE YOU GO,
  TICKETS, Read More, Add to Calendar, "See the flyer below", and similar.
  Lowercase forms only count with an arrow or at the very end, so sentences
  like "buy tickets at the door" or "sponsored by the League of Women Voters"
  stay.
- A clear copy of the title at the start is dropped ("Title - …", "Title:
  …", "Title Title is …").
- Text ends at a sentence break, or a word break plus "…", within `DESC_MAX`
  (1200). The queue's short form uses `truncate_desc(…, 600)`.

Cases are in `tests/test_descriptions.py`. Add one whenever a new kind of
junk shows up. On the 2026-10-12 data, CSS dropped from 19 events to 0,
leftover HTML from 57 to 0, mid-word endings from 569 to 0, and boilerplate
from 71 to 16. The remaining 16 are real "sponsored by …" sentences.

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

**Look (owner decision 2026-10-12).** The site is meant to live on the PBS
North website, so every page follows the PBS style guide's colours and type,
with no PBS logo or name. `site/theme.css` holds all of it:

- The palette tokens: PBS Blue `#2638C4`, Navy `#0A145A` (header band),
  Medium Blue `#0F1E8C`, and the sparing accents teal, yellow (focus ring)
  and coral.
- The light and dark values, and FullCalendar's button and today colours.
- The font stack. PBS Sans is licensed, so it's listed first (used if
  installed) and Inter from Google Fonts stands in.

Pages link `theme.css` and keep only layout CSS, using `var(--…)` tokens and
never hard-coded colours. The exception is the calendar's category colours
(`CAT_COLORS` in `index.html`), a colour-blind-safe set kept for legibility.

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
