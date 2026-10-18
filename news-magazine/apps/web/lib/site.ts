/**
 * Site-wide constants and the JSON-LD serialiser.
 *
 * These were duplicated as file-local `SITE_URL` consts in layout.tsx,
 * robots.ts and sitemap.ts, and one of the fixes that needed it would
 * have silently shipped `undefined` because the const was not in scope.
 */

export const SITE_URL = (
  process.env.NEXT_PUBLIC_SITE_URL ?? "https://new24hr.vercel.app"
).replace(/\/+$/, "");

export const SITE_NAME = "Nepal News & Magazine";

/** The wordmark shown in the header. */
export const SITE_SHORT_NAME = "NEWS";

/**
 * Publisher logo for structured data.
 *
 * Google requires a publisher logo of at least 112x112. app/icon.svg has
 * a 64x64 viewBox, so it cannot satisfy that — the 192px PNG can.
 */
export const SITE_LOGO = `${SITE_URL}/icon-192.png`;

/**
 * Serialise a JSON-LD object for a <script type="application/ld+json">.
 *
 * JSON.stringify alone is not safe here. A headline is feed-derived text
 * and a headline containing `</script>` closes the script element early,
 * turning the remainder of the value into markup the browser parses —
 * so a publisher (or anyone who can get text into a feed) could inject
 * script into our pages. Escaping `<` as < produces
 * byte-identical JSON that no parser can break out of.
 */
export function jsonLd(value: unknown): string {
  return JSON.stringify(value)
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/&/g, "\\u0026")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
}

/**
 * Clamp a description to a length search engines will actually render.
 *
 * Excerpts are extracted from article bodies, so they routinely run to
 * several hundred characters. Google truncates around 160 and social
 * platforms around 200; anything past that is discarded silently.
 */
export function clampDescription(text: string | null | undefined, max = 200): string {
  if (!text) return "";
  const collapsed = text.replace(/\s+/g, " ").trim();
  if (collapsed.length <= max) return collapsed;

  const cut = collapsed.slice(0, max);
  const lastSpace = cut.lastIndexOf(" ");
  return `${(lastSpace > max * 0.6 ? cut.slice(0, lastSpace) : cut).trimEnd()}…`;
}