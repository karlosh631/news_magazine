-- =========================================================
-- Local taxonomy seed only. No article or source fixtures are inserted.
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

