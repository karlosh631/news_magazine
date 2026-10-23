# Nepal News & Magazine

Real-time Nepali news aggregation. A Python ingestion service scrapes 27
Nepali outlets on their own schedules, deduplicates syndicated stories,
extracts high-resolution media and a four-point summary from each
article's own page, and serves them to a Next.js frontend backed by
Supabase.

Nothing on this site is generated. Every headline, image and summary
point comes verbatim from the outlet that published it.

---

## What this does

| | |
|---|---|
| **Sources** | 27 Nepali outlets, 25 active — 10 with public RSS/Atom feeds, 15 scraped from their listing pages |
| **Cadence** | Each source polls on its own interval (3–30 min), staggered so outlets are not hit simultaneously |
| **Deduplication** | Cross-outlet syndicated copies collapse into one story, with an "also reported by N outlets" count |
| **Media** | `og:image`, YouTube/Vimeo embeds, and podcast audio scraped from each article page |
| **Summaries** | Exactly four points per story, extracted verbatim from the article body |
| **Retention** | Articles pruned 48h after publication, then re-fetched on demand if someone searches for them |
| **Search** | Postgres full-text + trigram, so Nepali (Devanagari) queries actually match |
| **Reels** | Short video from Wikimedia Commons, Pexels, Pixabay and YouTube, served newest-arrival first |
| **Admin** | Sign-in, live dashboard, per-source health, and the reels feed's own source health |

---

## Architecture

```
   ┌──────────────────┐        ┌────────────────────────┐
   │  Next.js (web)   │  anon  │      Supabase          │
   │  apps/web        │───────▶│  Postgres · RLS        │
   └──────────────────┘        └───────────▲────────────┘
         ▲                                │ service-role
         │ server-side search call        │
         │                                │
   ┌─────┴──────────────────┐   ┌────────┴───────────┐
   │  FastAPI (ingestion)   │   │  worker.py daemon  │
   │  :8000                 │   │  polls 27 outlets  │
   │  /api/scrape-on-demand │   │  every 3–30 min    │
   └────────────────────────┘   └────────────────────┘
```

Two processes, two jobs. `worker.py` owns scraping; the FastAPI service
owns the HTTP surface. Running them in one process works for local
development but must not be scaled horizontally — two schedulers means
two sets of requests to every publisher.

See `ARCHITECTURE.md` for the full diagram and design rationale.

---

## Setup

### 1. Supabase

Apply the migrations in order. `scripts/supabase_admin.py` does this
through the Management API:

```bash
# 1. Put NEXT_PUBLIC_SUPABASE_URL and API_ACCESS_TOKEN in apps/web/.env.local
#    (API_ACCESS_TOKEN: Supabase dashboard -> Account -> Access Tokens)

python apps/ingestion/.venv/Scripts/python.exe scripts/supabase_admin.py plan
python apps/ingestion/.venv/Scripts/python.exe scripts/supabase_admin.py status
python apps/ingestion/.venv/Scripts/python.exe scripts/supabase_admin.py apply
```

`apply` runs each file once and records it in `_applied_migrations`, so
re-running is safe.

### 2. Migrations

Apply these in order. Each is idempotent, so re-running is safe.

| Migration | What it does |
|---|---|
| `0017_admin_users.sql` | The `admin_users` and `admin_sessions` tables the admin panel signs in against, and the seeded operator account |
| `0018_reels_source_health.sql` | One row per reels provider: enabled, which env var is missing, key count, and how the last run went |
| `0019_articles_ingested_at_index.sql` | The index behind the arrival-time tiebreaker, and a backfill of arrival time for rows that predate the column |
| `0020_fix_outlet_selectors.sql` | Working selectors for the outlets whose listing pages could be read, and the reasoning for the ones that cannot |
| `0021_expanded_sources.sql` | The expanded outlet list: the dead outlets disabled and the verified new ones added |
| `0022_daily_panchang.sql` | The daily almanac: one row per day, with the tithi, the nakshatra and the time windows |
| `0023_rashifal_category.sql` | The Rashifal category row |
| `0024_purge_spam_articles.sql` | The removal of the search-engine spam a parked domain served as news |
| `0025_daily_rashifal.sql` | The twelve horoscopes: twelve rows a day, one per sign |
| `0031_admin_login_attempts.sql` | The durable counters that slow a password guess down, per email address and per IP, and the column that says which hashing policy an account's hash was written under |

Migrations 0001–0016 are the original schema and are described in
`ARCHITECTURE.md`.

**0017 is safe to run twice.** PostgreSQL has no `create policy if not
exists`, so the policies are dropped and recreated — otherwise a second
run dies on the policy and everything after it in the file never
happens.

**0031 is the sign-in's rate limit.** It is optional in the sense that
the panel still works without it — the counters then live in the
process's memory and are lost on a restart — but with it the limit
survives a restart and is shared by every instance of the app. Apply
it.

### 3. Ingestion service

```bash
cd apps/ingestion
python -m venv .venv                      # Python 3.12
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# .venv/bin/pip install -r requirements.txt                  # macOS/Linux
cp .env.example .env                      # then fill it in
```

`.env` needs `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`. Everything
else has a working default.

Run the daemon:

```bash
.venv/Scripts/python.exe -m app.worker
```

It runs continuously, re-reads the sources table every 5 minutes so
admin edits take effect without a restart, backs off exponentially per
failing source, and survives any individual source error.

Run the HTTP service alongside it (for search resurrection):

```bash
SCHEDULER_ENABLED=false .venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
```

### 4. Frontend

```bash
cd apps/web
cp .env.example .env.local               # fill in Supabase keys
npm install
npm run dev                              # http://localhost:3000

# NOTE: `npm run build` overwrites the same .next directory the dev
# server uses. Stop `npm run dev` first, or the site goes blank until
# you restart it. CI has no dev server, so it is unaffected.
```

---

## Daily archive (optional)

Snapshots the day's published news into `data/daily/YYYY-MM-DD.json` and
commits it, giving you a dated record of what the pipeline actually
produced — useful for auditing coverage and dedup decisions after the
48-hour TTL has pruned the live rows.

`.github/workflows/archive.yml` runs this automatically at 23:45 UTC and
pushes the snapshot, once the repository secrets above are set.

To run it locally instead:

```bash
cd apps/ingestion
.venv/Scripts/python.exe scripts/install_daily_archive.py     # daily at 23:45
.venv/Scripts/python.exe scripts/install_daily_archive.py --remove
```

It commits locally. It does not push unless you run the GitHub workflow,
which does.

---

## Machine translation (optional)

`scripts/translate_content.py` adds a machine translation of the headline
and the four summary points to articles that have none yet.

It is **off by default** (`ENABLE_TRANSLATION=false`), and the reason
matters: a translation is not the outlet's reporting. So the schema
forbids the mistake — `headline` and `summary_points` are never
overwritten, translations go to `headline_translated` and
`summary_points_translated`, and the page labels them as
machine-generated. A missing translation is fine; an unmarked fabricated
one is not.

```bash
cd apps/ingestion
ENABLE_TRANSLATION=true .venv/Scripts/python.exe scripts/translate_content.py
```

Needs `GEMINI_API_KEY` and migration `0011_translation_columns.sql`.

---

## How much one cycle may fetch

A cap that only exists in a config file is a lie told to the operator.
There are four, and every one is enforced **at the point the work is
done** — articles past a cap are never normalised, deduped, page-fetched
for their media or stored, not merely counted.

| Setting | Default | Bounds |
|---|---|---|
| `MAX_ARTICLES_PER_SOURCE` | 500 | One outlet's contribution in one poll |
| `MAX_ARTICLES_PER_CYCLE` | 20,000 | The whole pipeline, in one cycle |
| `CYCLE_WINDOW_SECONDS` | 900 | How long one cycle is — a rolling window |
| `MAX_REELS_PER_PROVIDER` | 500 | One video provider's contribution in one pass |
| `MAX_REELS_PER_CYCLE` | 5,000 | The whole reels pass |

Raise one and the pipeline really does fetch more; lower one and it
really does fetch less. When a cap truncates a cycle it is logged, it
is written to `source_errors` as `fetch_capped` with the cap and the
number dropped, and the sync log carries it — so the dashboard shows
it rather than the operator wondering why a quiet outlet is quiet.

The per-cycle cap is a rolling window rather than a fixed schedule,
because the scheduler has no single pass over every outlet: each
outlet is polled on its own interval, and the window is what bounds
the pipeline's total appetite.

**The almanac is not one of the capped things, and it is on a budget of
its own.** It is a single source, and it is not a news outlet, so the
per-cycle caps do not describe it. What bounds it is that the pass asks
the database first: today's row is refreshed at most once an hour, and
the week ahead is walked at most once an hour and only when a day is
missing from it. In steady state that is one request an hour for today
and eight a day for the week ahead — about forty a day, against a
source that serves one page to each of its own visitors.

---

## Ingestion endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness + real database probe. Reports `degraded` rather than false-passing. |
| POST | `/api/sync` | Force a sync. Requires `x-ingestion-secret`. |
| GET | `/api/sources` | List sources and their adapters. Requires secret. |
| GET | `/api/sources/health` | Per-source sync status. Requires secret. |
| GET | `/api/search?q=` | Database-only search. Never scrapes, safe per keystroke. |
| POST | `/api/scrape-on-demand` | Search, then re-poll sources if the retention window has pruned the story. |
| POST | `/api/prune` | Force a retention prune. Requires secret. |

### Search resurrection

Articles are pruned 48 hours after publication to keep the table light.
That would make a search for a three-day-old story return nothing, so
`/api/scrape-on-demand` closes the loop: if the live database has fewer
than three matches, it re-polls the enabled sources, keeps entries
matching the query, re-inserts them flagged `resurrected_at`, and
returns everything together.

It is rate limited per query per hour, capped for concurrency, and every
request is recorded in `scrape_requests`.

---

## How deduplication works

Exact URL and exact title matching cannot collapse a wire story — ten
outlets filing the same cabinet reshuffle produce ten different URLs and
ten differently-worded headlines. So the pipeline escalates:

1. **Canonical URL** — the same link
2. **Content hash** — the same story, same words
3. **Token fingerprint** — order-independent set of meaningful words
4. **Fuzzy similarity** — `rapidfuzz` over candidates narrowed by a
   Postgres trigram pre-filter

Measured on real Nepali wire headlines, step 4 scores a syndicated pair
at **78.7–83.0** and an unrelated pair at **43.3**, so the threshold
sits at 78.

`partial_ratio` is deliberately **not** used. It scores two clearly
different Nepali sentences at 75.8 because they share topic vocabulary,
which both merged unrelated stories and collapsed a 35-sentence article
into a single summary point.

The first version seen wins the article row. Later copies are recorded in
`article_sources` with `is_primary = false` and counted, so a story
carried by four outlets renders once and says so.

---

## How summaries work

Every card shows four points. Every point is a **verbatim sentence from
the article**, chosen by TF-IDF scoring with a position prior, deduped
only against near-identical repeats.

This is extractive, not generative, on purpose. Asking a language model
to "summarise this news" produces a plausible-sounding sentence that no
outlet published, and a portal showing invented text under a real
outlet's name is worse than one showing nothing.

Gemini is available but optional and off by default:

- `ENABLE_GEMINI_SUMMARY` — may write the stored excerpt. Its failure can
  never block or reject an article.
- `ENABLE_GEMINI_RANKING` — may reorder the four already-extracted
  points. Anything not already in the points is discarded before
  returning.

Neither is required. The four-point summaries work without any API key.

---

## Sources

Configured as rows in the `sources` table, not in code. Migration
`0008_seed_nepali_sources.sql` seeds 27 outlets, and
`0021_expanded_sources.sql` adds fifteen more, re-points four and
disables twelve that are verifiably dead.

**Every outlet, with the URL that works or the exact reason it does
not, is in `apps/ingestion/docs/outlet-expansion.md`.** Seventy were
measured against the live web to write it. The short version of what
was found:

- seven domains do not resolve, and four refuse TCP — one range of
  addresses that looks like a block or an outage rather than four
  separate breakages, and worth re-testing from a Nepali network;
- three serve a GoDaddy parking lander and one redirects to
  HugeDomains, so the pipeline was filing a domain for sale as news;
- `news24nepal.tv` has `Disallow: /` in its robots.txt and so is not
  addable at all;
- two seed rows had been disabled for the wrong reason — Himal Khabar
  and Ekantipur were called "client-rendered", which is true of their
  listings and false of their feeds, and Ekantipur's robots.txt contains
  no directive at all. Both work now.

| Adapter | What it is for |
|---|---|
| `generic_rss` | A WordPress or RSS feed. The page size is sent anyway (`RSS_PAGE_SIZE`), and paging is the mechanism that actually delivers more (`RSS_FEED_PAGES`, off by default because every extra page is an extra request to a publisher). Only known-WordPress hosts get any parameter, and the host list was built by reading each feed's `<generator>` tag rather than guessed. |
| `html_listing` | A listing page with no feed. It needs a `selector_config`, and the selectors are the whole of the contract. |

Adding an outlet is an `INSERT`, not a deploy:

```sql
insert into sources (name, base_url, feed_url, adapter_key, enabled, allowed, poll_interval_seconds, sector)
values ('Some Outlet', 'https://example.com', 'https://example.com/feed', 'generic_rss', true, true, 600, 'national');
```

For an outlet with no feed, set `adapter_key = 'html_listing'` and give
it `fallback_url` plus a `selector_config`:

```json
{"item": "article, .post", "link": "a[href]", "exclude": "/author|/tag/|\\.(jpg|png)$"}
```

`allowed = false` means the worker will not fetch it at all. Every fetch
is additionally gated by a robots.txt check and a per-host rate limiter,
so enabling an outlet cannot make the worker hammer a site.

---

## The admin panel

`/admin` is a dashboard, not a page that links to one. It opens on
`/login` when there is no valid session, and the sign-in is a real one:
PBKDF2-SHA256 at 210,000 iterations for the password, an HMAC-signed
session cookie, a rate limit on failed attempts that lives in the
database, and an origin check on every request that changes something.

| Route | What it is |
|---|---|
| `/admin` | The dashboard: 12 stat cards, hourly and daily charts, the event mix, the per-outlet table, the categories, the most-read stories, and the recent errors |
| `/login` | The sign-in form |
| `/403` | Signed in, and the answer is still no |
| `/api/admin/login` | POST. Verifies the password, writes the session row, issues the cookie |
| `/api/admin/logout` | POST. Deletes the session row and clears the cookie |
| `/api/admin/sync` | POST. Triggers the ingestion service. The cron secret, a Supabase admin, or a signed-in operator |

**It is a second door, not a replacement.** The Supabase Auth path in
`middleware.ts` is untouched and still works: a user with an `admin` or
`super_admin` row in `user_roles` gets in exactly as before. This door
exists for the operator who should not have to open a Supabase dashboard
to use their own site.

### The sign-in, and what it refuses

**The password is never compared in a way that leaks.** The stored hash
is PBKDF2-HMAC-SHA256 at 210,000 iterations with a 16-byte salt, and the
comparison is constant-time. An email address that belongs to no
account is verified against a hash of a password nobody kept, so it
costs exactly the same and answers exactly the same as a wrong
password. There is no way to ask the form "does this address exist?".

**The guess is slowed down, and the counters survive a restart.** Two
counters, both in the database (`admin_login_attempts`, migration
0031): one for the email address that was typed, one for the IP the
request came from. After five failures against an address, each further
attempt waits longer — one second, two, four, eight, and eight from
then on. After twenty failures from an address, that address is
blocked outright for the length of the window.

The account limit is a **delay and not a lockout**, and that is
deliberate: the operator's email address is published in this
repository, so a hard lockout on it is a denial of service that anybody
could trigger on purpose, over and over. A delay slows a guesser down
and never stops the person who knows the password from signing in.

The counter is keyed on the address that was **typed**, not on the
accounts that exist, so an address nobody owns is counted exactly like
one that does. That is what keeps the delay from being an oracle.

**A request from another site is refused.** `/api/admin/login`,
`/api/admin/logout` and `/api/admin/sync` all check where the request
came from — `Sec-Fetch-Site`, then `Origin`, then `Referer` — and
answer 403 to anything that is not this site. A page on another site
cannot make the operator's browser sign in, sign out, or trigger a
sync. The check runs before the rate limit, so a cross-site attempt
cannot spend the operator's budget either.

**The cookie is a claim; the row is the fact.** The cookie is
`httpOnly`, `SameSite=Lax`, `Secure` in production, and signed with
HMAC-SHA256 over its payload, so a tampered expiry or a swapped user id
is detected. The expiry is enforced on the server, not merely decoded.
Deleting the row in `admin_sessions` ends the session at once — that is
what "sign out everywhere" is — and every route that trusts the cookie
checks the row through `lib/admin-session.ts`, which fails closed: if
the row cannot be read, the answer is no.

**The signing key can be replaced.** `ADMIN_SESSION_SECRET` signs the
cookie. `ADMIN_SESSION_SECRET_PREVIOUS` is accepted when a cookie is
*read* and never when one is written, so the key can be rotated
without signing everybody out at once: put the new key in
`ADMIN_SESSION_SECRET`, move the old one to
`ADMIN_SESSION_SECRET_PREVIOUS`, and take the old one away when the
last session signed with it has expired. A secret shorter than 32
characters is refused rather than used — the sign-in says so instead of
signing cookies with a key somebody could guess.

**The hash gets a salt of its own.** The seeded account's hash has a
salt that is published in this repository, because it had to be written
down for the operator to use it. The first time an account signs in
after migration 0031, its hash is rewritten with a salt generated for
that one account, and the password is not changed. Any account can have
the same treatment by having its hash rewritten.

### The cards are live, and the filters are honest

The twelve stat cards are the **health indicators** — all time, today,
this week — and they are never narrowed by a filter. Narrowing them
would turn "is the site working?" into "does this one source have
stories?".

The tables below them are the working view, and the filters apply to
those. A date range that ends before it starts, or that starts in the
future, is **ignored** rather than applied, and the panel says so: a
range that matches nothing is a range that was never asked for, and
reporting it as "0 articles" says something false about the site.

The panel does not move under the operator's hands. The header's
fifteen-second refresh skips `/admin`, because a dashboard that
redraws its charts and shifts its rows every fifteen seconds cannot be
read.

**Changing the password.** The seeded account's hash is in migration
0017, and a hash in a public repository is a published credential.
**The password that goes with it was in the repository too** — it was
pinned in `apps/web/tests/admin-auth.spec.ts`, so that the test could
prove the migration's hash and the verifier agree. That line has been
removed, because a live credential written in a published repository is
not a credential. The account needs a new password, and only the
operator can choose one:

```bash
cd apps/web
node scripts/hash-admin-password.mjs "the new password"
```

It prints the hash and the SQL to store it. It prints the SQL rather
than running it, because it has no database access on purpose.

The hash it prints is salted at random, so the same password never
produces the same hash twice, and the account's stored hash is given a
salt of its own the first time it signs in after migration 0031.

**Do this before anything else.** Until the seeded password is
replaced, the published hash in migration 0017 is a credential that
anybody with this repository can use.

### The outlets, and which of them are actually dead

`apps/ingestion/docs/outlet-diagnosis.md` records what was found when
every failing outlet was fetched and checked by hand, with robots.txt
read first. Most of it is not fixable from the database, and saying so
is the useful part:

- **24 News** and **MyReplica** are parked domains on GoDaddy, and
  **Voice of Nepal** is for sale at HugeDomains. There is nothing to
  wait out.
- Seven outlets (Annapurna Post, Arthikabhojan, Bhabishaanepal, Nepal
  Kantha, Nepalbhas, Newahale, Prasuto Journal) do not resolve at all.
- **Ministry of Foreign Affairs** does not answer on port 80 or 443.
- **Himal Khabar** is a client-rendered shell with no article links in
  the served HTML.
- **Sudarshan**'s domain now hosts a Hindi-language Indian outlet, so
  a fixed selector would fetch the wrong country.
- **The Himalayan Times** was a genuine selector fault and is fixed.

Migration `0020` carries the working selector changes; the rest is in
it commented out, with the reasoning, because enabling and disabling an
outlet is the operator's decision and not a code change.

Two adapter bugs were found on the way, and both of them produced
errors that looked exactly like a bad selector:

- `date` and `image` are documented as **optional** keys in
  `selector_config`, and both were fatal when absent — the guard read
  `config.get("date", "time")`, which returns `"time"` when the key is
  *missing*, so the next line raised `KeyError` on the first card.
- An article page had to carry an `<h1>` to be believed to be an
  article. An outlet whose pages carry the headline in an `<h2>` or
  only in the Open Graph metadata was rejected in its entirety. The
  check is "does this page name the story", and there are three ways
  to answer that, so all three are accepted.

### What it needs

Migrations `0017_admin_users.sql` and `0031_admin_login_attempts.sql`,
applied:

```bash
python apps/ingestion/.venv/Scripts/python.exe scripts/supabase_admin.py apply
```

The migration seeds one account — `thapakarlosh631@gmail.com` — with a
PBKDF2 hash. **Change the password.** A seeded credential in a public
repository is a published credential; the hash is safe to publish and
the password is not. The password that went with the seeded hash was
pinned in the test suite, and that line has been removed — but the
hash is still published, so the account needs a new password today:

```bash
cd apps/web
node scripts/hash-admin-password.mjs "a long password of your own"
```

and the SQL it prints run against the database.

The session cookie is signed with a secret, and the secret has to be the
same on every request — a Vercel function can be a different instance on
the next request, and a key generated per process would invalidate every
session on every cold start. The order of preference:

1. `ADMIN_SESSION_SECRET` — the right answer, and at least 32 characters
2. `SUPABASE_SERVICE_ROLE_KEY` — already in the deployment
3. `INGESTION_SECRET` — also already in the deployment

The fallback exists so the panel works the moment it is deployed. Set
`ADMIN_SESSION_SECRET` if you would rather it were not derived from a
key that has another job.

**Rotating it later.** Put the new key in `ADMIN_SESSION_SECRET` and
the old one in `ADMIN_SESSION_SECRET_PREVIOUS`. Cookies signed with
either are accepted; cookies are only ever signed with the new one.
Take the old key away once the last session signed with it has
expired — eight hours is the life of a session.

### The reels health panel

The reels were not updating, and the reason was invisible: Pexels,
Pixabay and YouTube are implemented, and all three were being **skipped**
because their API keys are not set. Nothing said so — a provider with no
credential is logged at INFO, because "we have no key" is not an error.

Migration `0018_reels_source_health.sql` records, per provider, whether
it is enabled, **which environment variable is missing**, how many keys
there are, and how the last run went. The dashboard shows it. The row
that matters is the one with `enabled = false`, because it carries the
exact name to set.

| Variable | Provider |
|---|---|
| `PEXELS_API_KEYS` | Pexels — stock video, comma-separated keys |
| `PIXABAY_API_KEYS` | Pixabay — stock video, comma-separated keys |
| `YOUTUBE_API_KEYS` | YouTube — comma-separated API keys |

Wikimedia Commons needs no key and always runs.

**The reels were standing still, and it was the search.** The worker
logged a healthy run, the provider said it had stored clips, and the page
showed the same twelve it had shown for days. Commons was asked for the
*newest* uploads first and the provider asked for exactly **one page** of
them — so every pass asked for the same twenty-five newest files for
every term, got the same twenty-five back, found them already stored, and
stored nothing.

The search now walks further down each term's results, and the walk
**rotates**: the newest page is always taken, because that is where a
new upload appears, and a band further down is taken as well, and the
band moves with the hour. So a pass covers the whole catalogue over the
course of a day without any one pass having to walk all of it.

**The feed rotates too.** The page shows the whole catalogue rather than
a page of it, and when the reader reaches the last clip the feed goes
back to the first — a page that says "you have reached the end" is an
invitation to leave.

**A ceiling worth knowing about.** Commons holds a few hundred videos on
Nepal subjects, and **none of them are H.264** — the Wikimedia derivative
ladder is `*.vp9.webm`, `*.mpeg4.mov`, `*.theora.ogv`, so nothing in that
catalogue plays on iOS. A hundred thousand reels is not reachable from
Commons; it needs Pexels, Pixabay or YouTube, which is what those three
variables are for.

---

## Clicking a headline on the reels page

The two columns that drift down either side of the video feed are the
one place on the site where a headline is the only way from the current
page to a story. Clicking one used to navigate away, which threw the
reader out of the video.

It now opens **in the column it was clicked in** — not in a panel over
the middle of the screen, which covered the video and sat between the
two columns the story belongs to. And only that column stops moving: the
other one keeps drifting and the reel keeps playing, so the page carries
on around the story being read.

The dialog is a real one. The focus moves into it, is trapped inside it,
and returns to the card that opened it. Escape and the backdrop close
it. The background is taken out of the accessibility tree while it is
open, and the card stays a real link, so "open in a new tab" and "copy
link" still work.

---

## What is on the page, and why

### Newest first means newest NEWS

The feed is ordered by `published_at` — when the outlet **published** the
story — with `ingested_at` (when we fetched it) as the tiebreaker.

This used to be the other way round: the order was by arrival, on the
argument that a publisher who sits on a story for hours makes it new to
this site when it finally appears. The argument is true, and it is also
the wrong thing to show somebody who opened a news site. The top of the
homepage was a story published in **June**, above the news of the day,
with two more from three days earlier above that — because we had
re-fetched them.

A feed whose first cards are four months old reads as broken, and the
reader was right. So the newest news is on top, and within one
publication moment the most recently fetched copy wins — which is where
the better picture and the higher outlet count come from.

The re-fetch was lying too. A story pulled back out of the database by a
search — a "resurrected" one — was stamped with the *current* time as
its arrival, so every re-fetched old story claimed to be brand new and
jumped to the top. A resurrected story now keeps its publication time as
its arrival time; the re-fetch is still recorded, in `resurrected_at`
and `last_seen_at`.

The keyset cursor addresses `published_at` for the same reason as the
ordering: a cursor that addresses a different ordering than the query
runs puts the page boundary in the wrong place, and the reader loses
stories with no error and no empty page — just rows that were never
shown. `lib/feed-cursor.ts` holds the order and the cursor together,
because they are one decision.

### How fast the pages are

Every public route used to render from the database on the request,
and the database is in another continent from the server that renders
it. Measured against the live site, that was the whole of the
Performance score: **1.9 seconds of time-to-first-byte on the
homepage and 2.9 on a category page**, all of it spent waiting for
PostgREST before a single byte of HTML was produced.

Nothing a page does afterwards can be fast enough to matter while the
browser is still waiting for the first byte. So:

- the public reads are cached with an **explicit** revalidate of sixty
  seconds, and the homepage is a static route regenerated in the
  background rather than a `force-dynamic` one. The revalidate is
  explicit on purpose: the implicit form — a plain GET with no cache
  option — is cached for ever on Next 14, and the old code had that
  bug, so the homepage was pinned to the first thing it ever fetched;
- the client-side poll on top of it merges in anything newer the
  moment the worker publishes it, so the page a reader is looking at
  still moves.

Measured after, on the live site:

| | TTFB | LCP | CLS | TBT |
|---|---|---|---|---|
| homepage | 57ms | 510ms | 0.000 | 65ms |
| a category page | 349ms | 655ms | 0.000 | 65ms |
| the Rashifal page | 548ms | 847ms | 0.000 | 45ms |
| the reels page | 636ms | 1288ms | 0.000 | 23ms |

**And the feed no longer moves under the reader.** A card with
nothing to show used to be swapped for the next story by an effect,
which runs after the first paint — so the card was in the page, the
reader had seen it, and it was then taken away, and every card below
it slid up into the gap. That was a Cumulative Layout Shift of 0.07
on the homepage and 0.21 on a category page. Whether a card will show
nothing is a fact about the **story**, and the story is in hand
before anything is rendered, so the feed now leaves those stories out
of the very first paint.

### Every page is endless

The category pages used to render twenty-four cards and stop. They were
the only pages on the site that ended, so a reader who wanted the rest
of a category had nowhere to go, and the newest story in a busy
category could sit below the fold with nothing to pull it up. They use
the homepage's live feed now: newest first, growing as the reader
scrolls, never leaving the category, and never showing a story twice.

The loader starts a page 1200px ahead of the viewport rather than
800px, and a page is thirty cards rather than twenty-four, so a reader
scrolling fast through a category does not outrun the loader and watch
it catch up.

### The almanac, and the days ahead of it

`/category/rashifal` carries the Nepali almanac: today's date in
Gregorian and in Bikram Sambat, the tithi, the nakshatra, the yoga, the
karana, the paksha, sunrise and sunset, the auspicious and inauspicious
windows of the day — and the twelve horoscopes, one for each sign.

**The values are fetched and stored, never computed on the page.**
Getting the Bikram Sambat calendar right needs the month lengths for
every year, and a wrong table is worse than no date — a site that says
"५ माघ २०८३" on the wrong day is not a site that says nothing. The
source publishes the panchang as the Nepal Panchanga Nirnayak Samiti
computes it, and the site is a copy of one day of it.

**The cron fetches it, and it asks the database first.** The pass runs
from the five-minute GitHub Actions cron as well as from the long-lived
worker, because the long-lived worker is a service that sleeps and the
cron is not. When today's row is there and less than an hour old, the
pass costs one query and no requests at all — without that check the
cron would re-download the same large page three hundred times a day.

**The days ahead are kept too.** "Today's tithi" is one question and
"tomorrow's tithi" is another, and only the first of them had a row.
Each per-day page links to the day after it, and that link is followed:
it is the site's own conversion of one day into the next, which is worth
a great deal, because the Bikram Sambat month is 29 to 32 days long and
cannot be reached by adding seven to today's date. The days ahead bring
their own horoscopes with them.

The page offers the days it has as a row of buttons, today first, and
the day is in the URL — so a reader can send somebody tomorrow's
horoscope and they will see the same day.

**The date is Nepal's date, not the server's.** Kathmandu is UTC+05:45,
so at 18:00 UTC it is already the next day there, and an almanac row
keyed on the server's day is a row nobody will ever read.

### A picture that belongs to the story

A story could show a photograph that belonged to a *different* story —
a football image on a story about drugs being seized. Three causes, all
fixed:

- the RSS feed's picture was treated as authoritative over the story's
  own page, so a syndicated story kept the publisher's thumbnail instead
  of the photograph on the article;
- the fallback that fills in a missing picture walked backwards,
  preferring the feed's image over the page's;
- three classes of junk URL were being accepted as photographs — theme
  assets, "copy" duplicates, and GIFs left over from an earlier site.

The page is now always fetched for a new story, and where the picture
came from is recorded on the row (`image_verified_at`), so the dashboard
can tell a picture verified against the article from one that was not.

**AND AN UNCONFIRMED PICTURE IS NOT SHOWN AT ALL.** That is the
whole rule, and it is enforced at three points:

- the pipeline will not STORE a picture it could not confirm against
  the story's own page. The candidate list is the page's images plus
  the publisher's feed thumbnail, and the feed thumbnail is the
  publisher's choice about a slot in a feed — regularly a promoted or
  related story's photograph. Measured on production: a story about
  PHP security was illustrated with a black-and-white photograph of
  an elephant, because the "Most Read from World" thumbnail that
  Kathmandu Post puts on every one of its article pages was the first
  picture the page scan found;
- the repair pass re-resolves a row's picture from the story's own
  page, newest first, and stamps the marker so the row is never
  fetched again;
- the web reads the marker and does not render a picture without one.

**The backlog of unconfirmed rows is worked through by a script**, not
by the poll. The poll re-resolves five rows a poll per source, which
is right for a steady state and far too slow for a few hundred rows
left behind by an older build. `apps/ingestion/scripts/refresh_images.py`
walks them newest first, and the **Refresh images** workflow runs it —
on a schedule, or from the button.

### A story with no picture

A card with no picture used to show a grey panel that said "No image
available" — honest, and useless, because the reader scrolled to a
headline and got told nothing about the story.

The summarizer already lifted four sentences out of the outlet's own
article and stored them on the row, verbatim, in the outlet's own words.
Those sentences **are** the story, in miniature, so a card with no
photograph now shows the first two of them where the photograph would
be, under a label that says what they are.

This is the operator's brief, and it is the right one: a story with no
picture is fine, a story with a picture that belongs to something else
is not, and a story with its own key points is better than either. A
broken picture and a missing one are the same thing to a reader, so they
are the same thing to the card.

The consequence runs through the feed: a story with key points is not
"imageless", so the passes that remove empty cards keep it. Only a story
with no picture, no video **and** no points is swapped out.

### The same photograph twice

The same photo served at two sizes is two URLs and one filename stem, so
the feed catches it by **stem**, not by full URL.

The second card is **dropped**, not merely stripped of its picture. A
duplicate is almost never a different story: it is the same wire copy,
or the same outlet twice, and the headline is usually mangled by
whatever the listing did to it — so the reader was seeing the same story
again under a title that read *"SPORTS Nepal face Oman in crucial World
Cup Lea... Nepal face Oman in crucial World Cup Lea..."*. A blanked card
is not neutral either: its headline says one thing and its picture slot
says another.

The dropped card's slot is filled by the next story down, which is why
every caller over-fetches before calling the pass. The floor is not
negotiable: a page of one photograph returns one card rather than an
empty page.

---

## Deployment

### The one thing you must set up

**Vercel cannot run the scraper.** It runs serverless functions that are
killed after ~10 seconds of CPU, and ingestion needs to run 24/7. If you
deploy only the frontend, the database drains after the 48-hour TTL and
the site goes blank.

Two ways to fix it. Pick one.

#### Option A — GitHub Actions (nothing else to host)

A workflow in this repo runs the scraper every five minutes. Add four
secrets once:

**Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
|---|---|
| `SUPABASE_URL` | `https://<project-ref>.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | from Supabase → Project Settings → API |
| `INGESTION_SECRET` | any random string |
| `CRON_SECRET` | any random string |

Nothing else to configure. After that, the site keeps itself current
forever without you touching it.

**The three workflows, and what each is for.**

| Workflow | When | What it does |
|---|---|---|
| `ingest.yml` | every 5 minutes | One full pass over every outlet: fetch, deduplicate, store, and the artwork repair passes |
| `almanac.yml` | 01:00 Nepal time (19:15 UTC the day before) | The day's almanac and the twelve horoscopes, and the week ahead of them |
| `refresh-images.yml` | nightly, and on a button | Works through the rows whose picture has never been confirmed against their own page |

GitHub's own scheduling is not precise — a scheduled workflow can run
minutes or hours late, and on a busy repository much later — so the
long-lived worker ALSO does the almanac, hourly, and whichever lands
first wins. The store is an upsert on the Gregorian day, so the second
run refreshes the row instead of adding a second one.

#### Option B — a real background service (lower latency)

This is the option to pick for the tightest freshness. The worker has no
scheduler floor — it polls every source on its own interval and re-reads
the sources table every 5 minutes.

| Piece | Where | Command |
|---|---|---|
| `apps/web` | Vercel, root directory `apps/web` | — |
| `apps/ingestion` API | Render / Fly / Railway | `uvicorn app.main:app` with `SCHEDULER_ENABLED=false` |
| `apps/ingestion` worker | Render / Fly / Railway, **separate service** | `python -m app.worker` |

The worker must be a **single replica**. Two replicas means two
schedulers polling the same 27 outlets concurrently.

### The thing to check first: is anything else polling?

Two schedulers over one set of outlets is not a redundancy, it is a
bug: every outlet is asked twice as often, publishers start refusing
with 403, and the dashboard's last-result-per-outlet becomes a
race between the two.

**The symptom is a dashboard where an outlet flips between Healthy
and Failed with no change in the world.** The worker on a laptop is
the usual culprit — it keeps running after the terminal is closed, it
keeps running after the code has changed (Python loads modules at
import time, so editing the files on disk changes nothing in a
running process), and it stops the moment the machine sleeps.

So, on the machine that has been running the worker:

```bash
# What is polling right now?
tasklist | findstr python

# Stop the ones that are not the service you mean to run.
taskkill /PID <pid> /F
```

And on the hosted worker, make sure the **service type is Background
Worker** and not Web Service. A Web Service binds no port, so Render's
health check cannot find one, sends SIGTERM and restarts it every
fifteen minutes; a Background Worker does not sleep and does not need
the port.

#### The worker must be redeployed when the code changes

A Render service redeploys when you push, if it is set to. Check it
has: the dashboard's "last poll" times are the proof, and an outlet
whose error is `KeyError: 'date'` — a line that no longer exists in
the tree — is a worker running old code.

### Environment

**Vercel** (`apps/web`): `NEXT_PUBLIC_SUPABASE_URL`,
`NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_SITE_URL`, and optionally
`GEMINI_API_KEY`, `INGESTION_SERVICE_URL`, `INGESTION_SECRET`,
`ADMIN_SESSION_SECRET` (and `ADMIN_SESSION_SECRET_PREVIOUS`, while a
key is being rotated).

`SUPABASE_SERVICE_ROLE_KEY` is also read on the server, by the admin
panel only — it is never sent to the browser. The admin panel needs a
service-role key to read `admin_users` and `admin_sessions` at all.

**Ingestion**: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
`INGESTION_SECRET`, `CRON_SECRET`, and optionally
`PEXELS_API_KEYS` / `PIXABAY_API_KEYS` / `YOUTUBE_API_KEYS` for the
reels feed. Everything else has a working default — see
`apps/ingestion/.env.example`.

---

## Tests

```bash
cd apps/ingestion
.venv/Scripts/python.exe -m pytest            # 551 tests

cd apps/web
npx playwright test                            # 143 tests
```

The ingestion suite covers URL normalisation (including the srcset and
mixed-content bugs), the Devanagari slug path, fuzzy syndication
thresholds, the `partial_ratio` regression guard, sentence splitting, the
media resolution rules, and the guarantee that every summary point is a
verbatim sentence from the source article.

The web suite is unit tests over pure functions — no browser, no server,
no network, about a second in total. It covers the duplicate-suppression
rules, the picture rules, the image-host allowlist, the feed's merge and
its ordering, the keyset cursor, the admin sign-in, the reels drift and
the story dialog, and the date and weather in the header.

CI runs both plus a production build and an import check of every module
on every push.

---

## Notes on honest limitations

- **Eight sources fail DNS from some networks** (24 News, MyReplica,
  Annapurna Post, Nepal Kantha, Newahale, Nepalbhas, Bhabishaanepal,
  Prasuto Journal). They back off and retry automatically. If they
  resolve from your deployment host they will work there unchanged.
- **The language toggle switches between Nepali and English editions of
  the same outlets**, which are both ingested. It is not a translation.
  Machine-translating a headline rewrites the outlet's words, which is
  the fabrication failure this project is built to avoid.
- **The homepage polls every 30 seconds**, not every second. Outlets
  publish on a 3–30 minute cadence, so a 1-second poll would be ~2,900
  requests an hour per open tab to surface the same few stories a few
  seconds sooner.
- **Article bodies are stored only where a publisher licenses it.**
  `republish_permission` is false for every seeded outlet, so out of the
  box we publish a headline, an image, an attribution link and an
  extractive summary, and link out for the rest. The licence gate
  existed from the start but nothing ever *populated* the field it
  guarded, so flipping it changed nothing; `app/sources/body.py` is the
  producer, it is off by default (`ENRICH_BODY`), and it stores plain
  text only — never a publisher's markup. `FETCH_FULL_TEXT` is a
  separate opt-in because it costs a page request per article, which
  across 27 publishers is a crawl rather than a poll.