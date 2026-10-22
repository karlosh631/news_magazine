-- =============================================================
-- 0028_purge_wrong_images.sql
-- Take away the pictures that belong to a different story.
--
-- WHAT THIS IS
-- ------------
-- A story was shown with a black-and-white photograph of an elephant
-- lifted by an adult elephant's trunk. The story was "What is PHP, and
-- why are security flaws putting European government websites at
-- risk?". The photograph was the "Most Read from World" thumbnail
-- that Kathmandu Post puts at the top of EVERY one of its article
-- pages - the same picture, on every story on the site.
--
-- The URL loaded. It was a real image, of something else entirely, so
-- nothing on the page could tell the reader, and the picture was
-- stored by an ingestion build older than the fix that rejects it.
--
-- THE PICTURE GOES; THE STORY STAYS
-- --------------------------------
-- The operator's rule: a picture must belong to the story it is shown
-- with, and a story with no picture is fine. So the picture is taken
-- away and the headline stays on the page. The ingestion repair pass
-- re-resolves the row from the story's own page shortly afterwards,
-- and the card shows the story's key points in the meantime.
--
-- The statements are NOT wrapped in a transaction, so the SELECT
-- genuinely shows what is there and the UPDATE genuinely removes it.
-- =============================================================

-- What will be touched, before anything is.
--
-- A SELECT rather than a blind UPDATE, because a blind UPDATE on a
-- pattern is a thing you want to see before it happens.

select
  id,
  headline,
  source_name_snapshot,
  published_at,
  featured_image_url
from articles
where status = 'published'
  and duplicate_of is null
  and featured_image_url is not null
  and (
    -- The resizer. A `thumb.php?src=...` URL is a LISTING thumbnail,
    -- never the story's own photograph: it is the size the site uses
    -- for a card, a sidebar, or a "Most Read" block.
    featured_image_url ilike '%/thumb.php?%'
    -- The elephant itself, in case it was ever stored under another
    -- proxy form.
    or featured_image_url ilike '%8481867891459148672780311864949528951689914n%'
    -- A directory path with no file behind it. There is no picture
    -- at the end of it, and it renders as a broken card.
    or featured_image_url !~* '\.(jpe?g|png|webp|gif|avif|bmp|svg)(\?|#|$)'
    -- The byline portrait: a photograph of the reporter, not of the
    -- story.
    or featured_image_url ilike '%/uploads/source/author/%'
  )
order by published_at desc nulls last;

-- The removal. The picture only; the row, the headline and the
-- article's own key points all stay.
update articles
set featured_image_url = null,
    image_highres_url  = null,
    gif_url            = null,
    featured_image_alt = null,
    image_verified_at  = null
where status = 'published'
  and duplicate_of is null
  and featured_image_url is not null
  and (
    featured_image_url ilike '%/thumb.php?%'
    or featured_image_url ilike '%8481867891459148672780311864949528951689914n%'
    or featured_image_url !~* '\.(jpe?g|png|webp|gif|avif|bmp|svg)(\?|#|$)'
    or featured_image_url ilike '%/uploads/source/author/%'
  );
