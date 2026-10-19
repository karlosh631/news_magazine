-- =========================================================
-- 0013_tighten_poll_intervals.sql
-- Halves the time between polls, so the site reflects news sooner.
--
-- Why
-- ---
-- With every outlet polling no faster than 180 seconds, the slowest
-- possible path from "an outlet publishes" to "it appears on the site"
-- was three minutes plus whatever the reader's browser waited. Measured
-- across the feed, arrivals were already landing every 2-4 minutes, so
-- the interval was the binding constraint rather than the news itself.
--
-- Why this is still polite
-- -----------------------
-- MIN_POLL_SECONDS is 60, and every source carries
-- rate_limit_per_minute = 8. At 90 seconds an outlet is asked for
-- 0.67 requests a minute — comfortably inside what it has already
-- declared it tolerates, and well under what a site sees from its own
-- readers' feeds.
--
-- Halving to 90s cuts worst-case delivery from ~3 minutes to ~90
-- seconds. It does not make it "live", and nothing can: a publisher
-- only puts a story on its feed when it decides to.
--
-- If a source starts returning 429s or slowing down, raise its row
-- back rather than lowering MIN_POLL_SECONDS — the floor exists to
-- stop a misconfiguration turning the crawler into a load test.
-- =========================================================

update sources
   set poll_interval_seconds = case
         -- The largest, most active feeds go first, so they lead.
         when poll_interval_seconds <= 300 then 90
         -- Everything else moves to three minutes.
         else 180
       end
 where enabled
   and allowed;

comment on column sources.poll_interval_seconds is
  'Seconds between polls of this outlet. Floor is MIN_POLL_SECONDS (60). '
  'Lower means fresher and more requests to the publisher; 90-180s is '
  'polite for a news site, sub-minute is a load test.';

-- ---------------------------------------------------------
-- Verify after applying
--
--   select min(poll_interval_seconds), max(poll_interval_seconds)
--     from sources where enabled and allowed;
--
-- Expect 90 / 180.
-- ---------------------------------------------------------