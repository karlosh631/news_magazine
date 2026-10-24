import { expect, test } from "@playwright/test";

import { IMAGE_HOST_PATTERNS, canOptimizeImage } from "../lib/image-hosts";
import { mergeArticles, byNewest } from "../lib/feed-merge";

/**
 * The image-optimizer allowlist, and the two halves that have to agree
 * about it.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * `next/image` THROWS while rendering if it is handed a URL whose host is
 * not in `images.remotePatterns`. It does not fall back to a plain <img>; it
 * fails the render. So the runtime check in components/OptimizedImage.tsx
 * has to be at least as strict as Next's own matcher — one host that the
 * check allows and the config does not is a card that does not exist.
 *
 * The wildcard rule is the part worth pinning: `**.example.com` matches
 * every SUBDOMAIN and not the apex. That was read out of Next's bundled
 * picomatch, not assumed, and the table below is what it actually does.
 */

/** Every host seen serving an article image in a 400-article sample. */
const OBSERVED_HOSTS = [
  "npcdn.ratopati.com",
  "www.onlinekhabar.com",
  "images.nagariknewscdn.com",
  "assets-cdn.kathmandupost.com",
  "english.nepalnews.com",
  "www.setopati.com",
  "nepalnews.com",
  "thehimalayantimes.com",
  "ichef.bbci.co.uk",
  "english.onlinekhabar.com",
  "assets-api.kathmandupost.com",
  "rpcdn.ratopati.com",
  "ictsamacharcdn.prixacdn.net",
  "static.hugedomains.com",
  "www.nepalhomes.com",
  "nagariktimes.com",
];

test.describe("the optimizer allowlist", () => {
  test("every host the live feed actually uses is on it", () => {
    // The three that were missing before this file existed: www.setopati.com
    // (only the bare apex was listed), ichef.bbci.co.uk (a subdomain of a
    // different domain to the bbc.com entry) and static.hugedomains.com.
    const missing = OBSERVED_HOSTS.filter(
      (host) => !canOptimizeImage(`https://${host}/some/photo-1200x675.jpg`)
    );
    expect(missing).toEqual([]);
  });

  test("a wildcard subdomain pattern does not cover the apex", () => {
    // Next's `**` matches subdomains but not `example.com` itself, which is
    // why the list carries both `setopati.com` and `**.setopati.com`. If this
    // ever passes, the matcher has become more permissive than the config
    // and a host could slip through to a render-time throw.
    expect(canOptimizeImage("https://sub.example-not-listed.com/a.jpg")).toBe(
      false
    );
    expect(canOptimizeImage("https://setopati.com/a.jpg")).toBe(true);
    expect(canOptimizeImage("https://www.setopati.com/a.jpg")).toBe(true);
    expect(canOptimizeImage("https://deep.www.setopati.com/a.jpg")).toBe(true);
    expect(canOptimizeImage("https://xsetopati.com/a.jpg")).toBe(false);
    expect(canOptimizeImage("https://bbci.co.uk/a.jpg")).toBe(false);
    expect(canOptimizeImage("https://ichef.bbci.co.uk/a.jpg")).toBe(true);
  });

  test("host matching is case-insensitive, the way a URL is", () => {
    // `new URL()` lowercases the host, so the config only has to be
    // consistent with that rather than case-fold everything itself.
    expect(canOptimizeImage("https://WWW.Setopati.com/a.jpg")).toBe(true);
  });

  test("only absolute https URLs may be optimized", () => {
    // toHttps() in NewsCard has already rewritten http:// to https:// before
    // anything gets here, but a protocol-relative or relative URL must not
    // be handed to next/image either way.
    expect(canOptimizeImage("http://www.setopati.com/a.jpg")).toBe(false);
    expect(canOptimizeImage("//www.setopati.com/a.jpg")).toBe(false);
    expect(canOptimizeImage("/uploads/a.jpg")).toBe(false);
    expect(canOptimizeImage("")).toBe(false);
    expect(canOptimizeImage("not a url")).toBe(false);
  });

  test("the query string and path never change the answer", () => {
    // Outlet CDNs put the file identity in the query on several hosts
    // (`thumb.php?src=...`), so a pattern must not be narrowed by one.
    const plain = canOptimizeImage("https://assets-api.kathmandupost.com/thumb.php?src=x");
    const noisy = canOptimizeImage(
      "https://assets-api.kathmandupost.com/thumb.php?src=x&w=300&height=200"
    );
    expect(plain).toBe(true);
    expect(noisy).toBe(plain);
  });

  test("every entry is a bare protocol+hostname pair", () => {
    // The matcher above reads only those two keys. An entry that grew a
    // `pathname` or a `port` would be honoured by Next and ignored here,
    // which is exactly the silent divergence this guards against.
    const shaped = IMAGE_HOST_PATTERNS.filter(
      (pattern) => Object.keys(pattern).sort().join(",") !== "hostname,protocol"
    );
    expect(shaped).toEqual([]);
  });

  test("the list is short enough for Next to accept", () => {
    // Next refuses to start with more than 50 remotePatterns.
    expect(IMAGE_HOST_PATTERNS.length).toBeLessThanOrEqual(50);
  });
});

/**
 * The rule that stopped the homepage rebuilding itself every fifteen
 * seconds — see lib/feed-merge.ts for the measured before/after.
 */
test.describe("merging a batch into a running feed", () => {
  const story = (id: string, ingested: string, published = ingested) => ({
    id,
    ingested_at: ingested,
    published_at: published,
    source_count: 1,
  });

  test("a poll that found nothing returns the same array", () => {
    // Identity, not just equality: React bails out of the re-render when the
    // state setter gets the array back, and a re-render of the grid is what
    // the layout shift was made of.
    const current = [story("a", "2026-10-20T10:00:00Z"), story("b", "2026-10-20T09:00:00Z")];
    expect(mergeArticles(current, [story("a", "2026-10-20T10:00:00Z")])).toBe(current);
    expect(mergeArticles(current, [])).toBe(current);
  });

  test("a genuinely new story goes to the top and nothing else moves", () => {
    const current = [story("a", "2026-10-20T10:00:00Z"), story("b", "2026-10-20T09:00:00Z")];
    const merged = mergeArticles(current, [story("c", "2026-10-20T11:00:00Z")]);
    expect(merged.map((a) => a.id)).toEqual(["c", "a", "b"]);
  });

  test("the server re-rendering the same page does not shrink the list", () => {
    // THE case: components/Header.tsx calls router.refresh() every 15s, which
    // hands the feed the same twenty-four stories in a new array. The old
    // code replaced the list with them and the grid collapsed from 60 cards
    // to 24.
    const serverPage = [story("a", "2026-10-20T10:00:00Z"), story("b", "2026-10-20T09:00:00Z")];
    const union = mergeArticles(serverPage, [story("c", "2026-10-20T11:00:00Z")]);
    expect(mergeArticles(union, serverPage)).toBe(union);
    expect(mergeArticles(union, serverPage).map((a) => a.id)).toEqual(["c", "a", "b"]);
  });

  test("a story already on the page keeps the copy it arrived with", () => {
    // Deliberate, and the other half of the identity rule: the API hands
    // back freshly-built objects for every row on every poll, so comparing
    // them would rebuild the list ten times a minute. The trade is that a
    // row the worker enriches later (more outlets carrying it, a better
    // photograph) shows as it first was until the page is reloaded.
    const current = [{ ...story("a", "2026-10-20T10:00:00Z"), source_count: 1 }];
    const merged = mergeArticles(current, [
      { ...story("a", "2026-10-20T10:00:00Z"), source_count: 4 },
    ]);
    expect(merged).toBe(current);
    expect(merged[0]!.source_count).toBe(1);
  });

  test("the newest FETCHED comes first, whatever order it was published in", () => {
    // The page is the newest FETCHED. The reader asked for it in the
    // same words several times: "the new articles must be shown on the
    // top based on the time immediatley fetch".
    //
    // The helper is (id, ingested, published): "early" was published an
    // hour before "late", but it was fetched after it - so it is the
    // one the reader sees, because it arrived last.
    const publishedEarlier = story("early", "2026-10-20T11:00:00Z", "2026-10-20T10:00:00Z");
    const publishedLater = story("late", "2026-10-20T08:00:00Z", "2026-10-20T11:00:00Z");
    expect(
      [publishedEarlier, publishedLater].sort(byNewest).map((a) => a.id)
    ).toEqual(["early", "late"]);
  });

  test("within one arrival, the id breaks the tie", () => {
    // Two rows fetched in the same instant. The id decides, so the
    // server's answer and the browser's sort cannot disagree and
    // reshuffle the page under the reader.
    const publishedFirst = story("00000000-0000-5000-8000-000000000001", "2026-10-20T09:00:00Z", "2026-10-20T08:00:00Z");
    const publishedLast = story("00000000-0000-5000-8000-000000000002", "2026-10-20T09:00:00Z", "2026-10-20T08:30:00Z");
    expect(
      [publishedFirst, publishedLast].sort(byNewest).map((a) => a.id)
    ).toEqual(["00000000-0000-5000-8000-000000000002", "00000000-0000-5000-8000-000000000001"]);
  });
});
