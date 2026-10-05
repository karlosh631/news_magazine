-- Ensure the navigation categories exist in hosted Supabase projects too.

insert into categories (slug, name_en, name_ne, sort_order) values
  ('national', 'National', 'राष्ट्रिय', 1),
  ('politics', 'Politics', 'राजनीति', 2),
  ('business', 'Business', 'व्यापार', 3),
  ('technology', 'Technology', 'प्रविधि', 4),
  ('sports', 'Sports', 'खेलकुद', 5),
  ('entertainment', 'Entertainment', 'मनोरञ्जन', 6)
on conflict (slug) do nothing;