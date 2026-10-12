# Sources

`sources/sources.yaml` is the single source of truth. This file explains its
fields, how to triage, and the cross-cutting findings that don't belong to any
one entry. Per-source findings go in that entry's `notes:`.

Snapshot 2026-10-11: 153 sources. 85 active, 44 todo, 19 no-calendar,
4 blocked, 1 broken. By category: 62 gov, 53 venue, 21 chamber, 9 social,
7 tourism, 1 news.

## Fields

| field | meaning |
|-------|---------|
| `id` | stable slug, used in `--only`, the cache and the event `source_id` |
| `name`, `url` | display name and site root |
| `events_url` | calendar page (blank = start at `url`). `spektrix:<client>` for Spektrix |
| `category` | `gov`, `chamber`, `tourism`, `venue`, `news`, `social`, `aggregator`. Drives the trust tier |
| `state`, `area` | `MN`/`WI`/`MI`, and the public filter label |
| `platform` | pins an adapter (see `docs/ADAPTERS.md`). Blank = generic chain |
| `status` | see below |
| `trust` | optional `high`/`low` override of the category-derived tier |
| `notes` | triage findings, dated (e.g. "Triaged 2026-09: …") |

## Statuses

| status | meaning |
|--------|---------|
| `todo` | not triaged, or needs a bespoke adapter |
| `active` | scraped every run |
| `no-calendar` | no machine-readable calendar (Weebly/Wix/Joomla, agendas only, tiny townships). Say why in `notes` |
| `blocked` | bot challenge (e.g. SiteGround `sgcaptcha`) or 403. **Do not work around it** |
| `broken` | site itself is failing (5xx, DNS, SSL). Recheck later |

## Triage workflow

1. `python main.py triage --only <id>` probes the generic chain and prints what each adapter found.
2. If nothing hits, inspect the page for the endpoint it calls itself (XHR, RSS,
   iCal, JSON-LD, microdata) before reaching for HTML parsing or a browser.
3. Set `platform:`, `events_url:`, `status:`, and a dated `notes:` by hand.
   Avoid `triage --write`, which drops comments and `trust:`.
4. `python main.py scrape --only <id>` to confirm counts and dates look right.

## Cross-cutting findings

**Government (62):** all triaged in 2026-09. 18 dead URLs from the original
appendix were corrected in `sources.yaml` (e.g. hibbingmn.gov, koochiching.gov,
ironwoodmi.gov, cityofmooselake.gov). Many small gov calendars legitimately
return 0 in some months.

**Revisit candidates (no-calendar / todo):**
- stlouis-county-mn: custom CMS, board articles only.
- lake-county-mn: lsvr_event post type, date not in REST.
- international-falls-mn: Google Calendar iframe; extract the calendar id for `ics`.
- washburn-wi, wakefield-mi, cloquet-mn, hayward-wi: 403 to a bot UA. A
  rendered fetch may work, but only if that isn't defeating a deliberate block.
- grand-portage: Joomla.
- Tourism/chamber todos: explore-minnesota, iron-range, north-shore-visitor,
  lake-superior-circle, several chambers. felivelife.org feeds
  ironwood-chamber and could be its own source.

**Venues without a machine-readable calendar:** Squarespace booking pages
(fitgers-brewhouse, gunflint-tavern, blacklist-brewing), the
google-calendar-events plugin rendering nothing (bent-paddle, duluth-cider),
WooCommerce/Elementor without an events plugin (hoops-brewing, clyde-iron,
ursa-minor), Tribe without the REST API exposed (zeitgeist, voyageur), and
Ludus (ironwood-theatre). Their big shows usually arrive via Perfect Duluth Day,
the DECC or allevents.

**Flaky from GitHub Actions** (fine locally, datacenter-IP rate limiting):
spooner-chamber (consistently), proctor-mn, cityofgrandrapidsmn,
duluth-art-institute, and the headless-browser sources (norshor-playhouse).
The 6-day carry-forward cache covers short gaps only.

**Facebook / Instagram:** not scraped directly and won't be. There has been no
public events API since 2018. Event pages are login-walled, datacenter IPs get
blocked, and it breaks the ToS. Public Facebook events come in via the
`allevents` aggregator and go through review. Eventbrite is partly covered by
allevents. Ticketmaster Discovery has a free API, but it needs an account key,
so it needs owner approval first.
