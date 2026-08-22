# Nepal News & Magazine — Stage 1 Scaffold

Production-shaped scaffold for a Nepal-focused news/magazine SaaS: Next.js
frontend, Supabase (Postgres/Auth/Storage), and a Python ingestion service.
See `ARCHITECTURE.md` for the system diagram and what's real vs. stubbed.

This is **Stage 1** of the build (architecture → schema → auth/RLS →
ingestion framework → minimal frontend), matching the execution order the
original spec requested. It is not the full 21-stage deliverable — see
"Next stages" below for exactly what's left.

## Local development

### 1. Supabase

```bash
npx supabase init          # if not already a supabase project
npx supabase start         # local Postgres + Auth + Storage via Docker
npx supabase db reset      # applies supabase/migrations/*.sql, then seed/seed.sql
```

Or against a hosted project: create one at supabase.com, then

```bash
npx supabase link --project-ref <your-project-ref>
npx supabase db push
```

Generate real TS types once linked (replaces the hand-authored starter in
`apps/web/types/database.ts`):

```bash
npx supabase gen types typescript --project-id <project-id> > apps/web/types/database.ts
```

### 2. Frontend (`apps/web`)

```bash
cd apps/web
cp .env.example .env.local   # fill in Supabase URL/anon key, etc.
npm install
npm run dev                  # http://localhost:3000
```

### 3. Ingestion service (`apps/ingestion`)

```bash
cd apps/ingestion
cp .env.example .env         # fill in SUPABASE_URL + SERVICE_ROLE key
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload   # http://localhost:8000/api/health
```

Or via Docker: `docker compose up --build` from the repo root.

### Adding a real source

Sources are configured in the database, not in code:

```sql
insert into sources (name, base_url, feed_url, adapter_key, poll_interval_seconds)
values ('Some Outlet', 'https://example.com', 'https://example.com/rss', 'generic_rss', 900);
```

Leave `enabled` and `allowed` `false` until you have personally confirmed
that outlet's `robots.txt` and Terms of Service permit automated feed
ingestion — flipping those two flags is the only thing that turns
fetching on for that source.

## Deployment

### Supabase (production)
Create a project → run migrations (`supabase db push` from CI or locally)
→ enable email auth (and Google OAuth if desired) in Authentication
settings → note the project URL, anon key, and service-role key.

### Vercel (`apps/web`)
Import the repo, set the **Root Directory** to `apps/web`, and add env
vars from `apps/web/.env.example`. Only `NEXT_PUBLIC_*` vars are exposed
to the browser — everything else (service-role key, cron secret,
ingestion secret) stays server-only.

### Render (`apps/ingestion`)
New Web Service → Root Directory `apps/ingestion` → Docker runtime (uses
the included `Dockerfile`) → add env vars from
`apps/ingestion/.env.example`. Render's health check should point at
`/api/health`.

### Cron
Point Vercel Cron (or Render's own cron) at
`POST https://<your-app>/api/admin/sync` with header
`Authorization: Bearer <CRON_SECRET>` on whatever cadence you want a
safety-net full sync (the scheduler inside the ingestion service also
runs continuously per-source, so this is a backstop, not the primary
trigger).

## Required environment variables

**apps/web** (`.env.example`): `NEXT_PUBLIC_SUPABASE_URL`,
`NEXT_PUBLIC_SUPABASE_ANON_KEY` (browser-safe), `SUPABASE_SERVICE_ROLE_KEY`,
`CRON_SECRET`, `INGESTION_SERVICE_URL`, `INGESTION_SECRET`, `SMTP_*`
(server-only — never prefixed `NEXT_PUBLIC_`).

**apps/ingestion** (`.env.example`): `SUPABASE_URL`,
`SUPABASE_SERVICE_ROLE_KEY`, `INGESTION_SECRET`, `CRON_SECRET`,
`SCHEDULER_ENABLED`, `LOG_LEVEL`.

Never commit `.env` / `.env.local` — only the `.env.example` files.

## Next stages (not yet built)

Following the spec's own execution order:

- **Auth UI**: login/signup/reset-password pages using Supabase Auth
  (schema + RLS + middleware protection already exist; forms don't yet)
- **Admin CMS UI**: article editor, source manager UI, user manager,
  comment moderation, ad manager (backing tables + RLS exist; no UI yet)
- **Category pages, search page** (Postgres full-text index already
  exists on `articles.search_vector` — needs the `/search` route and
  `/category/[slug]` route wired up the same way `app/page.tsx` is)
- **Comments UI** + rate limiting + profanity/spam filtering on the API route
- **Realtime**: Supabase Realtime subscription on `articles` for
  live homepage updates
- **PWA**: manifest.json, service worker, offline fallback
- **Newsletter/email**: SMTP integration for verification, reset,
  digests, breaking-news alerts
- **Advertisement rendering**: ad-slot components reading from
  `advertisements`/`advertisement_campaigns`
- **Full test suite**: Playwright e2e, RLS/authz tests, security tests
  (SQL injection, XSS, IDOR, rate-limit bypass) per spec section 34
- **Source-specific adapters**: only after manually verifying each
  outlet's robots.txt/ToS and obtaining a real feed URL

Tell me which of these to build next and I'll continue in the same
architecture and file structure.
