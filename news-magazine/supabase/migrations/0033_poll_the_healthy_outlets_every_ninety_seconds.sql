-- =========================================================
-- 0033_poll_the_healthy_outlets_every_ninety_seconds.sql
--
-- The owner asked for the freshest feed this system can give: every
-- outlet asked as often as it is safe to ask it, and a pass every
-- three minutes instead of every nine.
--
-- WHAT CHANGED
-- ------------
-- Two knobs, and the second one only counts once the first is in
-- place.
--
-- 1. THE SWEEP NOW HONOURS EACH OUTLET'S OWN INTERVAL.
--
--    `scripts/sync_once.py` used to ask EVERY outlet on EVERY pass.
--    The pass runs every three minutes now, so an outlet that had
--    agreed to be polled every five minutes was being asked every
--    three - which is not an interval anybody agreed to.
--
--    The sweep now asks the question the interval column exists to
--    answer: has this outlet been asked recently enough that asking
--    again would be one request too many? `last_success_at` is written
--    by every poller the moment a poll of that outlet finishes
--    without failing, so the two pollers - the CI pass and the
--    long-lived worker - take turns rather than doubling up.
--
--    A pass that finds every outlet inside its own interval is a
--    GREEN pass, not a failed one. It used to report "no source was
--    successfully synced" and turn the run red, which is how an
--    operator learns to ignore a red run.
--
-- 2. THE INTERVALS THEMSELVES.
--
--    The twenty-five outlets that answer are now asked every ninety
--    seconds - a minute and a half. That is the number migration 0013
--    called polite for a news site, and it is the number the
--    scheduler's own floor has always allowed (MIN_POLL_SECONDS is
--    60, so 90 is above it).
--
--    The five that refuse us are NOT moved. Migration 0032 measured
--    them and gave them 300 seconds, and Ekantipur 900, for reasons
--    that have not changed: they answered a full Chrome request with
--    a 200 from an ordinary client and a 403 from a datacentre
--    address, so the refusal is aimed at the address and not at the
--    request. Asking them MORE often is the one thing that is
--    certain to make it worse. They stay where 0032 put them.
--
--    The nine that answer the CI runner with a 403 (Ekantipur, both
--    Nepal News feeds, both OnlineKhabar feeds, Ratopati, Ujyaalo
--    Online, both Hamro Patro listings) are on the same slow
--    intervals, because a 403 is a 403 however often it is asked.
--
-- WHAT THIS DOES NOT DO
-- ----------------------
-- It does not make the site fetch nine more publishers. It makes the
-- twenty-five that answer be fetched far more often, and it stops
-- the sweep from asking the others more often than was agreed.
--
-- Apply with:  supabase db push
-- =========================================================

update sources
   set poll_interval_seconds = 90
 where enabled
   and allowed
   and name not in (
     -- The five that refuse this worker (migration 0032).
     'Ekantipur',
     'Nepal News',
     'Nepal News English',
     'Ratopati',
     'Ujyaalo Online',
     -- The two Hamro Patro listings, which answer the CI runner with
     -- a 403 and answer the Render worker with a 200. Their interval
     -- is the one the WORKER keeps; the CI pass will find them "not
     -- due" often enough on its own.
     'Hamro Patro News',
     'Hamro Patro National',
     -- The two Online Khabar feeds, refused the same way.
     'OnlineKhabar',
     'OnlineKhabar English'
   );

comment on column sources.poll_interval_seconds is
  'Seconds between polls of this outlet. Floor is MIN_POLL_SECONDS (60). '
  'Migration 0033: 90s for the twenty-five outlets that answer; 300s for '
  'the five that refuse this worker; 900s for Ekantipur, whose poll costs '
  'a page fetch and up to three image probes PER CARD. The CI sweep '
  '(scripts/sync_once.py) honours this column, and so does the worker.';

-- ---------------------------------------------------------
-- Verify after applying
--
--   select name, poll_interval_seconds, rate_limit_per_minute
--     from sources
--    where enabled and allowed
--    order by poll_interval_seconds, name;
--
-- Expect: 90 for the twenty-five that answer; 300 for Nepal News,
-- Nepal News English, Ratopati and Ujyaalo Online; 900 for Ekantipur;
-- 300 for the two Hamro Patro listings and the two Online Khabar feeds.
--
-- And the pass must now say which outlets it left alone:
--
--   grep "not due yet" the run log
--
-- A pass that asks twenty-five outlets and says the other nine are
-- "not due yet" is the sweep working.
-- ---------------------------------------------------------
