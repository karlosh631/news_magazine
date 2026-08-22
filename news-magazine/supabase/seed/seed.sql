-- =========================================================
-- DEMO DATA — local development / staging only.
-- Never run against production. Nothing here should ship live.
-- =========================================================

insert into categories (slug, name_en, name_ne, sort_order) values
  ('national', 'National', 'राष्ट्रिय', 1),
  ('politics', 'Politics', 'राजनीति', 2),
  ('business', 'Business', 'व्यापार', 3),
  ('technology', 'Technology', 'प्रविधि', 4),
  ('sports', 'Sports', 'खेलकुद', 5),
  ('entertainment', 'Entertainment', 'मनोरञ्जन', 6),
  ('lifestyle', 'Lifestyle', 'जीवनशैली', 7),
  ('education', 'Education', 'शिक्षा', 8),
  ('health', 'Health', 'स्वास्थ्य', 9),
  ('world', 'World', 'विश्व', 10),
  ('opinion', 'Opinion', 'विचार', 11)
on conflict (slug) do nothing;

insert into authors (slug, display_name, is_staff) values
  ('demo-staff-writer', 'Demo Staff Writer [DEMO DATA]', true)
on conflict (slug) do nothing;

-- A source row with enabled=false / allowed=false — administrators must
-- explicitly verify ToS/robots.txt and flip these before any real sync.
insert into sources (name, base_url, feed_url, adapter_key, enabled, allowed, poll_interval_seconds, attribution_required, republish_permission, notes)
values (
  'Demo RSS Source [DEMO DATA]',
  'https://example.com',
  'https://example.com/feed.xml',
  'generic_rss',
  false,
  false,
  900,
  true,
  false,
  'Placeholder for local dev. Point feed_url at any legitimate RSS feed you have permission to ingest, then verify robots.txt before enabling.'
)
on conflict (name) do nothing;

insert into articles (slug, headline, subtitle, language, status, excerpt, primary_category_id, published_at, ingested_at)
select
  'demo-article-one',
  '[DEMO DATA] Sample headline for local development',
  'This row exists only to exercise the homepage/article-page queries locally.',
  'en',
  'published',
  'This is placeholder excerpt text for local development and is not real news content.',
  c.id,
  now(),
  now()
from categories c where c.slug = 'national'
on conflict (slug) do nothing;
