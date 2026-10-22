-- =============================================================
-- Purge the search-engine spam
--
-- A parked domain served as a news listing, and the pipeline
-- believed it. The result was in the database and in the site's
-- "Most read" panel: a headline asking how to transfer a domain
-- registration to GoDaddy, sitting among stories about Nepal.
--
-- It was stored the same way a real article is, because the pipeline
-- cannot tell the difference: the page had a headline, a link and an
-- image, and it came from a source the operator had approved. The
-- source has since been disabled - its domain is for sale - but the
-- rows it left behind are still here, and they will be served until
-- they are removed.
--
-- WHY A HEADLINE PATTERN AND NOT A SOURCE
-- --------------------------------------
-- The obvious fix is "delete everything from that source", and it is
-- the wrong fix: the source published real Nepali news before it
-- went bad, and those articles are worth keeping. What is not worth
-- keeping is the spam, and the spam is recognisable.
--
-- These headlines are domain-registration and web-hosting marketing.
-- They are not news, they are not about Nepal, and no Nepali outlet
-- has ever run one. The patterns are deliberately narrow: "hosting"
-- on its own appears in real technology coverage, and "domain" in
-- real technology coverage too, so both need the marketing sense
-- around them.
--
-- The deletion CASCADES: article_sources, article_views, media and
-- the other child rows all reference the article with `on delete
-- cascade`, so nothing is left pointing at a row that is gone.
-- =============================================================

-- What will be removed, before anything is.
--
-- A SELECT rather than a blind DELETE, because a blind DELETE on a
-- pattern is a thing you want to see before it happens. The
-- operator can run this on its own, look, and then run the DELETE.
-- The statements are NOT wrapped in a transaction, so the SELECT
-- genuinely shows what is there and the DELETE genuinely removes
-- it.

select
  id,
  headline,
  source_name_snapshot,
  published_at
from articles
where headline ~* 'registrar'
   or headline ~* 'go\s*daddy'
   or headline ~* 'namecheap'
   or headline ~* 'hostinger'
   or headline ~* 'transfer your domain'
   or headline ~* 'domain transfer'
   or headline ~* 'buy a domain'
   or headline ~* 'web hosting'
   or headline ~* 'domain name registrar'
order by published_at desc nulls last;

-- The removal.
delete from articles
where headline ~* 'registrar'
   or headline ~* 'go\s*daddy'
   or headline ~* 'namecheap'
   or headline ~* 'hostinger'
   or headline ~* 'transfer your domain'
   or headline ~* 'domain transfer'
   or headline ~* 'buy a domain'
   or headline ~* 'web hosting'
   or headline ~* 'domain name registrar';
