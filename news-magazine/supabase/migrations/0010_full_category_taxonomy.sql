-- =========================================================
-- 0010_full_category_taxonomy.sql
-- Completes the category taxonomy.
--
-- 0005 seeded only six categories, but services/categorizer.py has rules
-- for `health` and `world` as well. Those two rules matched a headline,
-- produced a slug, and then found no matching row in `categories`, so
-- classify() returned None and the article silently fell back to the
-- source's default category (usually national). Health and world
-- coverage was therefore misfiled.
--
-- Also adds the five remaining public categories so the navigation is
-- data-driven rather than limited to the six the header used to
-- hardcode.
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
on conflict (slug) do update set
  name_en = excluded.name_en,
  name_ne = excluded.name_ne,
  sort_order = excluded.sort_order;

-- Backfill any articles that were filed before their category existed.
update articles a
set primary_category_id = c.id
from categories c
where a.primary_category_id is null
  and c.slug = case
    when a.headline ~* '(स्वास्थ्य|अस्पताल|रोग|औषधि|चिकित्सा|health|hospital|disease|medicine|doctor|महामारी)'
      then 'health'
    when a.headline ~* '(अमेरिका|भारत|चीन|बेलायत|रुस|अन्तर्राष्ट्रिय|विश्व|international|world news|america|india|china)'
      then 'world'
    when a.headline ~* '(खेल|क्रिकेट|फुटबल|विश्वकप|sports|cricket|football|खेलाडी)'
      then 'sports'
    when a.headline ~* '(मनोरञ्जन|चलचित्र|फिल्म|सिनेमा|अभिनेता|गायक|entertainment|movie|film|cinema|music)'
      then 'entertainment'
    when a.headline ~* '(प्रविधि|टेक्नोलोजी|सफ्टवेयर|इन्टरनेट|technology|tech news|artificial intelligence|डिजिटल)'
      then 'technology'
    when a.headline ~* '(ब्याज|शेयर|बजार|अर्थतन्त्र|बैंक|bank|stock|market|economy|finance)'
      then 'business'
    when a.headline ~* '(सरकार|प्रधानमन्त्री|संसद|निर्वाचन|राजनीति|government|prime minister|parliament|election|politics)'
      then 'politics'
    else 'national'
  end;