-- =============================================================
-- 0022_daily_panchang.sql
-- Today's Nepali almanac: the Bikram Sambat date, the tithi, the
-- nakshatra, sunrise and sunset, and the windows the panchang
-- calls auspicious and inauspicious.
--
-- WHAT THIS IS
-- ------------
-- The site header shows a line of almanac: today's Bikram Sambat
-- date, the tithi, the nakshatra, sunrise and sunset, and the
-- शुभ (auspicious) and अशुभ (inauspicious) windows of the day.
-- Those are published every morning and they do not change once the
-- day is over.
--
-- So this table is NOT the articles table and does not behave like
-- it. There is ONE row per calendar day, keyed by the Gregorian date
-- the row is FOR, and re-fetching a day UPDATES that row instead of
-- adding a second one. That is what makes the daily job safe to run
-- as often as the operator likes: the second run of the day is a
-- no-op, not a duplicate.
--
-- It is not an article either. It is never deduplicated against
-- articles, never normalised, never summarised. It is an almanac.
--
-- WHERE THE VALUES COME FROM
-- --------------------------
-- hamropatro.com, whose robots.txt allows these paths. The site
-- publishes the panchang as the Nepal Panchanga Nirnayak Samiti
-- computes it, and credits that committee at the foot of the page.
-- The site is the source; this table is a copy of one day of it.
--
-- THE TIME WINDOWS ARE JSONB, NOT ROWS
-- -------------------------------------
-- The auspicious/inauspicious windows are a list of time ranges that
-- only ever belong to the day they are stored against, they are read
-- and written as a whole, and there are about sixteen of them. A
-- second table would be a lot of machinery for a list nobody queries
-- on its own.
--
-- Each window looks like:
--
--   {"khanda": "प्रथम", "period": "day", "from": "06:07",
--    "to": "07:32", "result": "शुभ",
--    "start_minute": 367, "end_minute": 452}
--
-- `start_minute` and `end_minute` are minutes from local midnight.
-- The night windows run PAST midnight, so a night window's
-- `end_minute` can be greater than 1440 — 05:14 the next morning is
-- 1754 — which is what keeps the sequence monotonic and stops the
-- frontend from having to guess which side of midnight a time is on.
-- `from` and `to` are the wall-clock strings for display.
--
-- Idempotent: safe to apply to an existing production database, and
-- safe to apply twice.
-- =============================================================

create table if not exists daily_panchang (
  -- The Gregorian day this row is FOR. Primary key, so re-running
  -- the day's fetch is an UPSERT and never a second row for the same
  -- day.
  panchang_date date primary key,

  -- The Bikram Sambat date, in parts. A BS date is not a number and
  -- cannot be compared as one, and "the fifth of Kartik" is not
  -- "the fifth of the third month" — so the parts are kept, 1-based,
  -- Baishakh = 1 and Chaitra = 12.
  bikram_sambat_year integer not null,
  bikram_sambat_month integer not null,
  bikram_sambat_day integer not null,

  -- The same date as the source writes it, for display:
  -- "कार्तिक ५, २०८३". Never parsed back out of this — the parts
  -- above are the truth.
  bikram_sambat_label text not null,

  -- The weekday, in both languages the source publishes.
  weekday text not null,
  weekday_np text not null,

  -- The tithi. `tithi` is the bare name ("एकादशी") and `tithi_full`
  -- is the whole thing with its lunar month and paksha
  -- ("असोज शुक्ल एकादशी"). The full form names the LUNAR month,
  -- which is routinely the month before the solar one — that is the
  -- panchang being correct, not a bug.
  tithi text not null,
  tithi_full text,

  -- शुक्ल (waxing) or कृष्ण (waning).
  paksha text,

  -- The nakshatra, the yoga and the karana: the other three of the
  -- five limbs of the panchang.
  nakshatra text,
  yoga text,
  karana text,

  -- The moon's phase, as the source names it.
  moon_phase text,

  -- Sunrise and sunset. The wall-clock time as the source prints it
  -- (Nepal time, UTC+05:45) AND the instant it refers to, so a
  -- reader in London can be shown the right moment while the site
  -- still renders "६:०७" in Nepali.
  sunrise time not null,
  sunset time not null,
  sunrise_at timestamptz,
  sunset_at timestamptz,

  -- How long the day is, in minutes. The site prints it
  -- ("११ घण्टा २१ मिनेट"); a number is easier to render.
  day_length_minutes integer,

  -- The windows the panchang calls शुभ and अशुभ. Both default to an
  -- empty list rather than NULL, so a reader never has to check for
  -- null before iterating.
  auspicious_times jsonb not null default '[]'::jsonb,
  inauspicious_times jsonb not null default '[]'::jsonb,

  -- Where the row came from, and when we last confirmed it.
  source_url text not null,
  fetched_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Look a day up by its Bikram Sambat date. The site is addressed by
-- BS date and so is the backfill, so this is the second way in.
--
-- Deliberately NOT unique: a unique index here would turn a mistake in
-- the source's BS conversion into a failed upsert, and a failed
-- upsert loses the day entirely. A duplicate BS row is a visible
-- oddity; a missing almanac is a broken header.
--
-- There is no second index on panchang_date. The primary key IS the
-- date, and Postgres scans a b-tree backwards as happily as forwards,
-- so "the newest row" costs no sort step and no extra index to keep
-- in step with the table.
create index if not exists daily_panchang_bs_idx
  on daily_panchang (bikram_sambat_year, bikram_sambat_month, bikram_sambat_day);

-- ---------------------------------------------------------
-- updated_at
--
-- The same generic trigger every other table in this schema uses.
-- Postgres has no `create trigger if not exists`, so the trigger is
-- dropped first: re-applying this migration must not fail.
-- ---------------------------------------------------------

drop trigger if exists trg_daily_panchang_updated_at on daily_panchang;
create trigger trg_daily_panchang_updated_at before update on daily_panchang
  for each row execute function set_updated_at();

-- ---------------------------------------------------------
-- ROW LEVEL SECURITY
--
-- Read-only for the anon key: the site only ever READS the almanac.
-- The header renders it, and nothing a visitor does should be able
-- to change it. Writes go through the service-role client in the
-- ingestion service, which bypasses RLS exactly as it does for
-- articles.
-- ---------------------------------------------------------

alter table daily_panchang enable row level security;

drop policy if exists "daily_panchang_public_select" on daily_panchang;
create policy "daily_panchang_public_select" on daily_panchang
  for select using (true);

drop policy if exists "daily_panchang_service_write" on daily_panchang;
create policy "daily_panchang_service_write" on daily_panchang
  for all using (false) with check (false);
