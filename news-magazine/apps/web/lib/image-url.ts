/**
 * Image URL normalisation, shared by the renderer and the read path.
 *
 * `withoutSizeSuffix` used to live in components/NewsCard.tsx, where its
 * only job was picking which rendition to fall back to when another one
 * 404s. The read path needs the same knowledge for a different reason —
 * deciding whether two cards are showing the same photograph — so the
 * function moved here and both sides import it. Two copies of that regex
 * would drift, and a drift between "what the card renders" and "what the
 * feed compared" is exactly the kind of bug that makes this look like it
 * does not work.
 *
 * Nothing in here touches React, the DOM, or the database: NewsCard pulls
 * this file into the browser bundle and lib/feed-images.ts pulls the same
 * file into the server one.
 */

/**
 * Strip a rendition suffix so a dead "-1200x675" rendition falls back to
 * the original file at its own path.
 *
 * Moved verbatim from NewsCard. The three forms are the three ways an
 * outlet's CDN spells "the same photograph, a different size": a
 * `-1200x675` infix before the extension, a `-w-1200` path segment, and a
 * `?w=1200` query parameter.
 */
export function withoutSizeSuffix(url: string): string {
  return url
    .replace(/[-_]\d{2,4}x\d{2,4}(?=\.(?:jpg|jpeg|png|webp))/i, "")
    .replace(/[-_](?:w|width|resize)[-_]?\d{2,4}(?=[x_.]|\.(?:jpg|jpeg|png|webp))/i, "")
    .replace(/[?&]w=\d{2,4}\b/i, "");
}

/**
 * Query parameters that name a RENDITION, not a file.
 *
 * Only these are dropped, and deliberately not the whole query string. On
 * several CDNs the query IS the file identity — `photo?id=45123` and
 * `photo?id=45124` are two different photographs served from one path — so
 * discarding it would merge two unrelated stories and drop one of them off
 * the page. That is the worse of the two mistakes: a missed duplicate is
 * an annoyance, a removed story is a lie about what was reported today.
 */
const RENDITION_PARAM =
  /^(?:w|width|h|height|q|quality|resize|size|sz|fit|fm|format|auto|dpr)$/i;

/** The parameter name, decoded, tolerating a malformed percent-escape. */
function paramName(param: string): string {
  const raw = param.split("=")[0] ?? "";
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

/**
 * The key two image URLs must share to count as the same picture.
 *
 * Null for anything that is not a URL. That is the whole reason this
 * returns a nullable: a story with no photograph must have NO key, not the
 * key "", or every imageless card on the page would suppress the next one
 * and a text-only feed would collapse to a single card.
 *
 * Four things are normalised away, and nothing else:
 *
 *   - the scheme and a leading `www.`, because one photograph is fetched
 *     from http://, https:// and www.example.com alike;
 *   - the fragment, which is never sent to the CDN and so cannot select a
 *     different file;
 *   - the rendition parameters above, via withoutSizeSuffix and the
 *     RENDITION_PARAM filter;
 *   - the ORDER of the surviving parameters, since `?a=1&b=2` and `?b=2&a=1`
 *     are the same request.
 *
 * Everything else — host, path, and any parameter that is not a rendition —
 * survives, so two distinct files are never collapsed into one key.
 */
export function imageKey(url: string | null | undefined): string | null {
  const trimmed = (url ?? "").trim();
  if (!trimmed) return null;

  const addressed = trimmed
    .replace(/^https?:\/\//i, "")
    .replace(/^www\./i, "");

  const [pathAndQuery = ""] = addressed.split("#");
  const [path = "", query = ""] = pathAndQuery.split("?");

  const kept = query
    .split("&")
    .filter(Boolean)
    .filter((param) => !RENDITION_PARAM.test(paramName(param)))
    .sort();

  const base = withoutSizeSuffix(path);
  return kept.length > 0 ? `${base}?${kept.join("&")}` : base;
}