-- =============================================================
-- 0025_daily_rashifal.sql
-- The twelve-sign rashifal: today's horoscope for each sign.
--
-- WHAT THIS IS
-- ------------
-- The site calls the page "राशिफल" and it is the one the operator
-- asked for: the twelve horoscopes, मेष (Aries) through मीन
-- (Pisces), each with a few lines of advice.
--
-- It is a DIFFERENT THING from the panchang in 0022 and it lives in
-- a different table on purpose:
--
--   daily_panchang   - one row per DAY. The almanac: the Bikram
--                      Sambat date, the tithi, the nakshatra, the
--                      sunrise and sunset, the auspicious hours.
--   daily_rashifal   - twelve rows per DAY. The horoscopes: one row
--                      per sign.
--
-- The grains are different, so the tables are different. A sign is a
-- thing with its own name, its own emoji, its own advice, its own
-- lucky colour and its own lucky number, and a reader who wants
-- "today's Scorpio" is asking a different question from the one who
-- wants "today's tithi". Putting the twelve signs in a jsonb column
-- on daily_panchang would have made the second question a JSON path
-- expression and the first one a table scan of a blob.
--
-- WHERE THE VALUES COME FROM
-- --------------------------
-- hamropatro.com/rashifal, whose robots.txt allows the path. The
-- twelve signs are published daily and do not change within the day.
--
-- ONLY WHAT THE SOURCE ACTUALLY PUBLISHES GETS A COLUMN
-- -------------------------------------------------------
-- The source publishes, per sign: the name (in Nepali and English),
-- the emoji, the letter initials, the advice, the lucky colour and
-- the lucky number. It publishes NO English advice (the `descEn`
-- field is empty on both the Nepali and the English page) and NO
-- lucky direction or muhurat. So there is no `advice_en` column
-- and no `lucky_direction` column: a column nobody ever fills is a
-- promise the data does not keep.
--
-- `advice` is the text VERBATIM as published, including its last
-- sentence ("आजको शुभ रंग गुलाबी हो भने शुभ अंक ७ रहेको छ।"),
-- which is where `lucky_colour` and `lucky_number` come from. The
-- advice is stored whole on purpose - if the site ever rewords that
-- sentence and the extraction stops matching, the words are still
-- in the row.
--
-- Idempotent: safe to apply to an existing production database, and
-- safe to apply twice.
-- =============================================================

create table if not exists daily_rashifal (
  -- The Gregorian day this row is FOR. The same day key the
  -- panchang uses, so the two tables join on it.
  panchang_date date not null,

  -- The sign, 1 to 12, in the order the site lists them:
  -- 1 मेष (Aries) ... 6 कन्या (Virgo) ... 12 मीन (Pisces).
  --
  -- The check constraint is deliberate. A parser that counted from
  -- zero, or an off-by-one in the loop, would write 0 or 13 and the
  -- constraint would say so instead of a reader seeing eleven
  -- horoscopes and a gap. If the zodiac ever grows a thirteenth
  -- sign, this is the line to relax.
  sign smallint not null check (sign between 1 and 12),

  -- The source's own name for the sign ('mesh', 'brish', ...).
  -- Stable, and the same slug the site's own pages use.
  sign_slug text not null,

  -- The sign's name, in English and in Nepali.
  sign_name text,
  sign_name_ne text,

  -- The zodiac symbol, and the letter initials the site prints
  -- under the name ('चु, चे, चो, ...').
  sign_emoji text,
  initials text,

  -- Today's advice for this sign, verbatim.
  advice text,

  -- The lucky colour and the lucky number, taken from the last
  -- sentence of the advice. Nullable: a wording the parser does not
  -- recognise leaves them empty rather than guessing.
  lucky_colour text,
  lucky_number text,

  -- Where the row came from, and when we last confirmed it.
  source_url text not null,
  fetched_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  -- One row per sign per day. Re-running the day's fetch is an
  -- UPSERT on this key, so a second pass refreshes the twelve rows
  -- instead of adding a second set of them.
  primary key (panchang_date, sign)
);

-- One sign over time: "every Scorpio reading the site has
-- published". The primary key serves "the twelve signs of one day",
-- so this is the other direction.
create index if not exists daily_rashifal_sign_idx
  on daily_rashifal (sign, panchang_date desc);

-- ---------------------------------------------------------
-- updated_at
--
-- The same generic trigger every other table in this schema uses.
-- Postgres has no `create trigger if not exists`, so the trigger is
-- dropped first: re-applying this migration must not fail.
-- ---------------------------------------------------------

drop trigger if exists trg_daily_rashifal_updated_at on daily_rashifal;
create trigger trg_daily_rashifal_updated_at before update on daily_rashifal
  for each row execute function set_updated_at();

-- ---------------------------------------------------------
-- ROW LEVEL SECURITY
--
-- Read-only for the anon key, exactly like daily_panchang: the site
-- only ever READS the horoscopes. Writes go through the service-role
-- client in the ingestion service, which bypasses RLS.
-- ---------------------------------------------------------

alter table daily_rashifal enable row level security;

drop policy if exists "daily_rashifal_public_select" on daily_rashifal;
create policy "daily_rashifal_public_select" on daily_rashifal
  for select using (true);

drop policy if exists "daily_rashifal_service_write" on daily_rashifal;
create policy "daily_rashifal_service_write" on daily_rashifal
  for all using (false) with check (false);
