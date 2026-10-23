/**
 * The rules of the sign-in rate limit, with nothing in them.
 *
 * This file has no imports on purpose. It is the part of the limiter
 * that can be reasoned about - and tested - without a database, a
 * server or a network, which is what `lib/admin-rate-limit.ts` then
 * keeps somewhere.
 */

/** The knobs. The numbers are the ones the code below explains. */
export const RATE_LIMIT = {
  /** How long a run of failures is remembered. */
  windowMs: 15 * 60 * 1000,
  /** Failures from one address before that address is blocked. */
  ipMaxFailures: 20,
  /** Failures against one account before the delay starts. */
  accountDelayThreshold: 5,
  /** The first delay. */
  accountDelayBaseMs: 1000,
  /** The longest delay anybody waits. */
  accountDelayMaxMs: 8000,
} as const;

/**
 * How long to wait before answering a failed attempt.
 *
 * Zero for the first few failures, then it doubles: one second, two,
 * four, eight, and eight from then on. The ceiling is deliberate - a
 * serverless function that sleeps for a minute is a function that
 * costs a minute, and an eight second ceiling still leaves a guesser
 * with about one attempt every eight seconds.
 */
export function failureDelayMs(failures: number): number {
  if (!Number.isFinite(failures) || failures <= RATE_LIMIT.accountDelayThreshold) {
    return 0;
  }

  const steps = failures - RATE_LIMIT.accountDelayThreshold;
  const delay = RATE_LIMIT.accountDelayBaseMs * 2 ** (steps - 1);
  return Math.min(delay, RATE_LIMIT.accountDelayMaxMs);
}

/**
 * Whether a string looks like an IP address.
 *
 * Loose on purpose: the point is to notice that the header holds an
 * address rather than to parse one perfectly.
 */
function looksLikeIp(value: string): boolean {
  if (!value) return false;
  if (value.includes(":")) return /^[0-9a-f:.]+$/i.test(value);
  return /^[0-9.]{3,45}$/.test(value);
}

/**
 * The address the request came from.
 *
 * `x-vercel-forwarded-for` first: it is the address the edge saw, and
 * it cannot be replaced by whatever the caller put in the headers.
 * `x-forwarded-for` is the fallback, and its FIRST entry is the
 * original client by the usual convention.
 *
 * The address can be spoofed by a caller who is not behind a proxy
 * that replaces the header, which is exactly why the per-ACCOUNT
 * limit is the one that matters: the account limit cannot be spoofed
 * at all, because it is keyed on the email address that was typed.
 */
export function clientIpFromHeaders(headers: Headers): string {
  const candidates = [
    headers.get("x-vercel-forwarded-for"),
    headers.get("x-real-ip"),
    headers.get("x-forwarded-for"),
  ];

  for (const candidate of candidates) {
    if (!candidate) continue;
    const first = candidate.split(",")[0]?.trim();
    if (first && looksLikeIp(first)) return first.toLowerCase();
  }

  return "unknown";
}
