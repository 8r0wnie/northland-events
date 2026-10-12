# Sources

`sources/sources.yaml` is the single source of truth. This file explains its
fields, how to triage, and the cross-cutting findings that don't belong to any
one entry. Per-source findings go in that entry's `notes:`.

Snapshot 2026-10-12, after the coverage push: 174 sources. 151 active,
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
| `tier` | `trusted` / `source` / `potential`, set from the admin page (blank = derived from `trust`/category) |
| `autopromote` | `yes` = this potential source may auto-promote to `source` (admin-added sources; cleared by any manual tier change) |
| `trust` | legacy `high`/`low` override, used only when `tier` is blank |
| `refresh` | `relay` = the site refuses GitHub's datacenter IPs; CI fetches it through the site's Cloudflare Worker (`docs/DEPLOY.md` §10). `local` = legacy owner-PC refresh, unused |
| `relay_hosts` | extra hosts to relay for a `refresh: relay` source, comma list (e.g. its ticketing API) |
| `timefix` | per-site time correction an adapter applies. `utc-wall` = the site publishes local time as if it were UTC (jsonld) |
| `notes` | triage findings, dated (e.g. "Triaged 2026-09: …") |

## Statuses

| status | meaning |
|--------|---------|
| `todo` | not triaged, or needs a bespoke adapter |
| `active` | scraped every run |
| `paused` | kept in the list but not scraped (set from the admin page) |
| `no-calendar` | no machine-readable calendar (Weebly/Wix/Joomla, agendas only, tiny townships). Say why in `notes` |
| `blocked` | bot challenge (e.g. SiteGround `sgcaptcha`) or 403. **Do not work around it** |
| `broken` | site itself is failing (5xx, DNS, SSL). Recheck later |
| `duplicate` | same organization as another entry; kept for history, never scraped |
| `closed` | the venue/organization has closed; kept for history, never scraped |
| `dead` | the domain lapsed, is parked, or was hijacked (spam). Find the organization's new site; never scrape the old one |

## Adding sources

Usually from the admin page (**Sources → Add a potential source**). The site
is scraped from the next run with the full generic chain plus the `posts`
text fallback. Its events go to review, and it auto-promotes to `source`
after producing events on 5 of 7 runs. To give it a dedicated adapter, triage
it as below, and set `platform:`.

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

**Blocked from GitHub Actions → `refresh: relay`** (owner decision
2026-10-12). These sites answer normally but return HTTP 403 to GitHub's
datacenter IPs: spooner-chamber, virginia-mn, hill-city-mn, norshor-playhouse
(plus `api.ovationtix.com`), duluth-art-institute, wolf-ridge,
fond-du-lac-band. CI fetches them through the site's Cloudflare Worker
(`docs/DEPLOY.md` §10). A new source that's blank in CI but fine locally
goes on this list only with the owner's OK. Check the CI log for `HTTP 403`
first.

**Facebook / Instagram:** not scraped directly and won't be. There has been no
public events API since 2018. Event pages are login-walled, datacenter IPs get
blocked, and it breaks the ToS. Public Facebook events come in via the
`allevents` aggregator and go through review. Since 2026-10-12 it covers 19
DMA towns: the original 9 plus Ely, Grand Marais, Hayward, Ironwood, Spooner,
Silver Bay, Moose Lake, Aitkin, Hinckley and Washburn. Slugs vary
(`ely-mn`, `ironwood`, `hayward-wi`), so check that the final URL wasn't
redirected to /events. Bayfield and Eveleth have no allevents page. Eventbrite is partly covered by
allevents. Ticketmaster Discovery has a free API, but it needs an account key,
so it needs owner approval first.

More legitimate routes to Facebook-only events, as of 2026-10-12:
- **Newspaper evvnt calendars** (`evvnt` adapter). Organizers submit the
  events they also post on Facebook. Mesabi Tribune (~500) and APG Wisconsin
  are live.
- **Newsletter archives.** `posts` reads Mailchimp archive feeds linked from a
  site. A scan of all sources found only FeLiveLife's, which is already
  structured, so this mostly matters for sources added later.
- **Regional radio roundups**, such as KAXE "Events Up North".
- **Meta's Graph API, organizer opt-in.** A Page owner can authorize an app
  to read that Page's own events. This is fully within Meta's terms, but it
  needs a Meta developer account and app review (`pages_read_engagement`,
  `pages_read_user_content`). That's an owner decision and not built yet.
  It's planned in `CLAUDE.md`.
