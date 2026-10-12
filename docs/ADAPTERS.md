# Adapters

Every adapter lives in `scraper/adapters/`, implements `scrape(source) ->
AdapterResult` (`base.py`), and is registered in `adapters/__init__.py`.
This file records **what each one talks to and what bit us**. The module
docstring of each adapter is the detailed reference. Keep both in sync.

## How selection works

- `platform:` set in `sources.yaml` → that adapter only (`BY_PLATFORM`).
- No `platform:` → the `GENERIC` chain, in order, first result with events wins.
  Order is cheap/precise first, expensive/broad last.
- Adapters that drive a headless browser are **pin-only**. Never add them to
  `GENERIC`, because the chain runs on every unpinned source.

## Generic chain (in order)

### `tribe` — WordPress "The Events Calendar"
`GET /wp-json/tribe/events/v1/events`. Aliases: `wordpress-tribe`. Tribe
categories are mapped and left alone by `infer_category`. Some WP hosts
rate-limit GitHub Actions IPs (see `docs/SOURCES.md`). If wp-json is
bot-walled, use `tribehtml`.

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
`GET <root>/events` (also tries `/calendar`, `/meeting-calendar`). Month
params `CM`/`CY` are ignored by the server. Two cell layouts exist:
`a.eventLink` + `.eventTip`, or `a.eventLink` + `.eventDates`.

### `heygov` — HeyGov
`GET https://api.heygov.com/<domain>/events?month=YYYY-MM&expand=parent&source=calendar-embed`
per month. **Gotcha:** `starts_at_local` is local wall time despite the
trailing `Z`.

### `jsonld` — schema.org Event JSON-LD
Pulls `Event` objects (direct, arrays, `@graph`) from the events page, with a
headless-render fallback, and samples a few linked detail pages. Pinned for
lsvr-events WordPress sites (visit-ashland, lake-county-mn) with
`events_url: /events/`. Does not set a category.

### `ics` — iCal feed
Discovers `<link rel=alternate type=text/calendar>`, `.ics` / `?ical=` /
`outlook-ical` links, or the Tribe `?ical=1` convention. Also follows
`webcal://`, a direct `.ics` `events_url`, and Google Calendar embed feeds.

### `chamberorganizer` — ChamberOrganizer
`POST auth.chamberwidgets.com/cn-api/org/calendar/events` with form
`searchValues=&org_id=<CODE>&year=&month=&day=`, one per month. The org code
comes from `org_id=` in page HTML.

### `chambermaster` — ChamberMaster / GrowthZone MIC
Walks `<mic>/events/calendar/<YYYY-MM-01>` to list detail URLs, then parses
schema.org microdata on `/events/details/` pages. Host discovery probes
`business.` / `members.` subdomains and homepage links to
`*.chambermaster.com` / `*.growthzoneapp.com`. Alias: `growthzone`. Slowest
generic adapter, which is why it's last.

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

## Adding an adapter

1. Confirm the data is public and anonymous (Rules in `CLAUDE.md`). No keys or
   accounts without owner approval.
2. Prefer the endpoint the page itself calls (JSON/RSS/iCal) over HTML parsing.
   Use the headless browser only when a plain request is refused.
3. Write `adapters/<name>.py` with a module docstring covering endpoint,
   fields, and quirks. Return times as America/Chicago local, naive datetimes.
4. Register it in `adapters/__init__.py`: always in `BY_PLATFORM`, and in
   `GENERIC` only if it's cheap, precise and browser-free.
5. Pin the source(s) with `platform:` and test with `python main.py scrape --only <id>`.
6. Update this file, the adapter lists in `CLAUDE.md`, and the table in `README.md`.
