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

/**
 * A looser key: the FILENAME with its digits and separators removed.
 *
 * WHY A SECOND KEY IS NEEDED
 * --------------------------
 * `imageKey` is exact, and exactness cannot see a syndicated photograph.
 * Two outlets covering the same event each re-upload the same agency
 * picture under their own filename, so the two cards show one photograph
 * twice while the keys differ in every character. Measured on the live
 * feed:
 *
 *   assets-cdn.kathmandupost.com/.../kbktmrss04u3a81321211020260848571000x0-1792569245.jpg
 *   english.nepalnews.com/.../KB_-KTM_-RSS_-04_-_U3A8132-1-768x512-1.jpeg
 *
 * Both are the same frame of the President receiving Dashain tika. With
 * the digits and the separators gone the two filenames both reduce to
 * "kbktmrssuab", which is the photograph's identity underneath the
 * renaming.
 *
 * WHY DIGITS AND SEPARATORS, AND NOTHING ELSE
 * -------------------------------------------
 * The renaming an agency photo goes through keeps the letters and
 * changes everything else: a counter, a date, a size, the separators
 * that join them. So the letters are the part that survives, and they
 * are what identifies the picture.
 *
 * The length floor matters. "image.jpg" and "photo.png" reduce to
 * "image" and "photo", which would collapse every story that uses a
 * generically named file into one. At 10 characters a key has to be
 * specific enough to be a real photograph's name - measured against the
 * live feed, ten letters produced ten clusters and thirteen rows, and
 * every one of them was either a genuine duplicate or a publisher's
 * own site-wide default image.
 *
 * WHAT THIS CANNOT DO
 * -------------------
 * It cannot tell two DIFFERENT photographs that happen to share a
 * filename. When it guesses wrong the cost is one card showing no
 * picture instead of a duplicate one - the story is still on the page,
 * and the honest placeholder is better than the same frame twice.
 */
export function imageStemKey(url: string | null | undefined): string | null {
  const trimmed = (url ?? "").trim();
  if (!trimmed) return null;

  const withoutFragment = trimmed.split("#")[0] ?? trimmed;
  const path = withoutFragment.split("?")[0] ?? withoutFragment;
  const fileName = path.replace(/^https?:\/\//i, "").split("/").pop() ?? "";

  // The extension goes first, for the same reason the size token does:
  // it is not part of the picture's name. One outlet saved the frame as
  // ".jpg" and another as ".jpeg", and keeping the extension would make
  // two copies of one photograph differ by three letters.
  const name = fileName.replace(/\.[A-Za-z0-9]+$/, "");

  // Letters only: digits and separators are the part that gets rewritten
  // when one photo is published by two outlets.
  //
  // The size token goes next, and that order matters too. A rendition
  // marker is digits, an "x", and more digits - "768x512", and the
  // "x0" a news CMS leaves on an unknown-size original. If those
  // letters were kept, the two copies of one photograph would differ
  // by a stray "x" and never match. Removing the whole token first
  // leaves the letters that actually name the picture:
  //
  //   kbktmrss04u3a81321211020260848571000x0-1792569245  -> kbktmrssuab
  //   KB_-KTM_-RSS_-04_-_U3A8132-1-768x512-1              -> kbktmrssuab
  const withoutSizes = name.toLowerCase().replace(/\d+x\d*/g, "");
  const stem = withoutSizes.replace(/[^a-z]/g, "");

  return stem.length >= IMAGE_STEM_MIN_LENGTH ? stem : null;
}

/** The shortest filename stem that is specific enough to be a photograph. */
export const IMAGE_STEM_MIN_LENGTH = 10;