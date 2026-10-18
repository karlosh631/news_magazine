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

### 2. Ingestion service

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

### 3. Frontend

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
`0008_seed_nepali_sources.sql` seeds 27 outlets.

| Adapter | Outlets |
|---|---|
| `generic_rss` | OnlineKhabar (ne/en), Kantipur/The Kathmandu Post, Ratopati, Setopati, Nepal News (ne/en), Nagarik News, Nagarik Times, Khabarzone, Pradesh Post, BBC Nepali, MOFA |
| `html_listing` | The Himalayan Times, Annapurna Post, 24 News, MyReplica, Newahale, Nepal Kantha, Bhabishaanepal, Arthikabhojan, Voice of Nepal, Nepalbhas, Prasuto Journal, Sudarshan |

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

## Deployment

### The one thing you must set up

**Vercel cannot run the scraper.** It runs serverless functions that are
killed after ~10 seconds of CPU, and ingestion needs to run 24/7. If you
deploy only the frontend, the database drains after the 48-hour TTL and
the site goes blank.

Two ways to fix it. Pick one.

#### Option A — GitHub Actions (nothing else to host)

A workflow in this repo runs the scraper every 20 minutes. Add four
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

#### Option B — a real background service (lower latency)

| Piece | Where | Command |
|---|---|---|
| `apps/web` | Vercel, root directory `apps/web` | — |
| `apps/ingestion` API | Render / Fly / Railway | `uvicorn app.main:app` with `SCHEDULER_ENABLED=false` |
| `apps/ingestion` worker | Render / Fly / Railway, **separate service** | `python -m app.worker` |

The worker must be a **single replica**. Two replicas means two
schedulers polling the same 27 outlets concurrently.

### Environment

**Vercel** (`apps/web`): `NEXT_PUBLIC_SUPABASE_URL`,
`NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_SITE_URL`, and optionally
`GEMINI_API_KEY`, `INGESTION_SERVICE_URL`, `INGESTION_SECRET`.

**Ingestion**: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
`INGESTION_SECRET`, `CRON_SECRET`. Everything else has a working default
— see `apps/ingestion/.env.example`.

---

## Tests

```bash
cd apps/ingestion
.venv/Scripts/python.exe -m pytest
```

44 tests covering URL normalisation (including the srcset and mixed-content
bugs), the Devanagari slug path, fuzzy syndication thresholds, the
`partial_ratio` regression guard, sentence splitting, and the guarantee
that every summary point is a verbatim sentence from the source article.

CI runs these plus a production build and an import check of every module
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
- **Article bodies are never stored.** `republish_permission` is false
  for every seeded outlet; we publish a headline, an image, an
  attribution link and an extractive summary, and link out for the rest.