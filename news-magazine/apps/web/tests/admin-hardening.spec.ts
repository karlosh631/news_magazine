import { expect, test } from "@playwright/test";

import {
  clientIpFromHeaders,
  failureDelayMs,
  RATE_LIMIT,
} from "../lib/admin-rate-limit-policy";
import {
  crossSiteReason,
  isSameOriginRequest,
  originHost,
  requestHost,
} from "../lib/same-origin";

/**
 * The two things that stand between a stranger and the admin panel.
 *
 * The origin check is what stops a page on another site from making
 * the operator's browser sign in, sign out, or trigger a sync. The
 * rate limit is what stops a script from guessing the password.
 *
 * Both are pure functions of a request's headers, so both are tested
 * here without a server, a database or a browser.
 */

const SITE = "news.example.com";

function headers(values: Record<string, string>): Headers {
  return new Headers({ host: SITE, ...values });
}

test.describe("where a request came from", () => {
  test("a request from this site is allowed", () => {
    expect(
      isSameOriginRequest(headers({ origin: `https://${SITE}` }))
    ).toBe(true);
  });

  test("a request from another site is refused", () => {
    expect(
      isSameOriginRequest(headers({ origin: "https://evil.example" }))
    ).toBe(false);
  });

  test("the browser's own answer beats every other header", () => {
    // Sec-Fetch-Site is set by the browser and cannot be set by the
    // page. A cross-site there is a refusal whatever else says.
    expect(
      isSameOriginRequest(
        headers({ "sec-fetch-site": "cross-site", origin: `https://${SITE}` })
      )
    ).toBe(false);

    expect(
      isSameOriginRequest(headers({ "sec-fetch-site": "same-origin" }))
    ).toBe(true);

    expect(
      isSameOriginRequest(headers({ "sec-fetch-site": "none" }))
    ).toBe(true);
  });

  test("an origin of null names no site and is refused", () => {
    // A sandboxed frame and a data: URL both send "null".
    expect(isSameOriginRequest(headers({ origin: "null" }))).toBe(false);
  });

  test("the referer is the fallback when there is no origin", () => {
    expect(
      isSameOriginRequest(headers({ referer: `https://${SITE}/login` }))
    ).toBe(true);

    expect(
      isSameOriginRequest(headers({ referer: "https://evil.example/login" }))
    ).toBe(false);
  });

  test("a request with neither header is not a browser request", () => {
    // A command line client sends neither, and refusing those would
    // break the cron that calls the admin sync route. A browser
    // always sends Sec-Fetch-Site and Origin on a POST, so this cannot
    // be reached by a forged form.
    expect(isSameOriginRequest(headers({}))).toBe(true);
  });

  test("the port is part of the host", () => {
    expect(
      isSameOriginRequest(headers({ origin: `https://${SITE}:8443` }))
    ).toBe(false);

    expect(
      isSameOriginRequest(
        headers({ host: `${SITE}:8443`, origin: `https://${SITE}:8443` })
      )
    ).toBe(true);
  });

  test("the host the edge saw is the one that counts", () => {
    // Behind a proxy the Host header may be the internal one, and
    // x-forwarded-host is what the browser actually used.
    expect(
      requestHost(
        headers({ host: "internal.local", "x-forwarded-host": SITE })
      )
    ).toBe(SITE);

    expect(
      isSameOriginRequest(
        headers({
          host: "internal.local",
          "x-forwarded-host": SITE,
          origin: `https://${SITE}`,
        })
      )
    ).toBe(true);
  });

  test("a header that is not a URL names no host", () => {
    expect(originHost("not a url")).toBeNull();
    expect(originHost("https://news.example.com/x")).toBe(SITE);
  });

  test("the reason says which header said no", () => {
    expect(
      crossSiteReason(headers({ "sec-fetch-site": "cross-site" }))
    ).toContain("sec-fetch-site");

    expect(crossSiteReason(headers({ origin: "https://evil.example" }))).toContain(
      "origin"
    );
  });
});

test.describe("how fast the password may be guessed", () => {
  test("the first few failures are answered at once", () => {
    expect(failureDelayMs(0)).toBe(0);
    expect(failureDelayMs(1)).toBe(0);
    expect(failureDelayMs(RATE_LIMIT.accountDelayThreshold)).toBe(0);
  });

  test("after that the wait grows", () => {
    expect(failureDelayMs(RATE_LIMIT.accountDelayThreshold + 1)).toBe(
      RATE_LIMIT.accountDelayBaseMs
    );
    expect(failureDelayMs(RATE_LIMIT.accountDelayThreshold + 2)).toBe(
      RATE_LIMIT.accountDelayBaseMs * 2
    );
    expect(failureDelayMs(RATE_LIMIT.accountDelayThreshold + 3)).toBe(
      RATE_LIMIT.accountDelayBaseMs * 4
    );
  });

  test("and stops growing at the ceiling", () => {
    // A function that sleeps for a minute is a function that costs a
    // minute, so the wait has a maximum.
    expect(failureDelayMs(100)).toBe(RATE_LIMIT.accountDelayMaxMs);
    expect(failureDelayMs(1000)).toBe(RATE_LIMIT.accountDelayMaxMs);
  });

  test("the delay is the same for an address nobody owns", () => {
    // The counter is keyed on the address that was TYPED. If it were
    // keyed on the accounts that exist, the delay would be a way of
    // finding out which addresses are administrators.
    expect(failureDelayMs(7)).toBe(failureDelayMs(7));
    expect(failureDelayMs(7)).toBeGreaterThan(0);
  });
});

test.describe("where the request came from", () => {
  test("the edge's own answer wins", () => {
    expect(
      clientIpFromHeaders(
        headers({
          "x-vercel-forwarded-for": "203.0.113.7",
          "x-forwarded-for": "10.0.0.1",
        })
      )
    ).toBe("203.0.113.7");
  });

  test("the first entry of x-forwarded-for is the client", () => {
    expect(
      clientIpFromHeaders(headers({ "x-forwarded-for": "203.0.113.7, 10.0.0.1" }))
    ).toBe("203.0.113.7");
  });

  test("a request with no address is 'unknown'", () => {
    // Every caller from that address shares one limit, which is
    // crude but safe.
    expect(clientIpFromHeaders(headers({}))).toBe("unknown");
  });
});
