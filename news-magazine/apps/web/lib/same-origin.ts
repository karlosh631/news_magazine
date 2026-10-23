/**
 * Where did this request come from?
 *
 * A state-changing request that a browser made on ANOTHER site is the
 * attack this file exists to stop: a page somewhere else posts to
 * `/api/admin/logout` and signs the operator out; a page somewhere
 * else posts to `/api/admin/login` and signs the operator into an
 * account the attacker controls.
 *
 * The `SameSite=Lax` cookie stops most of it - a cookie is not sent
 * with a cross-site POST - but `Lax` is chosen so that a link from an
 * email still carries the session, and "most of it" is not "none of
 * it". A request with a JSON body can also be sent cross-site from a
 * script, because `text/plain` is one of the three content types a
 * form may send without asking, and a form with `enctype="text/plain"`
 * can carry a body that happens to parse as JSON.
 *
 * So the origin is checked explicitly, on the server, on every route
 * that changes something.
 *
 * WHAT IS COMPARED
 * ----------------
 * The HOST, not the whole URL. The scheme is not compared because the
 * request that reaches the server may be plain HTTP behind a proxy
 * while the browser saw HTTPS, and a scheme mismatch there would
 * reject every legitimate sign-in. The host is the thing that decides
 * whether the request is "this site or another one", and the host
 * survives the proxy.
 */

/**
 * The host the request was addressed to.
 *
 * `x-forwarded-host` first: it is the host as the edge saw it, which
 * is the host the browser used, and it is the only one of the two that
 * is right when the site is behind a proxy that rewrites `Host`.
 */
export function requestHost(headers: Headers): string | null {
  const forwarded = headers.get("x-forwarded-host");
  if (forwarded) {
    const first = forwarded.split(",")[0]?.trim();
    if (first) return first.toLowerCase();
  }

  const host = headers.get("host");
  if (host) return host.trim().toLowerCase();

  return null;
}

/**
 * The host part of a URL, or null when it is not a URL.
 *
 * An `Origin` of "null" is what a sandboxed frame or a `data:` page
 * sends, and it names no host at all - so it can never match.
 */
export function originHost(value: string): string | null {
  try {
    const url = new URL(value);
    if (!url.host) return null;
    return url.host.toLowerCase();
  } catch {
    return null;
  }
}

/**
 * Whether a request came from this site.
 *
 * The order of the checks matters:
 *
 *   1. `Sec-Fetch-Site` is the browser's own answer to "is this
 *      request from another site?", and it is set by the browser, not
 *      by the page. A "cross-site" there is a refusal, whatever the
 *      other headers say.
 *   2. `Origin` is set by the browser on every POST, including a
 *      same-site one, so its absence means the request is not from a
 *      browser form at all.
 *   3. `Referer` is the older header and the fallback for the
 *      browsers that do not send `Origin`.
 *   4. Neither header: the request is not a browser request. A
 *      command line client or a server-to-server call does not send
 *      them, and refusing those would break the cron that calls the
 *      admin sync route. A browser always sends `Origin` on a POST,
 *      so this cannot be reached by a forged form.
 */
export function isSameOriginRequest(
  headers: Headers,
  host: string | null = requestHost(headers)
): boolean {
  const fetchSite = headers.get("sec-fetch-site");
  if (fetchSite && fetchSite.trim().toLowerCase() === "cross-site") {
    return false;
  }

  const origin = headers.get("origin");
  if (origin) {
    const trimmed = origin.trim();
    // "null" is the origin a sandboxed frame or a data: URL sends. It
    // names no site, so it is never us.
    if (trimmed.toLowerCase() === "null") return false;
    if (!host) return false;
    return originHost(trimmed) === host;
  }

  const referer = headers.get("referer");
  if (referer) {
    if (!host) return false;
    return originHost(referer) === host;
  }

  return true;
}

/**
 * Why a request was refused, in one line. For the log.
 */
export function crossSiteReason(
  headers: Headers,
  host: string | null = requestHost(headers)
): string {
  const fetchSite = headers.get("sec-fetch-site");
  if (fetchSite && fetchSite.trim().toLowerCase() === "cross-site") {
    return "sec-fetch-site: cross-site";
  }

  const origin = headers.get("origin");
  if (origin) {
    return `origin ${origin} does not match host ${host ?? "(none)"}`;
  }

  const referer = headers.get("referer");
  if (referer) {
    return `referer ${referer} does not match host ${host ?? "(none)"}`;
  }

  return "no origin header";
}
