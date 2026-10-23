/**
 * What a card has to show.
 *
 * A news card shows, in order of preference: its picture, its own key
 * points, or an honest "No image available" panel. When it has none of
 * the three, the feed swaps it for the next story — which is the
 * operator's rule ("never show a card with no picture, show a
 * different story instead").
 *
 * WHY THE ANSWER HAS TO BE KNOWN BEFORE THE FIRST PAINT
 * ------------------------------------------------------
 * The swap used to be driven by `onImageUnavailable`, which a card
 * raises from an effect once it knows it has nothing to show. An
 * effect runs AFTER the first paint, so the card was in the page, the
 * reader had already seen it, and it was then taken away — and every
 * card below it slid up into the gap.
 *
 * Measured on the homepage and on a category page, that was a
 * Cumulative Layout Shift of 0.07 and 0.21: the difference between a
 * "good" CLS and a "poor" one, caused by a rule that was doing
 * exactly what it was asked to do.
 *
 * But whether a card will show nothing is a fact about the STORY, and
 * the story is in hand before anything is rendered. A story with no
 * picture, no points and no video is a story with nothing to show,
 * and the feed can leave it out of the very first paint. Nothing
 * moves, because nothing was ever there.
 *
 * WHERE THE RULE LIVES
 * --------------------
 * This mirrors the rule components/NewsCard.tsx applies to a single
 * card, and the two have to agree — a card that shows a panel the
 * feed thought was empty, or the other way round, is a card that is
 * either removed for no reason or kept when it should have gone.
 *
 * The card's own copy of the rule could not be imported from here
 * when this was written, because that file was being edited by
 * another change at the same time; it lives in the card because the
 * card is what renders the states. When that file is next touched,
 * the two should be folded into this one.
 *
 * The video test here is deliberately the LOOSER one: any video at
 * all counts as something to show, where the card accepts only an
 * embeddable or directly playable one. A looser test here means a
 * card is occasionally offered for removal that the card then keeps,
 * which costs nothing; a tighter one would remove a card that had
 * something to show, which is a lie.
 */

import type { LatestArticle } from "@/types/database";

/**
 * The story's own key points, if it has any.
 *
 * A card shows them where the picture would be, so a story with
 * points has something to show even with no picture at all.
 */
export function storyPoints(article: {
  summary_points?: string[] | null;
}): string[] {
  return (article.summary_points ?? []).filter(
    (point): point is string =>
      typeof point === "string" && point.trim().length > 0
  );
}

/**
 * Will this card have nothing at all to show?
 *
 * True for a story with no picture, no key points and no video — the
 * only kind of story the feed may swap for another one.
 */
export function showsNothing(article: LatestArticle): boolean {
  if (storyPoints(article).length > 0) return false;

  // Any video at all is something to show.
  if (article.video_url || article.video_embed_url) return false;

  // A picture that was never confirmed against the story's own page is
  // not shown at all — see the operator's rule in NewsCard.
  if (article.image_verified_at === null) return true;

  return ![
    article.image_highres_url,
    article.featured_image_url,
    article.gif_url,
  ].some(
    (url) => typeof url === "string" && /^https?:\/\//i.test(url.trim())
  );
}
