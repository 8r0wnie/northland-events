# Adapters

Every adapter lives in `scraper/adapters/`, implements `scrape(source) ->
AdapterResult` (`base.py`), and is registered in `adapters/__init__.py`.
This file records **what each one talks to and what bit us**. The module
docstring of each adapter is the detailed reference. Keep both in sync.

## How selection works

- `platform:` set in `sources.yaml` → those adapters only (`BY_PLATFORM`).
  It may **chain** several with commas, tried in order, first with events wins:
  `govoffice,posts` = the calendar first, news-post text if the calendar is
  empty. Use a chain whenever a site's calendar is often empty but it posts news.
- No `platform:` → the `GENERIC` chain, in order, first result with events wins.
  Order is cheap/precise first, expensive/broad last.
- Adapters that drive a headless browser, and `posts`, are **pin-only**. Never
  add them to `GENERIC`, because the chain runs on every unpinned source.
- After any adapter, `main.trim_frequent` applies the frequent-series rule (a
  title one source lists 20+ times shows only its next 60 days). See
  `docs/ARCHITECTURE.md`.

## Generic chain (in order)

### `tribe` — WordPress "The Events Calendar"
`GET /wp-json/tribe/events/v1/events`. Aliases: `wordpress-tribe`. Tribe
categories are mapped and left alone by `infer_category`. The venue's state
(`stateprovince`/`state`/`province`) is read per event, so an aggregator that
spans a state line (FeLiveLife: Ironwood MI + Hurley WI) is tagged right. Some
WP hosts rate-limit GitHub Actions IPs (see `refresh: local` in
`docs/SOURCES.md`). If wp-json is bot-walled, use `tribehtml`. Sites that
closed the REST API often still serve JSON-LD on `/events/` (chain
`tribe,jsonld`).

### `weblink` — WebLink Connect / "Atlas" chambers
`GET api-internal.weblinkconnect.com/api/Events?PageSize=0&OrganizationEvent=true&CommunityEvent=true&MembersOnlyEvent=true&InternalEvent=false&EventClosed=false&SearchDateBegin=<iso>&SearchDateEnd=<iso>`
with header `x-tenant: <subdomain>` (case-insensitive, no auth). The tenant
comes from the `*.weblinkconnect.com` link on the homepage.

### `civicplus` — CivicPlus / CivicEngage gov sites
Combined calendar RSS at `/RSSFeed.aspx?ModID=58&CID=All-calendar.xml`.
Read the namespaced `<calendarEvent:EventDates/EventTimes/Location>` fields.
Category is forced to `community`.

### `revize` — Revize gov CMS
New sites: `/_assets_/plugins/revizeCalendar/calendar_data_handler.php?webspace=<slug>&relative_revize_url=//webgen1.revize.com&protocol=https:`
returns all events with optional iCal `rrule`. `<slug>` comes from `webspace=`
in page HTML. Legacy sites: `/calendar_app/db/calendar_1_activemonthsdata_YYYY-MM.xml`
(dates pre-expanded). **Gotcha:** only DAILY/WEEKLY/MONTHLY/YEARLY rules are
expanded, with start clamped to the window. Unbounded SECONDLY-type rules hung
`dateutil` once. Category forced to `community`.

### `mycalendar` — WordPress "My Calendar" plugin
`GET /wp-json/my-calendar/v1/events?from=&to=`. Recurrence arrives
pre-expanded in `occur_*` fields.

### `webcalendar` — DocAccess .NET calendar (City of Duluth)
`POST <root>/WebCalendar/CreateCalendarAndEvents` with form
`chosenCategories=1&...&selectedMonth=&selectedYear=&view=calendar`, one per
month. Parse `.day .calendaritem` buttons.

### `govoffice` — Vision / GovOffice (`/repository/designs/` sites)
Tries the pinned `events_url` first, then `<root>/events`, `/calendar`,
`/community-calendar`, `/meeting-calendar`, then homepage links labelled
calendar/events (the page often lives at `index.asp?SEC=<guid>`). Month
params `CM`/`CY` are ignored by the server. Three layouts: month grid with
`a.eventLink` + `.eventTip`, grid with `a.eventLink` + `.eventDates`, and list
view `article.summaryDisplay` (`h2 a` title + `<time>`). Many GovOffice cities
have moved their calendar to a **Google Calendar embed** on that page: pin
`ics` with the page as `events_url` (Ely, Grand Marais, Pine City).

### `heygov` — HeyGov
`GET https://api.heygov.com/<domain>/events?month=YYYY-MM&expand=parent&source=calendar-embed`
per month. **Gotcha:** `starts_at_local` is local wall time despite the
trailing `Z`.

### `jsonld` — schema.org Event JSON-LD
Pulls `Event` objects (direct, arrays, `@graph`) from the events page, with a
headless-render fallback, and samples a few linked detail pages. Pinned for
lsvr-events WordPress sites (visit-ashland, lake-county-mn) with
`events_url: /events/`. Does not set a category. Honors `timefix: utc-wall`
for WordPress MEC sites that publish local time as if it were UTC (Hurley:
"10 am" appears as `05:00-05:00`). Read the event page's own displayed time
before setting it.

### `microdata` — schema.org Event microdata
Every element with `itemtype=…Event` on the listing page, plus its nested
`itemprop`s (name, startDate, endDate, url, location, description/about).
Main use is **GrowthZone "Hub" chamber calendars**
(`business.<chamber>.com/calendar` or `/communitycalendar`, cards with
`startDate "10/4/2026 10:00:00 AM"`): East Lake County, Hermantown.
**Gotcha:** selectolax returns a new Node object on each access, so compare
`mem_id`, never `is`.

### `simplecalendar` — WordPress "Simple Calendar" (google-calendar-events)
The plugin renders the Google Calendar into the page as `.simcal-event`
items with `data-event-start` / `data-event-end` Unix seconds (UTC). Skips
"Canceled:" entries. It reads only what the page renders, usually the current
list or month. Bent Paddle, Duluth Cider, Hayward Chamber. (The old triage
note "plugin renders nothing" was wrong; there was just no adapter.)

### `ics` — iCal feed
Discovers `<link rel=alternate type=text/calendar>`, `.ics` / `?ical=` /
`outlook-ical` links, the Tribe `?ical=1` convention, and the WordPress
Modern Events Calendar site feed `/?mec-ical-feed=1` (Ely Chamber). Also
follows `webcal://` and a direct feed `events_url`.

- **Google Calendar embeds:** every `src=` calendar in the iframe is fetched
  and **merged** (one embed often layers city, parks, library calendars). The
  id may be raw (`x%40group.calendar.google.com`) or base64-encoded
  (`Z3JhbmRt...`); `_gcal_id` handles both. Google's public-holiday overlay
  is skipped.
- **Recurrence:** `RRULE`s are expanded inside the window (DAILY/WEEKLY/
  MONTHLY/YEARLY only; UTC `UNTIL` made naive to match DTSTART), `EXDATE`s
  removed, and `RECURRENCE-ID` overrides replace their original slot. Daily
  and weekly series are cut at 60 days (`FREQUENT_SERIES_DAYS`), others run
  the full 200 days; 60 occurrences per series max. Before 2026-10-11 the
  adapter ignored RRULE entirely, so a weekly event that started in the past
  vanished.

### `chamberorganizer` — ChamberOrganizer
`POST auth.chamberwidgets.com/cn-api/org/calendar/events` with form
`searchValues=&org_id=<CODE>&year=&month=&day=`, one per month. The org code
comes from `org_id=` in page HTML.

### `chambermaster` — ChamberMaster / GrowthZone MIC
Walks `<mic>/events/calendar/<YYYY-MM-01>` to list detail URLs, then parses
schema.org microdata on `/events/details/` pages. Host discovery probes
`business.` / `members.` subdomains and homepage links to
`*.chambermaster.com` / `*.growthzoneapp.com`. Alias: `growthzone`. Slowest
generic adapter, which is why it's last. Its microdata `startDate` is UTC
(`…T13:45:00Z` = 7:45 AM). `models.parse_dt` converts that since 2026-10-11.

## Pin-only

### `simpleview` — Simpleview DTN (CVB / tourism) · browser
The REST endpoint `/includes/rest_v2/plugins_events_events_by_date/find/` is
WAF-protected, so the adapter loads `<events_url>` and `?skip=N` pages in
Chromium and captures the JSON the page fetches. The API doesn't expand
recurrence. The adapter emits one-time events and parses "weekly on <Day>".
Used by bayfield-chamber and ely-chamber.

### `tribehtml` — Tribe via RSS, through a browser · browser
For Tribe sites whose wp-json and `.ics` are behind Cloudflare (Perfect
Duluth Day). Clear the challenge on the homepage, then fetch
`<events_url>/feed/?paged=N` from inside the page. `pubDate` is the event start,
converted from UTC to America/Chicago.

### `allevents` — allevents.in aggregator (Facebook events)
`GET allevents.in/<city>/all?page=N`, JSON-LD, plain httpx. Eight DMA city
sources, all `category: social`, so they are low-trust and go through review.
**Limitation:** no time of day, so all events are all-day. Infer-category
fallback is `community`. Background on why Facebook isn't scraped directly is
in the project memory and summed up in `docs/SOURCES.md`.

### `sidearm` — college athletics
Public iCal at `<host>/calendar.ashx/calendar.ics`. SUMMARY tags `[H]`/`[A]`/`[N]`
(+ `[W]/[L]/[T]`). Keep home and neutral games, drop away games, strip tags,
convert to Central. Sources: umd-bulldogs, uws-yellowjackets, css-saints.

### `ovationtix` — OvationTix / AudienceView · browser
Captures per-production `api.ovationtix.com/public/events/client(<id>)/production(<pid>)`
JSON from the venue page (bare requests are rejected). norshor-playhouse uses
client 35679, which covers both NorShor and the Duluth Playhouse. Flaky from CI.

### `decc` — The DECC
WordPress swim-events-calendar plugin. Parse `section.event-list-item`:
`span.event-date` ("Sep04| 8:00 pm"), `span.slide-venue`, `h2.entry-title`.
There is no year, so roll the year forward when the month goes backwards. Also
covers DSSO, MN Ballet and UMD hockey at AMSOIL, so the dsso source stays `todo`.

### `eventscalendar` — Wix "Events Calendar" app (inffuse) · browser
Captures `broker.eventscalendar.co/api/{google,eventbrite}/next` responses from
the widget iframe. Sources: earth-rider, arrowhead-arts.

### `spektrix` — Spektrix ticketing
`system.spektrix.com/<client>/api/v3/events` + `/events/<id>/instances`.
`events_url: spektrix:<client>` names the client directly. big-top uses
`ashwabayalliance`.

### `birkie` — American Birkebeiner (birkie.com/calendar/)
Hand-built WordPress block markup, no plugin and no JSON-LD. One
`h2.calendar-list__month-separator` per month, then one `div.wp-block-columns`
per event (`h3 a` title, date in `<em>` in the first paragraph, description in
the next). Server-rendered.

### `northhouse` — North House Folk School (northhouse.org/events)
Old-style schema.org **Microdata** (`itemtype=http://schema.org/EducationEvent`),
which `jsonld` doesn't read. Server-rendered.

### `eventbrite` — Eventbrite organizer (all upcoming events of one organizer)
`GET www.eventbrite.com/organizer-profile/api/organizers/<id>/events-from-date/?from_date=&page=&page_size=50&order_by=start_asc&include_started=true`
— the JSON the organizer page loads, plain request, no key. Organizer id from
`events_url: eventbrite:<id>`, an `eventbrite.com/o/...` URL, or the first
such link on the venue's page. Venue/city/state come from `primary_venue`.
Other Eventbrite endpoints (`/org/<id>/showmore`, `/api/v3`) 403 or need a
key, so don't use them. Earth Rider chains `eventscalendar,eventbrite`.

### `dpcalendar` — Joomla DPCalendar (City of Babbitt)
`GET <root>/index.php?option=com_dpcalendar&view=events&limit=0&format=raw&module_id=&Itemid=&filter[calendars][]=<id>...&list[start-date]=&list[end-date]=`.
Returns nothing unless the calendar ids are passed, so ids (`g-13` = a
Google calendar wired into DPCalendar, digits = native), `Itemid` and
`module_id` are read from the page. Queried in 60-day windows. Recurrence
arrives expanded. The frequent-series rule keeps daily programs to 60 days.

### `exploremn` — Explore Minnesota (state tourism) · browser
Statewide Cludo search `POST /cludosearch/api`, run from inside the loaded
public page (plain requests 403). **One call per run** with `perPage: 2000`:
the site 403s repeat search calls from the same page load, and we don't
work around that. No region field, so events are kept only when
`listing_location`/`Cities` is in `DMA_TOWNS` (in the adapter; add towns
there). Runs of consecutive `allDates_iso_date` become one multi-day,
all-day event.

### `squarespace` — Squarespace events collection
`<events page>?format=json`. An events collection (`collection.typeName`
`events`, `events-stacked`, …) carries `upcoming`/`past` items with epoch-ms
UTC `startDate`/`endDate`. `events_url` must be the collection page itself.
Find it in `/sitemap.xml` (Gunflint keeps old shows at `/events-1`, and
Fitger's lives on `fitgersbarrelroom.com/events-calendar`). Booking forms and
plain pages are reported as "not an events collection".

### `nps` — National Park Service unit calendars
`home.nps.gov/common/components/nps/eventcalendar/EventCalendarService.cfc?method=getEvents&parkCode=<code>&dateStart=&dateEnd=&expandRecurring=true&pageSize=50&pageNumber=N`,
the public service behind every nps.gov calendar page. No key (the
api.nps.gov developer API needs one). `events_url: nps:<code>`: grpo, apis,
voya. Northern parks post mostly in summer.

### `posts` — text inference from news posts and pages (fallback)
For sites with no machine-readable calendar. Reads the events page (rendered
if it's a JS shell, or if the plain page yields nothing), WordPress
`/wp-json/wp/v2/posts`, advertised RSS/Atom feeds plus `/feed/`, Wix
`/blog-feed.xml`, Squarespace `?format=rss`, and up to 6 same-site links
labelled events/news/calendar. It finds explicit dates (weekday checked
against the date), then a title:

- `<h2>Title</h2><time>date</time>` → the heading, unless that heading sits
  over 3+ dated blocks (a section header like "Upcoming at the Taproom").
- One listing line → the side of the date that a separator points to:
  `Oct 18 - Harvest Fest` (after) vs `Board Game Night Thursday, Oct 29` (before).
- An announcement post → the post title and its first upcoming date. A post
  with 3+ dates is a roundup and is parsed like a listing page.

It rejects publication or deadline dates ("posted / due / by / until ..."),
agenda, minutes and closure text, calendar-grid chrome ("no events",
"Events in October 2026"), time-only titles, roundup titles ("What's
Tappening"), social or newsletter boilerplate, and sentence-like prose
titles. **Every event it emits has `inferred: true`, so a lone inferred event
goes to the review queue** (`docs/ARCHITECTURE.md`). Tune it with the test
strings in its module docstring before relying on a new site.

## Adding an adapter

1. Confirm the data is public and anonymous (Rules in `CLAUDE.md`). No keys or
   accounts without owner approval.
2. Prefer the endpoint the page itself calls (JSON/RSS/iCal) over HTML parsing.
   Use the headless browser only when a plain request is refused.
3. Write `adapters/<name>.py` with a module docstring covering endpoint,
   fields, and quirks. Return times as America/Chicago local, naive datetimes.
   **Time rule:** parse strings with `models.parse_dt`, which converts any
   offset or `Z` to Central. Convert Unix time with `models.from_epoch`.
   Never `.replace(tzinfo=None)` an aware value, and never call a bare
   `datetime.fromtimestamp()`, because CI runs in UTC. Both mistakes have
   put events 5-6 hours off. Compare a few results with the site's own page.
4. If it needs a browser, use `fetch.playwright_session()` or
   `fetch.browser()`, never your own `sync_playwright()`. A second instance in
   one process crashes ("Sync API inside the asyncio loop").
5. Register it in `adapters/__init__.py`: always in `BY_PLATFORM`, and in
   `GENERIC` only if it's cheap, precise and browser-free.
6. Pin the source(s) with `platform:` and test with `python main.py scrape --only <id>`.
7. Add parsing cases to `scraper/tests/test_posts.py` (or a sibling test
   file) for anything regex- or format-driven.
8. Update this file, the adapter lists in `CLAUDE.md`, and the table in `README.md`.
