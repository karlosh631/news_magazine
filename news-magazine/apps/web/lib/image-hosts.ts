/**
 * Which hosts the Next.js image optimizer is allowed to fetch from.
 *
 * The list itself lives in `lib/image-hosts.json`, and that is deliberate.
 * Two different pieces of code have to agree on it and they are written in
 * two different languages:
 *
 *   - `next.config.js` needs it as plain CommonJS at config load time, and
 *     is not TypeScript;
 *   - the app needs it at RENDER time, in TypeScript, to decide whether a
 *     given picture may be handed to `next/image` at all.
 *
 * A second copy of the list in either place would be free to drift from the
 * other, and the failure mode of that drift is not subtle: a host that the
 * config forgot renders a card with no picture and nothing else reports it.
 * A JSON file is importable by both — `require()` in the config, `import` in
 * the app — so there is exactly one list on disk. This module is the only
 * place the rules for READING it live.
 *
 * The wildcard semantics below are Next's, not ours, and they are the part
 * that is easiest to get wrong: a `**.example.com` pattern matches every
 * SUBDOMAIN and *not* the apex, which is why a site that serves photographs
 * from both `example.com` and `www.example.com` needs two entries. That was
 * verified against the picomatch implementation Next itself uses, and
 * tests/image-hosts.spec.ts pins the whole table down.
 */

import remotePatterns from "@/lib/image-hosts.json";

/** One entry of the allowlist. Only these two keys are ever read. */
export type RemotePattern = {
  protocol: string;
  hostname: string;
};

/**
 * The allowlist, in the exact shape `images.remotePatterns` wants.
 *
 * next.config.js consumes the same array, so a host cannot be added to the
 * optimizer without the app learning about it (and vice versa) — the two
 * cannot disagree, because there is only one of them.
 */
export const IMAGE_HOST_PATTERNS: RemotePattern[] = remotePatterns;

/**
 * Does one pattern cover this URL?
 *
 * Mirrors `matchRemotePattern` in Next's own `match-remote-pattern.js` for
 * the two pattern shapes this allowlist actually uses, and mirrors it
 * strictly: anything this returns true for, Next also accepts.
 *
 *   - `example.com`     the apex and nothing else;
 *   - `**.example.com`  every subdomain, and NOT the apex.
 *
 * Being more permissive than Next would be the dangerous direction — a
 * `next/image` render for a host the config does not list THROWS at render
 * time and takes the card with it — so the fallback in
 * components/OptimizedImage.tsx is what makes this safe rather than this
 * function being clever.
 *
 * `pathname`, `port` and `search` are deliberately not consulted: Next only
 * applies them when the pattern sets them, and this allowlist does not.
 * tests/image-hosts.spec.ts fails if an entry ever grows a third key.
 */
function patternCovers(pattern: RemotePattern, url: URL): boolean {
  if (pattern.protocol && pattern.protocol !== url.protocol.replace(/:$/, "")) {
    return false;
  }

  // URL already lowercases the host, but a pattern is just a string in a
  // JSON file and does not get that treatment for free.
  const host = url.hostname.toLowerCase();
  const wanted = pattern.hostname.toLowerCase();

  if (wanted.startsWith("**.")) {
    return host.endsWith(`.${wanted.slice(3)}`);
  }
  return host === wanted;
}

/**
 * May this URL be handed to `next/image`?
 *
 * False for anything that is not an absolute https URL, and false for any
 * host the optimizer would reject. The caller is expected to render a plain
 * `<img>` in that case, which is what the app did before the optimizer was
 * introduced and still works for every host.
 */
export function canOptimizeImage(src: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(src);
  } catch {
    return false;
  }
  if (parsed.protocol !== "https:") return false;
  return IMAGE_HOST_PATTERNS.some((pattern) => patternCovers(pattern, parsed));
}
