# Sources

`sources/sources.yaml` is the single source of truth. This file explains its
fields, how to triage, and the cross-cutting findings that don't belong to any
one entry. Per-source findings go in that entry's `notes:`.

Snapshot 2026-10-12, after the coverage push: 161 sources. 138 active,
0 todo, 5 no-calendar, 7 blocked, 2 broken, 4 dead, 4 duplicate, 1 closed.
Every active source has a pinned `platform:`. Re-count with
`python -c "from registry import load_sources; import collections; print(collections.Counter(s.status for s in load_sources()))"`
from `scraper/`.

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
| `refresh` | `local` = the site 403s GitHub Actions' datacenter IPs; scraped from the owner's machine instead (below) |
| `timefix` | per-site time correction an adapter applies. `utc-wall` = the site publishes local time as if it were UTC (jsonld) |
| `notes` | triage findings, dated (e.g. "Triaged 2026-09: …") |

## Statuses

| status | meaning |
|--------|---------|
| `todo` | not triaged, or needs a bespoke adapter |
| `active` | scraped every run |
| `no-calendar` | no machine-readable calendar (Weebly/Wix/Joomla, agendas only, tiny townships). Say why in `notes` |
| `blocked` | bot challenge (e.g. SiteGround `sgcaptcha`) or 403. **Do not work around it** |
| `broken` | site itself is failing (5xx, DNS, SSL). Recheck later |
| `duplicate` | same organization as another entry; kept for history, never scraped |
| `closed` | the venue/organization has closed; kept for history, never scraped |
| `dead` | the domain lapsed, is parked, or was hijacked (spam). Find the organization's new site; never scrape the old one |

## Triage workflow

1. `python main.py triage --only <id>` probes the generic chain and prints what each adapter found.
2. If nothing hits, inspect the page for the endpoint it calls itself (XHR, RSS,
   iCal, JSON-LD, microdata) before reaching for HTML parsing or a browser.
3. Set `platform:`, `events_url:`, `status:`, and a dated `notes:` with the
   line-level editor, which keeps comments and every field:
   `python srcedit.py <id> platform=ics events_url=https://... "notes=Triaged 2026-10: ..."`
   (`triage --write` uses the same editor.)
4. `python main.py scrape --only <id>` to confirm counts and dates look right.
5. If the calendar is often empty but the site posts news, chain the text
   fallback: `platform=<calendar>,posts`.

What usually works, in order of preference:
1. A feed the page itself loads: iCal, Google Calendar embed, JSON API, RSS.
2. Structured markup: JSON-LD or microdata.
3. A headless render plus capture of the page's own request.
4. `posts`, which infers events from text. Those events go to review.

## Cross-cutting findings

**Government (62):** all triaged in 2026-09. 18 dead URLs from the original
appendix were corrected in `sources.yaml` (e.g. hibbingmn.gov, koochiching.gov,
ironwoodmi.gov, cityofmooselake.gov). Many small gov calendars legitimately
return 0 in some months.

**Deliberately parked:**
- `blocked`: lake-superior-zoo, great-lakes-aquarium and gogebic-county-fair
  (SiteGround `sgcaptcha`), itasca-county-fair, cloquet-mn ("Access Denied"
  even in a browser), wakefield-mi ("Robot Challenge Screen"), and
  duluth-public-library (interactive Cloudflare challenge on its events site). The owner
  wants these addressed. Use only legitimate routes: a feed hosted
  elsewhere, the venue's listings on another source, asking the site, or
  the planned manual-add form.
- `broken`: burnett-county-fair (HTTP 500), and dsso (unreachable; its
  concerts arrive via the DECC).
- `dead`: lake-vermilion-chamber and stlouis-county-fair (domains hijacked by
  spam sites), moose-lake-chamber (parked) and sandstone-chamber (no DNS).
  Searched 2026-10-12 with no replacement found. Lake Vermilion-area events
  now come from tower-soudan-history. wrenshall-mn moved to
  cityofwrenshall.com and is active again.
- `no-calendar`: iron-range (prose only), lake-superior-circle (touring blog),
  duluth-news-tribune (no calendar), knife-river-mn (URL is a gallery), and
  mcgregor-mn (cityofmcgregor.com is McGregor, **Texas**).

**Wrong-organization traps found in the seed list.** Check the page title
and location before trusting a URL:
- arrowheadarts.org is Lake Arrowhead, CA. The source now uses
  grandmaraisplayhouse.com.
- cityofmcgregor.com is a Texas city.
- kniferivermn.com is a private gallery.
- washburncounty.org is the county's tourism bureau, not the government.
- grandmaraismn.com is parked. Visit Grand Marais is now visitcookcounty.com.

**Venues that looked calendar-less but weren't (2026-10-11):** Bent Paddle
and Duluth Cider use the Simple Calendar plugin, which renders fine
(`simplecalendar`). Fitger's events live on the Barrel Room's Squarespace
events collection. Gunflint uses a Google Calendar embed, and Earth Rider uses
an Eventbrite organizer. Still text-only, through `posts`: clyde-iron,
ursa-minor, blacklist-brewing, voyageur-brewing, zeitgeist and
ironwood-theatre. hoops-brewing closed in Feb 2026.

**Blocked from GitHub Actions → `refresh: local`.** These sites answer from
home but return HTTP 403 to GitHub's datacenter IPs:
spooner-chamber, virginia-mn, hill-city-mn, norshor-playhouse,
duluth-art-institute, wolf-ridge, fond-du-lac-band. They're marked
`refresh: local`. `scripts/refresh_local.ps1`, run on the owner's machine,
scrapes them into `source_cache.json` and pushes, and CI carries those events
forward for up to 10 days. A source that's blank in CI and fine locally
belongs on this list. Check the CI log for `HTTP 403` first.

**Facebook / Instagram:** not scraped directly and won't be. There has been no
public events API since 2018. Event pages are login-walled, datacenter IPs get
blocked, and it breaks the ToS. Public Facebook events come in via the
`allevents` aggregator and go through review. Eventbrite is partly covered by
allevents. Ticketmaster Discovery has a free API, but it needs an account key,
so it needs owner approval first.
