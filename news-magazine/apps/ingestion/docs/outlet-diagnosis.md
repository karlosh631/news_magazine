# Outlet diagnosis — 2026-10-22

Every outlet named in the admin dashboard's error log was fetched by hand
(`curl` with a normal Chrome user agent) and its listing page parsed with the
**real** `HtmlListingAdapter`, then every discovered URL probed live. Nothing in
this document is inferred from the markup alone; the "verified" rows say how
many links actually resolved.

Two files come out of this:

- `supabase/migrations/0020_fix_outlet_selectors.sql` — the SQL the operator runs.
- this report — why each line is there.

No production database was touched. No migration under `0019` or earlier was
edited.

---

## What `selector_config` actually means

Read `app/sources/html_listing.py` before editing any of this. The contract:

| key | meaning | notes |
|---|---|---|
| `item` | CSS selector for **one card** | each match becomes one story; must be the smallest element wrapping a single headline |
| `link` | selector for the anchor **inside** the card | `select_one()`, i.e. **descendants only** — it does not match the card element itself |
| `title` | selector for the headline inside the card | falls back to the link's text, then its `title=` attribute |
| `image` | selector for the image inside the card | default `img`; set to `""` to skip listing thumbnails |
| `image_attr` | attribute preferred over `src` | for lazy loaders (`data-src`, …) |
| `date` | selector for a `<time>` inside the card | see the trap below |
| `exclude` | regex; a matching **href** is dropped | there is no `include` key |

Three traps that caused the reported failures:

1. **`item` must not be the `<a>`.** `_parse_card` does
   `card.select_one(config["link"])`, which searches descendants only. An
   `item` of `a[href^="/nepal/"]` yields a card that *is* the anchor, and every
   descendant query on it returns `None`, so every story is discarded silently.
   This is exactly what happened on The Himalayan Times.

2. **`"date"` is mandatory in practice, although it reads as optional.**
   `_parse_card` does:

   ```python
   if config.get("date", "time"):          # key absent -> "time"
       date_tag = card.select_one(config["date"])   # KeyError: 'date'
   ```

   Omit the key and the adapter raises `KeyError` on the first card. Every config
   written by this diagnosis carries an explicit `"date"` for that reason.

3. **There is no "include" regex.** An outlet whose listing mixes stories with
   chrome needs an allow-list written as a negative lookahead — see Sudarshan.

### What "did not resolve to an article page" means

`verify_entries()` GETs up to 40 discovered URLs and requires **all** of:

- HTTP status < 400,
- the final path is not `""` (a redirect to the homepage means the link was chrome),
- body ≥ 500 bytes,
- the body contains an `<h1>`.

If more than half the candidates fail it raises and the poll is abandoned. That
last rule — the `<h1>` — is what makes Ekantipur impossible today, and no
selector can talk its way around it.

---

## Results

| Outlet | URL checked | Works? | Verdict |
|---|---|---|---|
| The Himalayan Times | `thehimalayantimes.com/nepal` | **Yes** | selector wrong, fixed and verified |
| Sudarshan | `sudarshannews.com` → `sudarshannews.in` | Selector **yes**, content **no** | domain is now a Hindi Indian site |
| 24 News | `24news.com` | **No** | domain parked, for sale on GoDaddy |
| MyReplica | `myreplica.com` | **No** | domain parked, for sale on GoDaddy |
| Voice of Nepal | `voiceofnepal.com` | **No** | domain for sale at HugeDomains |
| Ministry of Foreign Affairs | `mofa.gov.np` | **No** | host unreachable; `generic_rss`, selectors irrelevant |
| Ekantipur | `ekantipur.com` | Selector **yes**, blocked by `<h1>` | needs a one-line code change first |
| Himal Khabar | `himalkhabar.com` | **No** | client-rendered shell, zero links in the HTML |
| Annapurna Post | `annapurbapost.com` | **No** | NXDOMAIN |
| Arthikabhojan | `arthikabhojan.com` | **No** | NXDOMAIN |
| Bhabishaanepal | `bhabishaanepal.com` | **No** | NXDOMAIN |
| Nepal Kantha | `nepalkantha.com` | **No** | NXDOMAIN |
| Nepalbhas | `nepalbhas.com` | **No** | NXDOMAIN |
| Newahale | `newahale.com` | **No** | NXDOMAIN |
| Prasuto Journal | `prasutojournal.com` | **No** | NXDOMAIN |

robots.txt was fetched before every listing page. Nothing in this diagnosis
touches a disallowed path.

---

## Fixed outlets (active SQL in `0020`)

### 1. The Himalayan Times — `https://thehimalayantimes.com/nepal`

- **robots.txt**: permits `/nepal` (only `/ajax/*`, `/search*`, `/login-mgt`,
  `/*.pdf`, `/widget/*` are disallowed). No sitemap was needed.
- **Server-rendered.** Plain server-side HTML, ~400 KB, 26 `<article>` elements.
  No JSON blob, no client rendering.
- **Was wrong because** `item` was
  `a[href^="/nepal/"], a[href^="/business/"], a[href^="/world/"]` with
  `link: "a"` — trap 1 above. Zero cards parsed, so the adapter fell through to
  the permissive anchor heuristic and collected site chrome. Hence *25/36
  candidate links did not resolve*, and the single survivor pointed at
  `/morearticles/Nepal`.
- **Cards come in two shapes**, which is why `link` is written the way it is:

  ```html
  <article class="row ...">       <figure><a class="ratio">…</a></figure>
                                  <h3 class="alith_post_title"><a href="…">…</a></h3>
  <article class="col-md-4 ...">  <a class="meta_section" href="/nepal">Nepal</a>
                                  <a class="ratio" href="…">…</a>
                                  <h3 class="alith_post_title"><a href="…">…</a></h3>
  ```

  The second shape puts the **section** link first. A bare `link: "a[href]"`
  would pick `/nepal` — a listing page, which passes the probe because it has an
  `<h1>`, and would be stored as an article. Pinning `link` to the headline
  anchor avoids that.

- **Verified**: 26 cards, 26 unique links, 26 images, **26/26 URLs resolve**
  (repeatable across runs). Images come from `data-src` on the `premiumread`
  CDN and return `200 image/webp`.

### 2. Sudarshan — selector fixed, but read this

- **robots.txt** (`sudarshannews.in`): `User-agent: * / Allow: /`. Permissive.
- **Two separate problems.**

  **(a) The domain is no longer the Nepali outlet.** `sudarshannews.com` now
  redirects to `www.sudarshannews.in`, which is *Sudarshn News* — a
  **Hindi-language Indian outlet** (Indore, Punjab, Assam, Madhya Pradesh). It
  is not in Nepali and not Nepali news. The `sources` row still says
  `default_language = 'ne'`, `sector = 'national'`, which would be wrong for
  what the page serves.

  **(b) The selector was wrong.** The old config listed
  `article, .post, .news-item` — none of which exist on this site. No card
  matched, so the anchor heuristic ran and collected chrome: `/AN`, `/DNH`,
  `/CG`, `/GJ`, `/MH`, `/UP`, `/BindasBolOnQuran`, `/UPSC_Jihad`. That is the
  *40/40* in the error message, and the exact scenario the `verify_entries`
  docstring warns about.

- **New selectors** cover the real card containers: `div.newssublist` (30),
  `div.newssublistnimg` (20), `div.trennewdata` (10), `div.livenewlist` (10),
  and the `<h2>/<h3>` inside `.bannewboxdata` (11). Article URLs are ASP.NET:
  `<slug>-<id>-newsdetails.aspx`.
- **Allow-list written as a negative lookahead**, since `exclude` is the only
  regex available:

  ```json
  "exclude": "^(?!.*newsdetails\\.aspx)"
  ```

  Read it as *"drop anything that is not a newsdetails.aspx link"* — that is
  what removes `/AN`, `/DNH`, `BindasBolOnQuran` and the `/bg-…` blog slugs.
- **Verified**: 58 cards, 58 unique links, **39/40 of the first 40 URLs
  resolve**. The single miss is a slug that contains a literal `?`, which the
  browser re-reads as a query string; the server then returns a different page.
  Well under the 50% threshold, so `verify_entries` does not raise.
- **No thumbnails.** The site paints artwork as a CSS `background-image` on
  `div.newlistimg`, which the adapter cannot read — it only looks at `<img>`
  attributes. Articles still get an image from `og:image` when the pipeline
  fetches the page.
- **Operator's call.** The `update` in `0020` fixes the selector only. The file
  also carries two *commented* alternatives: one that follows the domain to
  `.in` and relabels the row (`default_language = 'hi'`), and one that disables
  the source. Which one is right depends on whether Hindi content belongs in a
  Nepali portal — that is not a scraper decision.

---

## Cannot be fixed by any selector (commented-out SQL in `0020`)

### 24 News — `24news.com` — parked and for sale

`https://24news.com/` returns a **114-byte stub**:

```html
<!DOCTYPE html><html><head><script>window.onload=function(){window.location.href="/lander"}</script></head></html>
```

`/lander` answers `307 → https://forsale.godaddy.com/forsale/24news.com`.

The seed note called this a *"114-byte challenge page"* and suspected
anti-bot protection. It is not. It is a **GoDaddy parking redirect** — there is
no bot protection to wait out and no news content at any path. Both
`24news.com` and `myreplica.com` resolve to the same GoDaddy parking IPs
(13.248.169.48, 76.223.54.146), which confirms the shared infrastructure the
seed note suspected, for the wrong reason.

For the record: the Nepali broadcaster *News 24* (NBC) is at `news24nepal.com`
— a different channel on a different domain. Swapping it in is a sourcing
decision, so no such statement is pre-written.

### MyReplica — `myreplica.com` — parked and for sale

Byte-identical 114-byte stub, same GoDaddy `/lander` 307. Same verdict.

### Voice of Nepal — `voiceofnepal.com` — domain for sale

`https://www.voiceofnepal.com/` answers
`302 → https://www.hugedomains.com/domain_profile.cfm?d=voiceofnepal.com`,
a parked *"voiceofnepal.com is for sale"* page behind a captcha. This one is
HugeDomains, not GoDaddy. No robots.txt, no content.

### Ministry of Foreign Affairs — `mofa.gov.np` — host unreachable

Not a selector problem: this row uses `generic_rss`, and **`selector_config` is
never read by that adapter**. DNS resolves (`mofa.gov.np → 103.69.124.8`) but
TCP connect to **both :80 and :443 times out after ~21 s** from the ingestion
host.

Why the log says `fetch_or_parse_error` rather than `dns_failure`:
`RobotsChecker.is_allowed()` swallows the connection error and parses an empty
rule set (i.e. "allow"), so the request proceeds and dies on a connect timeout,
which `ingestion.py` has no DNS classifier for.

If the government site is blocked outside Nepal — likely for a `.gov.np` host —
disabling is the honest answer. If it is merely down, leave it enabled and it
will recover by itself. `0020` does not guess which.

---

## Re-enable candidates (all commented out in `0020`)

Enabling an outlet is the operator's decision: it asserts that the outlet's
terms permit automated access. Nothing below is pre-enabled.

### Ekantipur — selector verified, but **cannot be enabled yet**

The seed note said the homepage is client-rendered. **That is out of date** — it
is server-rendered, with 137 `div.news-section` cards in the HTML.

- `item`: `div.news-section:not(:has(.author-image))` — the four opinion cards
  whose only image is the writer's portrait are skipped, so a portrait is never
  stored as a story thumbnail. (soupsieve supports `:has()`, verified.)
- `link`: `h2 a[href], h3 a[href]` — the card's first anchor is often the
  `/author/…` link, so the selector is pinned to the headline anchor.
- **Verified**: 133 cards parsed (truncated to the adapter's own 80-card page
  limit), every link a real article URL of the form
  `/<section>/YYYY/MM/DD/<slug>-N-N.html`, HTTP 200 with `og:title`.
- **robots.txt** contains only a content-signals preamble — no `User-agent`, no
  `Disallow`. robots does not object.
- *** Why it still cannot run *** **Ekantipur article pages have no `<h1>`.**
  The headline is in `<h2>`, with structured data in a JSON-LD `headline` field.
  `verify_entries()` requires `<h1>`, so all 40 probed links fail and the poll
  raises *40/40 candidate links did not resolve* — exactly the shape of the
  error you would see if this were a selector problem, and it is not.
  Enabling it needs `is_article()` in `app/sources/html_listing.py` to accept
  `<h2>` / `og:title` as well. That is a code change and was not made here.

### Himal Khabar — `himalkhabar.com` — leave it off

The served HTML of the homepage contains **no article links at all**: only nav,
social, `/page/*`, `/patrachar`, `/unicode`, `/special`. No sitemap (`404`),
no `application/ld+json`, and no listing API behind the JS — `apps.js` and the
inline scripts reference only `/newsletter` and `/api/comment-*`. The strings
`news-container` and `news-cat` appear in scripts, not in markup.

Nothing in `selector_config` can select from a page that ships no listings.

### The seven NXDOMAIN outlets

`annapurbapost.com`, `arthikabhojan.com`, `bhabishaanepal.com`,
`nepalkantha.com`, `nepalbhas.com`, `newahale.com`, `prasutojournal.com` all
return **NXDOMAIN (RCODE 3)** for both apex and `www`, confirmed on the local
resolver *and* against Cloudflare DNS-over-HTTPS. The hostnames do not exist.

There is no listing page to select from, so no selector could be verified for
any of them, and **no enable statement is offered** — turning on a source whose
domain does not resolve buys nothing but `dns_failure` noise in the error log.
If these outlets have successors, register the new domains as new `sources`
rows rather than re-pointing these.

---

## What the operator should run

```sql
-- from supabase/migrations/0020_fix_outlet_selectors.sql
update sources
set selector_config = '{"item":"article","link":"h3 a[href], a.ratio[href]","title":"h3.alith_post_title, h2","image":"img","image_attr":"data-src","date":"time","exclude":"/author|/category|/tag/|/page/\\d+|\\.(jpg|jpeg|png|gif|webp|svg|css|js|pdf)$"}'::jsonb
where name = 'The Himalayan Times';

update sources
set selector_config = '{"item":"div.newssublist, div.newssublistnimg, div.trennewdata, div.livenewlist, .bannewboxdata h2, .bannewboxdata h3","link":"a[href]","title":"p, h2, h3","image":"img","image_attr":"data-src","date":"time","exclude":"^(?!.*newsdetails\\.aspx)"}'::jsonb
where name = 'Sudarshan';
```

Two statements, both idempotent, no schema change. Everything else in the file
is commented out and carries the reasoning inline.

### One bug worth fixing regardless

```python
# app/sources/html_listing.py, _parse_card
published = None
if config.get("date", "time"):
    date_tag = card.select_one(config["date"])   # KeyError when "date" is absent
```

`config.get("date", "time")` defaults the *condition* but the *body* indexes the
dict directly. Any `selector_config` written without a `date` key raises
`KeyError: 'date'` on the first card. One line — `config.get("date", "time")` in
both places — makes the key genuinely optional as documented. Every config in
this diagnosis carries an explicit `date` so nothing depends on that fix landing.

---

## How this was verified

- Every listing page fetched with `curl` (Chrome UA) after reading its
  robots.txt; pages saved and parsed offline, so no repeated live crawling.
- Every `selector_config` in `0020` was **extracted from the SQL file** and fed
  to the real `HtmlListingAdapter.parse()` against the real saved page — not
  retyped from the report.
- The JSON was asserted to parse, to contain only keys the adapter reads, to
  contain `item`/`link`/`title`/`image`/`image_attr`/`date` (the ones
  `_parse_card` indexes directly), to be valid CSS via BeautifulSoup, to be a
  valid regex, and for `link` to actually match a descendant anchor of a
  representative card.
- Final numbers, live probe of the first 12 links per outlet:

  | Outlet | cards | unique links | with image | probe |
  |---|---|---|---|---|
  | The Himalayan Times | 26 | 26 | 26 | 12/12 |
  | Sudarshan | 58 | 58 | 0 | 11/12 |
  | Ekantipur | 80 (of 133) | 80 | 8 | 0/12 — all `NO_H1`, the documented blocker |

- `pytest -q` in `apps/ingestion`: **423 passed** (the count moved from 421 only
  because `test_fetch_caps.py` and friends were already modified in the working
  tree before this work started; no application code was touched).