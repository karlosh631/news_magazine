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

  test("the second card loses its picture and keeps its story", () => {
    const rows = [
      row("a", "https://assets-cdn.kathmandupost.com/uploads/source/news/2026/third-party/kbktmrss04u3a81321211020260848571000x0-1792569245.jpg"),
      row("b", "https://english.nepalnews.com/wp-content/uploads/2026/10/KB_-KTM_-RSS_-04_-_U3A8132-1-768x512-1.jpeg"),
    ];

    const kept = dropRepeatedImages(rows);

    // BOTH stories are still on the page. A duplicate picture is not a
    // reason to lose a story.
    expect(kept).toHaveLength(2);

    // The first keeps its picture.
    expect(kept[0]!.featured_image_url).toContain("kbktmrss04u3a8132");

    // The second shows the honest "no image" state.
    expect(kept[1]!.featured_image_url).toBeNull();
    expect(kept[1]!.image_highres_url).toBeNull();
    expect(kept[1]!.gif_url).toBeNull();
    expect(kept[1]!.headline).toBe("Story b");
  });

  test("every artwork column is cleared, so the picture cannot come back", () => {
    // The card walks highres -> featured -> gif and falls through on a
    // 404. Leaving the gif in place would let the same picture back in
    // through a different door.
    const rows = [
      row("a", "https://x.test/dashaintika-1024x624.jpg"),
      row("b", "https://y.test/dashaintika-1024x624-1.jpg", {
        gif_url: "https://y.test/dashaintika.gif",
      }),
    ];

    const kept = dropRepeatedImages(rows);
    expect(kept[1]!.gif_url).toBeNull();
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
    expect(kept).toHaveLength(2);
    expect(kept[1]!.featured_image_url).toBeNull();
  });

  test("order is never changed", () => {
    const rows = [
      row("a", "https://x.test/kathmandu-dashain-tika.jpg"),
      row("b", "https://y.test/pokhara-lake-morning.jpg"),
      row("c", "https://z.test/kathmandu-dashain-tika.jpg"),
    ];

    const kept = dropRepeatedImages(rows);
    expect(kept.map((r) => r.id)).toEqual(["a", "b", "c"]);
  });

  test("a single row is returned untouched", () => {
    const rows = [row("a", "https://x.test/one.jpg")];
    expect(dropRepeatedImages(rows)).toBe(rows);
  });
});
