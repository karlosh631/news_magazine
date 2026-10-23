import { expect, test } from "@playwright/test";

import { dropRepeatedImages } from "../lib/feed-images";
import { imageStemKey } from "../lib/image-url";

/**
 * The wrong-picture rules.
 *
 * THE BUG THIS EXISTS FOR
 * -----------------------
 * A reader sent a screenshot of a homepage with two cards showing the
 * SAME photograph, and a separate screenshot of a story about drugs
 * seized from a motorcycle illustrated with an Aston Villa FOOTBALL
 * picture. Both were the same defect: a card showing a picture that
 * belongs to a different story.
 *
 * Two things were wrong, and they needed different fixes.
 *
 *   1. The wrong picture was STORED. The feed's image is whatever the
 *      publisher put in the feed enclosure, and for an outlet that
 *      reuses a slot in its feed that is a DIFFERENT story's picture.
 *      The article's own page is the authority; that is fixed in the
 *      ingestion service, and these specs pin the read side of it.
 *
 *   2. The same photograph appeared twice under two different URLs,
 *      because two outlets each re-uploaded the agency frame under
 *      their own filename. Exact URL matching cannot see that, so the
 *      feed now also compares the filename's letters.
 *
 * AND THE ONE THAT MATTERS MOST: the second card must lose its
 * PICTURE, not its STORY. The old behaviour dropped the row, which is a
 * lie about what was reported today.
 */

const row = (
  id: string,
  featured: string | null,
  extra: Partial<{
    image_highres_url: string | null;
    gif_url: string | null;
  }> = {}
) => ({
  id,
  headline: `Story ${id}`,
  featured_image_url: featured,
  image_highres_url: extra.image_highres_url ?? featured,
  gif_url: extra.gif_url ?? null,
});

test.describe("the same photograph under two different names", () => {
  test("the letters of the filename identify the picture", () => {
    // These are the two URLs measured on the live feed for one
    // photograph of the President receiving Dashain tika.
    expect(
      imageStemKey(
        "https://assets-cdn.kathmandupost.com/uploads/source/news/2026/third-party/kbktmrss04u3a81321211020260848571000x0-1792569245.jpg"
      )
    ).toBe(
      imageStemKey(
        "https://english.nepalnews.com/wp-content/uploads/2026/10/KB_-KTM_-RSS_-04_-_U3A8132-1-768x512-1.jpeg"
      )
    );
  });

  test("the second card is dropped, and the first keeps its picture", () => {
    const rows = [
      row("a", "https://assets-cdn.kathmandupost.com/uploads/source/news/2026/third-party/kbktmrss04u3a81321211020260848571000x0-1792569245.jpg"),
      row("b", "https://english.nepalnews.com/wp-content/uploads/2026/10/KB_-KTM_-RSS_-04_-_U3A8132-1-768x512-1.jpeg"),
    ];

    const kept = dropRepeatedImages(rows);

    // ONE card for one photograph. The second is dropped rather than
    // shown with a blanked picture: a duplicate is almost never a
    // different story — it is the same wire copy, or the same outlet
    // twice — and a card whose headline and picture slot disagree
    // reads as a rendering fault.
    expect(kept).toHaveLength(1);
    expect(kept[0]!.id).toBe("a");
    expect(kept[0]!.featured_image_url).toContain("kbktmrss04u3a8132");
  });

  test("the picture cannot come back through another column", () => {
    // The card walks highres -> featured -> gif and falls through on a
    // 404. A gif that shares the picture's stem is the same picture,
    // and the card that carries it goes with the rest.
    const rows = [
      row("a", "https://x.test/dashaintika-1024x624.jpg"),
      row("b", "https://y.test/dashaintika-1024x624-1.jpg", {
        gif_url: "https://y.test/dashaintika.gif",
      }),
    ];

    const kept = dropRepeatedImages(rows);
    expect(kept.map((r) => r.id)).toEqual(["a"]);
  });

  test("a page of one photograph still shows one card", () => {
    // The floor: dropping every row would leave the reader with an
    // empty page, which is a worse answer than a repeated picture.
    const rows = [
      row("a", "https://x.test/one.jpg"),
      row("b", "https://x.test/one.jpg"),
      row("c", "https://x.test/one.jpg"),
    ];

    const kept = dropRepeatedImages(rows);
    expect(kept).toHaveLength(1);
  });
});

test.describe("a generic filename is not a photograph identity", () => {
  test("a short stem is not trusted", () => {
    // "image.jpg" and "photo.png" must never be treated as the same
    // picture, or every story using a generically named file would
    // lose its artwork.
    expect(imageStemKey("https://x.test/image.jpg")).toBeNull();
    expect(imageStemKey("https://x.test/photo.png")).toBeNull();
    expect(imageStemKey("https://x.test/abc.jpg")).toBeNull();
  });

  test("a specific stem is trusted", () => {
    expect(imageStemKey("https://x.test/dashaintika.jpg")).toBe(
      "dashaintika"
    );
  });

  test("no URL at all has no stem", () => {
    expect(imageStemKey(null)).toBeNull();
    expect(imageStemKey(undefined)).toBeNull();
    expect(imageStemKey("")).toBeNull();
  });
});

test.describe("a picture that is genuinely different stays", () => {
  test("two different photographs are both kept", () => {
    const rows = [
      row("a", "https://x.test/kathmandu-dashain-tika.jpg"),
      row("b", "https://y.test/pokhara-lake-morning.jpg"),
    ];

    const kept = dropRepeatedImages(rows);
    expect(kept).toHaveLength(2);
    expect(kept[0]!.featured_image_url).not.toBeNull();
    expect(kept[1]!.featured_image_url).not.toBeNull();
  });

  test("the same URL twice is still caught", () => {
    // The exact case the old rule handled, and it must keep working.
    const rows = [
      row("a", "https://x.test/one.jpg"),
      row("b", "https://x.test/one.jpg"),
    ];

    const kept = dropRepeatedImages(rows);
    expect(kept).toHaveLength(1);
    expect(kept[0]!.id).toBe("a");
  });

  test("order is never changed", () => {
    const rows = [
      row("a", "https://x.test/kathmandu-dashain-tika.jpg"),
      row("b", "https://y.test/pokhara-lake-morning.jpg"),
      row("c", "https://z.test/kathmandu-dashain-tika.jpg"),
    ];

    const kept = dropRepeatedImages(rows);

    // "c" carries the same photograph as "a" under another host, so it
    // goes. The survivors keep their own slots: the order is never
    // changed, because "newest first" is the promise the page rests on
    // and a card must never be promoted to win a picture.
    expect(kept.map((r) => r.id)).toEqual(["a", "b"]);
  });

  test("a single row is returned untouched", () => {
    const rows = [row("a", "https://x.test/one.jpg")];
    expect(dropRepeatedImages(rows)).toBe(rows);
  });
});

test.describe("a story whose picture was never proven", () => {
  test("it is not dropped as a repeated photograph", () => {
    // It renders NO picture - the card shows its key points instead -
    // so it cannot be showing the same photograph as another story.
    //
    // Dropping it here is what made a freshly fetched story appear at
    // the top of the live feed and vanish a second later: the merge
    // added it, the pass ran again over the merged list, and removed
    // it.
    const rows = [
      {
        id: "a",
        featured_image_url: "https://x.test/same.jpg",
        image_highres_url: null,
        gif_url: null,
        image_verified_at: null,
      },
      {
        id: "b",
        featured_image_url: "https://x.test/same.jpg",
        image_highres_url: null,
        gif_url: null,
        image_verified_at: null,
      },
    ];

    expect(dropRepeatedImages(rows).map((r) => r.id)).toEqual(["a", "b"]);
  });

  test("a PROVEN picture that is repeated is still dropped", () => {
    const rows = [
      {
        id: "a",
        featured_image_url: "https://x.test/same.jpg",
        image_highres_url: null,
        gif_url: null,
        image_verified_at: "2026-10-23T00:00:00Z",
      },
      {
        id: "b",
        featured_image_url: "https://x.test/same.jpg",
        image_highres_url: null,
        gif_url: null,
        image_verified_at: "2026-10-23T00:00:00Z",
      },
    ];

    expect(dropRepeatedImages(rows).map((r) => r.id)).toEqual(["a"]);
  });

  test("an unproven picture does not claim the key a proven one shows", () => {
    const rows = [
      {
        id: "unproven",
        featured_image_url: "https://x.test/photo.jpg",
        image_highres_url: null,
        gif_url: null,
        image_verified_at: null,
      },
      {
        id: "proven",
        featured_image_url: "https://x.test/photo.jpg",
        image_highres_url: null,
        gif_url: null,
        image_verified_at: "2026-10-23T00:00:00Z",
      },
    ];

    expect(dropRepeatedImages(rows).map((r) => r.id)).toEqual([
      "unproven",
      "proven",
    ]);
  });
});
