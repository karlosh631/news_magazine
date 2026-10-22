# Outlet expansion — what was tested, what works, and what does not

**Date of verification:** 2026-10-22
**Migration produced:** `supabase/migrations/0021_expanded_sources.sql`
**Runs after:** `supabase/migrations/0020_fix_outlet_selectors.sql`

## The short version

| | Count |
|---|---|
| Outlets tested | **70** |
| **Added** (new rows, enabled and allowed) | **15** |
| **Re-pointed** (existing rows, now working) | **4** |
| **Disabled** (existing rows, verified dead) | **12** |
| Could not be verified | 3 |
| Tested and deliberately not added | 25 |

Migration `0021` adds 15 outlets and re-points 4, and disables 12. No
row is ever deleted: a `sources` row is referenced by the articles
already stored, and deleting it would orphan them.

> **Note on the brief.** The operator's 50+ item candidate list was not
> attached to the task. The candidate pool below was therefore built
> from the current live directories of Nepali outlets (a 2026-dated
> 45-site ranking, plus the outlets already seeded) and every entry
> in it was verified against the live web. If the operator's own list
> contains outlets not in this table, they are not covered here.

> **Note on 0020.** A parallel process produced
> `0020_fix_outlet_selectors.sql` at the same time, which repairs the
> `selector_config` of the rows that were already seeded. It reaches
> the same verdict as this work on every outlet it examines — the same
> seven NXDOMAIN domains, the same three GoDaddy-parked domains, the
> same unreachable MOFA host, and the same root cause behind the
> broken listing selectors. 0021 runs after it and does not undo any
> of it: where the two touch the same row (The Himalayan Times) they
> install the same selector, and where they differ (Ekantipur and
> Himal Khabar, which 0020 leaves commented out) 0021 is the one that
> switches them on, with a configuration that was measured.

---

## How each outlet was verified

1. **robots.txt first.** Fetched for the host and parsed with the bot's
   real User-Agent (`NepalNewsMagazineBot/1.0`). A path robots.txt
   disallows is not addable, and is reported as such.
2. **A 200 is not a feed.** The body was parsed with `feedparser` and
   had to yield `item`/`entry` elements whose entries each carry a
   **title, a link and a date**. An HTML page, a domain-parking lander
   or a Cloudflare challenge is not a feed, however it answers.
3. **The repo's own adapter ran.** Every feed row was run through
   `GenericRssAdapter.parse()` + `.normalize()` and the articles it
   produces were counted. Every listing row was run through
   `HtmlListingAdapter.parse()` and then its `verify_entries()` gate —
   the same code the worker runs — so "verified" means "the pipeline
   will actually get articles from this", not "it looked plausible".
4. **DNS was checked twice.** The local resolver fails transiently, so
   every NXDOMAIN was re-checked against Cloudflare's DNS-over-HTTPS
   resolver. A wrong NXDOMAIN would have disabled a healthy outlet.
5. **Freshness was measured.** The newest item date in each feed was
   read. A feed that parses but stopped publishing a year ago is not a
   source.

---

## A. Outlets already seeded by migration 0008 (27 tested)

| Outlet | Candidate URL(s) tested | Verdict | URL that works / why it fails |
|---|---|---|---|
| OnlineKhabar | `/feed` | **WORKS** | `https://www.onlinekhabar.com/feed` — 55 entries, all dated |
| OnlineKhabar English | `/feed` | **WORKS** | `https://english.onlinekhabar.com/feed` — 20 entries, all dated |
| Kantipur (The Kathmandu Post) | `/rss` | **WORKS, but no dates** | `https://kathmandupost.com/rss` — 40 entries, **zero** carry a `pubDate`. Left as it is (it is live and working in production) but see the finding on undated feeds below |
| Ratopati | `/feed` | **WORKS** | `https://ratopati.com/feed` — 30 entries, all dated |
| Setopati | `/feed` | **WORKS** | `https://www.setopati.com/feed` — 5 entries, all dated |
| Nepal News | `/feed` | **WORKS** | `https://nepalnews.com/feed` — 12 entries, all dated |
| Nepal News English | `/feed` | **WORKS** | `https://english.nepalnews.com/feed` — 12 entries, all dated |
| Nagarik News | `/feed` | **WORKS** | `https://nagariknews.com/feed` → redirects to `nagariknetwork.com/feed` — 20 entries |
| Nagarik Times | `/feed` | **WORKS** | `https://nagariktimes.com/feed` — 12 entries, all dated |
| Khabarzone | `/feed` | **WORKS** | `https://www.khabarzone.com/feed` — 10 entries, all dated |
| Pradesh Post | `/rss` | **WORKS** | `https://www.pradeshpost.com/rss` → redirects to `pradeshpost.com/feed` — 10 entries |
| BBC Nepali | `/index.xml` | **WORKS** | `https://www.bbc.com/nepali/index.xml` → 302s to `https://feeds.bbci.co.uk/nepali/rss.xml` — 20 entries. **Re-pointed** in 0021 at the canonical URL |
| Ministry of Foreign Affairs | `/rss` | **UNREACHABLE — disabled** | Resolves (103.69.124.8) but refuses TCP on 443 **and** 80. `ECONNREFUSED` from one network path, connect timeout from another, no HTTP response at all. Disabled as unreachable, not confirmed dead |
| The Himalayan Times | `/rss`, `/feed` | **LISTING ONLY — re-pointed** | `/rss` returns HTTP 200 but the body is an HTML page; `/feed` returns 404. The listing page works: 26 cards, 26/26 verified, all with images |
| Annapurna Post | 8 paths | **DEAD — disabled** | `annapurbapost.com` is **NXDOMAIN** (apex and www, confirmed via Cloudflare DoH). No path resolves |
| 24 News | 9 paths | **DEAD — disabled** | Every path returns the same **114-byte** HTML shell that redirects the browser to `/lander` — a GoDaddy parking lander. The 0008 note called this a "challenge page"; it is a parked domain |
| MyReplica | 9 paths | **DEAD — disabled** | The same 114-byte `/lander` parking shell as 24 News. **Not** the same outlet as the new myRepublica row — one letter apart, unrelated sites |
| Newahale | 8 paths | **DEAD — disabled** | `newahale.com` is **NXDOMAIN** (Cloudflare DoH confirmed) |
| Nepal Kantha | 8 paths | **DEAD — disabled** | `nepalkantha.com` is **NXDOMAIN** |
| Bhabishaanepal | 8 paths | **DEAD — disabled** | `bhabishaanepal.com` is **NXDOMAIN** |
| Arthikabhojan | 8 paths | **DEAD — disabled** | `arthikabhojan.com` is **NXDOMAIN** |
| Voice of Nepal | 8 paths | **DEAD — disabled** | Redirects to `hugedomains.com/domain_profile.cfm?d=voiceofnepal.com` — the domain is **parked and for sale** |
| Nepalbhas | 8 paths | **DEAD — disabled** | `nepalbhas.com` is **NXDOMAIN** |
| Himal Khabar | 8 paths | **WORKS — re-enabled** | `https://www.himalkhabar.com/feed` — 5 entries, all dated, all with images. Seeded disabled as "client-rendered"; that was true of the *listing page*, not the feed. **Re-pointed at the feed in 0021** |
| Prasuto Journal | 8 paths | **DEAD — disabled** | `prasutojournal.com` is **NXDOMAIN** |
| Sudarshan | 8 paths | **DEAD — disabled** | No feed: `/feed`, `/rss`, `/rss.xml`, `/feed.xml`, `/index.xml`, `/rss/` all 404. The homepage yields 80 links that are site chrome, not articles (`/AN`, `/DNH` are state abbreviations; `/BindasBolOnQuran` is a campaign slug). The repo's own `verify_entries()` rejected **all 80**. The domain now also redirects to `www.sudarshannews.in`, a **Hindi-language Indian** site — a different outlet — so the row is disabled rather than re-pointed at Hindi content |
| Ekantipur | 8 paths | **LISTING ONLY — re-pointed** | No feed: `/feed`, `/rss`, `/rss.xml`, `/feed.xml`, `/index.xml`, `/rss/` all 404, and `/?feed=rss2` just returns the homepage. The listing works: 130 cards matched, 80/80 verified. Seeded `allowed = false`; robots.txt has **no** `User-agent` or `Disallow` directive at all, so it is now allowed |

## B. New candidates (43 tested)

| Outlet | Domain | Verdict | URL that works / why it fails |
|---|---|---|---|
| The Nepali Times | nepalitimes.com | **ADDED** | `https://nepalitimes.com/feed` — 30 entries, all dated, all with images |
| Karobar Daily | karobardaily.com | **ADDED** | `https://www.karobardaily.com/feed` — 41 entries, the largest feed found |
| News of Nepal | newsofnepal.com | **ADDED** | `https://newsofnepal.com/feed/` — 12 entries, all dated |
| Rajdhani Daily | rajdhanidaily.com | **ADDED** | `https://rajdhanidaily.com/feed/` — 10 entries, all dated |
| Ujyaalo Online | ujyaaloonline.com | **ADDED** | `https://www.ujyaaloonline.com/feed/` — 5 entries, all dated |
| Artha Sarokar | arthasarokar.com | **ADDED** | `https://arthasarokar.com/feed` — 20 entries, all dated, all with images |
| Nepal Samaya | nepalsamaya.com | **ADDED** | `https://nepalsamaya.com/feed/` — 5 entries, all dated |
| OSNepal | osnepal.com | **ADDED** | `https://www.osnepal.com/feed` — 5 entries, all dated |
| Baahrakhari | baahrakhari.com | **ADDED** | `https://baahrakhari.com/feed` — 5 entries, all dated |
| Online TV Nepal | onlinetvnepal.com | **ADDED** | `https://onlinetvnepal.com/feed/` — 10 entries, all dated |
| Thaha Khabar | thahakhabar.com | **ADDED** | `https://www.thahakhabar.com/feed/` — 5 entries, all dated |
| Biz Khabar | bizkhabar.com | **ADDED** | `https://bizkhabar.com/feed/` — 10 entries, all dated |
| Techmandu | techmandu.com | **ADDED** | `https://techmandu.com/feed/` — 11 entries, all dated. But the newest item is **10 days old** and the *site* matches (2026-10-11, 2026-09-26, 2026-08-22) — the outlet publishes every one to three weeks, so it is polled at 600s |
| Nepal Patra | nepalipatra.com | **ADDED (listing)** | `/feed` works but its newest item is **344 days old**. The site itself is publishing (newest article 2026-10-22), so the **listing** is used: 36 cards, 35/36 verified |
| myRepublica | myrepublica.nagariknetwork.com | **ADDED (listing)** | No feed at all. Listing: 80 cards, 80/80 verified, 55 with images. robots.txt is `User-agent: * / Disallow:` — everything allowed |
| Arthik Abhiyan | abhiyandaily.com | NOT ADDED | `/rss` parses (20 entries) but **every link points at `janaaastha.com`** — it is a syndication feed, and it carries no dates |
| Naya Patrika Daily | nayapatrikadaily.com | NOT ADDED | `/rss` parses (20 entries) but every link points at `janaaastha.com`, and it carries no dates |
| New Business Age | newbusinessage.com | NOT ADDED | `/rss` parses (20 entries) but every link points at `janaaastha.com`, and it carries no dates |
| Jana Aastha | janaaastha.com | NOT ADDED | `/rss` works, 20 entries — but **no item carries a date** |
| Aarthik News | aarthiknews.com | NOT ADDED | `/rss` works, 20 entries — but **no item carries a date** |
| Lokaantar | lokaantar.com | NOT ADDED | `/feed` works, 20 entries — but **no item carries a date** |
| The Annapurna Express | theannapurnaexpress.com | NOT ADDED | `/rss` works, 20 entries — but **no item carries a date** |
| Telegraph Nepal | telegraphnepal.com | NOT ADDED | `/feed` works, 10 entries — but the newest item is **873 days old**, and the site itself is dead (its newest path date is 2021-03-13) |
| Kathmandu Tribune | kathmandutribune.com | NOT ADDED | `/feed` works but its newest item is **51 days old** while the site is publishing, so the listing was the alternative — 10 cards, 10/10 verified. Not added because the site''s own title is "A Literary and Commentary Journal" rather than a news daily, it publishes every one to three weeks, and it **intermittently serves a Cloudflare challenge** (a 12 KB page with no articles) when it is polled in a burst, which fails the poll. It is a good candidate for a human to switch on with a 600s interval, but it is a poor automatic source |
| Nepali Post | nepalipost.com | NOT ADDED | No feed. The listing yields 12 real headlines, but no images and it is a small diaspora title, not a national outlet |
| Hamrakura | hamrakura.com | NOT ADDED | No feed. The listing yields 80 links, but they are tag pages (`/tags/Nepali-Congress-General-Convention`) and section pages, not articles |
| Adarsha Samaj | eadarsha.com | NOT ADDED | No feed. The listing produces **no headlines at all** — the adapter raises "No headlines found" |
| People's Review | peoplesreview.com.np | NOT ADDED | No feed: `/feed` answers HTTP 415 to the bot and returns an HTML page to a browser |
| Online Majdoor | onlinemajdoor.com | NOT ADDED | No feed: HTTP 415 to the bot, an HTML page to a browser |
| Top Nepal News | topnepalnews.com | NOT ADDED | No feed: HTTP 415 to the bot, an HTML page to a browser |
| News24 Nepal | news24nepal.tv | **NOT ADDABLE — robots.txt** | `robots.txt` says `Disallow: /`. Every path is disallowed for our User-Agent. Not addable, full stop |
| Gorkhapatra Online | gorkhapatraonline.com | **COULD NOT VERIFY** | Resolves (103.69.124.67) but **refuses TCP on 443** — `ECONNREFUSED` from a second, independent network path. See the note on the 103.69.124.0/24 block below |
| Rising Nepal Daily | risingnepaldaily.com | **COULD NOT VERIFY** | Resolves (103.69.124.68) and refuses TCP on 443, exactly like Gorkhapatra Online. Same block |
| Naya Page | nayapage.com | **COULD NOT VERIFY** | The host times out on read; `/rss` returns 404. No response could be obtained at all |
| Gorkha Baadi | gorkhabadi.com | NOT ADDED | **NXDOMAIN** (Cloudflare DoH confirmed) |
| Nepal Bulletin | nepalbulletin.com | NOT ADDED | The same **114-byte** `/lander` parking shell as 24 News and MyReplica |
| Big Nepal | bignepal.com | NOT ADDED | HTTP 403 behind a Cloudflare "Just a moment…" challenge, redirecting to `domaineasy.com/buy-domain/bignepal.com` — the domain is for sale |
| Nepal Post | nepalpost.com | NOT ADDED | The same **114-byte** `/lander` parking shell |
| Aafno Aankhabar | aafnoaankhabar.com | NOT ADDED | **NXDOMAIN** |
| Nepsutra | nepsutra.com | NOT ADDED | **NXDOMAIN** |
| Baalbalidibus | baalbalidibus.com | NOT ADDED | **NXDOMAIN** |
| Naya Bharat | nayabharat.com | NOT ADDED | The same **114-byte** `/lander` parking shell |
| Chalchitra | chalchitra.com | NOT ADDED | The TLS certificate is **self-signed**, so `httpx` refuses it with `CERTIFICATE_VERIFY_FAILED`. The ingestion service would fail on every request. Not fixable from this side — turning off verification would be a bad trade |

### The 103.69.124.0/24 block

`mofa.gov.np` (103.69.124.8), `gorkhapatraonline.com` (103.69.124.67) and
`risingnepaldaily.com` (103.69.124.68) all resolve but refuse TCP
connections on 443. Two independent network paths agree
(`ECONNREFUSED` and a connect timeout), so this is not one flaky
router. It looks like a host-level outage or a block on that address
range. **MOFA is disabled as "unreachable"; the other two were never
seeded and are not added.** All three are worth re-testing from a
Nepali network before being written off.

---

## Why some working feeds were still not added

### Feeds that carry no date

Jana Aastha, Aarthik News, Lokaantar, The Annapurna Express, Arthik
Abhiyan, Naya Patrika Daily and New Business Age all serve a feed
that parses cleanly. **Not one item in any of them carries a
`pubDate`.** The pipeline substitutes the fetch time for a missing
date (`app/services/ingestion.py:1712`, `"published_at":
article.published_at or now`), so every one of those outlets would
re-publish its whole back catalogue as "just now", on every poll,
forever. That is worse than not having the outlet.

The seeded **Kantipur** row is already in this position — 40 entries,
zero dates — and it has been running in production. That is a
pre-existing issue and it was left alone here, but it is worth the
operator's attention.

### The Jana Aastha syndication group

`abhiyandaily.com/rss`, `nayapatrikadaily.com/rss` and
`newbusinessage.com/rss` each return 20 items whose `<link>` points
at **`janaaastha.com`**. They are one publisher's content served
three ways. Adding all four rows would store the same story four
times under one canonical URL and fight the dedup for nothing.

---

## Two bugs in the html_listing adapter, found and fixed

Both were found by running the seeded configurations against the live
web, and both are covered by new tests in
`apps/ingestion/tests/test_html_listing_config.py`.

**1. `UnboundLocalError` on every seeded listing row.**
`_parse_card` assigned `title` only when the config named a `title`
selector, then read `title` unconditionally on the next line. Every
selector_config in migration 0008 has an `item` and a `link` and
**no** `title`, so the first card whose `item` selector actually
matched raised `UnboundLocalError` — a failure that reads like a
broken website rather than a broken config.

**2. The operator's `item` selector was silently ignored.**
BeautifulSoup's `select_one()` only searches *descendants*, so an
`item` selector that picked the anchor itself could never find a
link inside itself. Every card was dropped, the adapter fell back to
its anchor heuristic, and it reported success while doing something
else. This is why the seeded selectors — which are written in the
anchor form (`a[href^="/nepal/"]`) — appeared to work: they never
actually ran.

Both are fixed in `app/sources/html_listing.py` (committed in
`d1f491b`), and the `selector_config` values in migration 0021 are
written against the fixed behaviour and re-verified against the live
pages.

**The myRepublica row depends on fix 2.** Its cards are anchors
(`<a href="/news/..."><h3>headline</h3></a>`), and the containers
around them are Tailwind class soup with no stable selector, so
`"item": "a[href*='/news/']"` with the anchor as its own card is the
only workable configuration. The row's `notes` say so.

### A third finding, reported but not changed

`_plausible_headline` rejects any headline containing the Nepali
page-furniture marker **`समाचार`** ("news"). That word appears in
real headlines — "समाचार कस्तो छ प्रवासी नेपालीको दसैं तयारी ?" and
"समाचार नौ वर्षदेखि बनेन शिवगंगाको पुल…" are both genuine stories
that the filter throws away. The same filter also rejects any
headline containing a semicolon.

Measured impact: 1–9 page texts dropped per listing page, of which
2–3 are real headlines (Ekantipur 3, Nepal Patra 2, MyRepublica 9
texts of which most are genuine). It was left alone because
loosening the marker list risks letting navigation labels back in,
and it is a content-quality decision rather than part of this
migration. It is worth a follow-up.

---

## STEP 5 — pulling more than ten items out of a feed

**The mechanism the brief asked for does not work.** Measured on 2026-10-22
against ten live Nepali WordPress feeds:

| Parameter | Effect |
|---|---|
| `?posts_per_page=100` | **Nothing.** The item count *and* the first item are identical with and without it on **10 of 10** feeds. WordPress builds the main feed's query with `posts_per_page` pinned to the site's `posts_per_rss` option and ignores the query string |
| `?paged=2`, `?paged=3` | **Works.** Returns the *next* page of items — older stories, the same count per page. Verified on khabarzone.com, bizkhabar.com, pradeshpost.com and karobardaily.com |

So the page size is still sent (it is free, and it does help any site
that does honour it) and **paging** is the mechanism that actually
delivers more items.

**What was implemented**, in `app/sources/generic_rss.py` and
`app/config.py`:

| Setting | Default | What it does |
|---|---|---|
| `RSS_PAGE_SIZE` | `100` | The page size asked of a WordPress feed. `0` asks for nothing |
| `RSS_PAGE_SIZE_PARAM` | `posts_per_page` | The parameter name, for sites running a feed plugin |
| `RSS_FEED_PAGES` | `1` | How many pages to request and merge. `1` is exactly today's behaviour: one request, one document, the same articles |
| `RSS_WORDPRESS_HOSTS` | unset | Comma-separated hosts added to the built-in WordPress list, without a deploy |

**Safety rules, all asserted by tests in
`apps/ingestion/tests/test_rss_paging.py`:**

* Only a **known WordPress host** gets any parameter. The list was
  built by reading each feed's `<generator>` tag, not by guessing. A
  non-WordPress feed is fetched exactly as it was, whatever the
  settings say.
* A URL that already has a query string is extended with **`&`**,
  never `?`, so nothing is thrown away.
* The number of pages is **capped at 10**, so a misconfigured setting
  cannot turn a poll into a crawl.
* `robots.txt` is checked for **every** paged URL, not only the first.
* A malformed page does not lose the pages that worked.
* A story that appears on two pages is stored once.

**Paging is off by default on purpose.** Every extra page is one
extra request to a publisher, and being a polite client is this
codebase's first rule. A source polled every 180s that serves 10 items
per page would need 10 pages to reach 100 items, and the operator
should decide how much of that a publisher owes them. To turn it on:
`RSS_FEED_PAGES=3`.

---

## What was NOT done, and why

* **No production database was touched.** Every check here was a
  read-only `GET` against a public website. The operator applies
  migration 0021 themselves.
* **No migration below 0020 was modified**, and `0020` itself was
  left alone.
* **Nothing under `apps/web` was touched.**
* **No commits and no pushes.** A parallel process committed some of
  the working tree while this work was in progress; nothing here was
  committed by hand and nothing was pushed.

## Suggested next steps

1. Apply `supabase/migrations/0021_expanded_sources.sql` (after
   0020).
2. Watch the first poll: the new listing rows are the ones most
   likely to need a selector tweak, because they read live markup
   that a publisher can change without warning. The `notes` column on
   each row says how many cards it collected on 2026-10-22, so a
   sudden drop to zero is obvious.
3. Re-test `mofa.gov.np`, `gorkhapatraonline.com` and
   `risingnepaldaily.com` from a Nepali network.
4. Decide what to do about feeds that carry no date — Kantipur is
   already live in that state, and seven more outlets are waiting on
   that answer.
5. Consider Kathmandu Tribune by hand: it works, it is just a slow
   literary journal behind an occasional Cloudflare challenge.
