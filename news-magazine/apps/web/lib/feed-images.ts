/**
 * Repeated-image suppression for a rendered feed.
 *
 * The companion to lib/feed-dedup.ts, and the completion of
 * `preferIllustrated` in lib/news.ts. Outlets reuse photographs. An
 * agency press shot ends up above three unrelated stories, an outlet's
 * site-wide banner gets scraped as an og:image, a stock portrait floats
 * above a story it has nothing to do with. The homepage then shows two
 * genuinely different stories under one identical picture — and the
 * reader has no way to tell a repeated photograph from a repeated story,
 * so they read both cards as a rendering fault on our side. That is the
 * bug: "Upper Karnali project set to advance" and "Why your Dashain
 * apples will cost more this year" under one portrait of a woman in
 * glasses.
 *
 * Title dedup cannot catch any of it, by construction. These are not
 * near-duplicate headlines — they are different stories, filed by
 * different outlets, about different things. The picture is the only
 * thing they share, so it is the only thing available to compare.
 *
 * Same rules as the story pass, for the same reasons.
 *
 *   Order is never changed. The survivor keeps its OWN slot. Promoting a
 *   six-hour-old story above a two-hour-old one to win a thumbnail is a
 *   worse lie than a repeated photo, and "newest first" is the promise
 *   the whole page rests on — the same refusal `preferIllustrated` makes
 *   when it declines to reorder around missing pictures. A dropped
 *   card's slot is filled by the next story DOWN, which is why every
 *   caller over-fetches before calling this (DEDUP_HEADROOM in
 *   lib/news.ts).
 *
 *   Scope is one response. There is deliberately no table of "this image
 *   has been used recently". A photograph that is legitimately reused
 *   days later — the same official at the same event, a follow-up on last
 *   week's story — would be hidden by such a table forever, and the
 *   reader would conclude the site had run dry. A feed is a snapshot;
 *   this filters the snapshot.
 *
 *   A row with no image contributes NO key at all, so two imageless rows
 *   never suppress one another. See `imageKey` for why that is a null
 *   rather than an empty string.
 *
 * What this cannot catch, stated plainly rather than left to be
 * discovered: a photograph reused by two DIFFERENT outlets. OnlineKhabar
 * and Nagarik both run the same agency shot, but each serves it from its
 * own host at its own path, so the two keys differ and both cards render
 * it. Catching that needs the image's CONTENT — a perceptual hash over
 * decoded bytes — which means fetching and comparing pictures on every
 * page render, or storing a hash at ingest. Neither is free, and a wrong
 * hash merges two unrelated stories. So the rule here is deliberately
 * narrow: it fixes the repeats this site can observe inside its own rows
 * (one outlet reusing its own file, the same CDN path re-spelled), and
 * makes no claim about the rest. A repeat it misses shows the picture and
 * reads as an annoyance; a repeat it wrongly drops would delete a story.
 */

import { imageKey } from "@/lib/image-url";

/** The fields `dropRepeatedImages` reads. Everything else is carried through. */
export interface ImageRow {
  id: string;
  featured_image_url?: string | null;
  image_highres_url?: string | null;
  gif_url?: string | null;
}

/**
 * Every key one row can be recognised by.
 *
 * All of the row's own renditions, not a single pick: the card chooses at
 * render time and cannot know which will load — `pickImages` in
 * components/NewsCard.tsx walks highres → featured → gif and falls through
 * on a 404. Two stories sharing ANY rendition will show the same picture
 * once one of them falls back, so sharing any rendition is sharing the
 * picture as far as the reader is concerned.
 *
 * `gif_url` is included because NewsCard renders it in exactly the same
 * slot when a story has no photograph. Two articles sharing one site's
 * default gif is the same defect as two sharing a photo.
 */
function rowKeys(row: ImageRow): string[] {
  const keys: string[] = [];
  for (const url of [row.image_highres_url, row.featured_image_url, row.gif_url]) {
    const key = imageKey(url);
    if (key && !keys.includes(key)) keys.push(key);
  }
  return keys;
}

/**
 * Keep one card per photograph.
 *
 * Greedy in feed order, the first row to claim a key keeps it and every
 * later row carrying that key goes. Linear rather than the pairwise scan
 * `dropDuplicateStories` needs, because image identity is exact: a Set
 * lookup settles it, there is no similarity threshold to tune, and a
 * feed is tens of rows.
 *
 * A dropped row's keys are NOT added to the set. It is not rendered, so
 * an image that appears nowhere else on the page stays available to a
 * later story instead of being suppressed by a card the reader never
 * sees.
 *
 * Empty in, empty out: if every row shares one photograph, this returns
 * a single card rather than an empty page. The feed the caller fetches
 * is over-provisioned for exactly that reason, and the reader still gets
 * one real story.
 */
export function dropRepeatedImages<T extends ImageRow>(rows: T[]): T[] {
  if (rows.length < 2) return rows;

  const seen = new Set<string>();
  const dropped = new Set<string>();

  for (const row of rows) {
    const keys = rowKeys(row);

    if (keys.some((key) => seen.has(key))) {
      dropped.add(row.id);
      continue;
    }

    for (const key of keys) seen.add(key);
  }

  return rows.filter((row) => !dropped.has(row.id));
}