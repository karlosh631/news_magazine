import { expect, test } from "@playwright/test";

import { dropUnconfirmed, showsNothing, storyPoints } from "../lib/card-artwork";
import type { LatestArticle } from "../types/database";

/**
 * Which stories the feed shows.
 *
 * THE RULE
 * --------
 * A picture must belong to the story it is shown with. A story whose
 * picture is missing — because it never had one, or because nobody
 * could confirm that the one it had belongs to it — is not shown at
 * all. The next story down slides into its place.
 *
 * THE TWO QUESTIONS, AND WHY THEY ARE NOT THE SAME
 * ------------------------------------------------
 * "What is this story's picture?" and "what do we show where the
 * picture would be?" are different questions, and they were being
 * answered with the same answer.
 *
 * The first question is answered in the data, before anything is
 * rendered: a story with no picture is dropped. The second is
 * answered in the browser, when a picture that WAS there turns out
 * to be dead — and there the story's own key points are a better
 * answer than a broken tile.
 *
 * Keeping a picture-less story because it happened to have some text
 * turned the feed into a wall of key points. That is what the
 * operator was looking at when they asked for the pictures to be
 * right.
 */

function article(overrides: Partial<LatestArticle> = {}): LatestArticle {
  return {
    id: "a1",
    slug: "a-story",
    headline: "A headline",
    excerpt: null,
    summary_points: null,
    language: "ne",
    featured_image_url: null,
    image_highres_url: null,
    image_verified_at: null,
    video_url: null,
    video_embed_url: null,
    audio_url: null,
    gif_url: null,
    published_at: "2026-10-23T00:00:00Z",
    source_name_snapshot: "An Outlet",
    source_article_url: null,
    source_count: 1,
    ...overrides,
  } as LatestArticle;
}

test.describe("a story with no picture", () => {
  test("it is not shown", () => {
    expect(showsNothing(article())).toBe(true);
  });

  test("it is not shown even when it has key points", () => {
    // The points are the answer to "the picture is dead", not to
    // "the story has no picture".
    expect(
      showsNothing(
        article({ summary_points: ["A point from the article."] })
      )
    ).toBe(true);
  });

  test("it is shown when it has a video", () => {
    expect(showsNothing(article({ video_url: "https://x.test/a.mp4" }))).toBe(
      false
    );
  });
});

test.describe("a story with a picture", () => {
  test("it is shown, and the picture has to be confirmed", () => {
    expect(
      showsNothing(
        article({
          featured_image_url: "https://x.test/photo.jpg",
          image_verified_at: "2026-10-23T00:00:00Z",
        })
      )
    ).toBe(false);
  });

  test("a picture that was never confirmed is not a picture", () => {
    // The marker is null: nobody has proved the picture belongs to
    // this story. The card does not show it, so the feed must not
    // keep the story for it.
    expect(
      showsNothing(
        article({
          featured_image_url: "https://x.test/photo.jpg",
          image_verified_at: null,
        })
      )
    ).toBe(true);
  });

  test("a picture in any of the three columns counts", () => {
    for (const column of [
      "featured_image_url",
      "image_highres_url",
      "gif_url",
    ] as const) {
      expect(
        showsNothing(
          article({
            [column]: "https://x.test/photo.jpg",
            image_verified_at: "2026-10-23T00:00:00Z",
          })
        )
      ).toBe(false);
    }
  });
});

test.describe("the key points", () => {
  test("blank ones are not points", () => {
    expect(storyPoints({ summary_points: ["", "   "] })).toHaveLength(0);
    expect(storyPoints({ summary_points: null })).toHaveLength(0);
    expect(storyPoints({ summary_points: undefined })).toHaveLength(0);
  });

  test("real ones are", () => {
    expect(
      storyPoints({ summary_points: ["One.", "", "Two."] })
    ).toEqual(["One.", "Two."]);
  });
});

test.describe("the confirmation pass, on the server", () => {
  const confirmed = article({
    id: "confirmed",
    featured_image_url: "https://x.test/photo.jpg",
    image_verified_at: "2026-10-23T00:00:00Z",
  });
  const unconfirmed = article({
    id: "unconfirmed",
    featured_image_url: "https://x.test/photo.jpg",
    image_verified_at: null,
  });

  test("a story whose picture was never confirmed is left out", () => {
    expect(
      dropUnconfirmed([confirmed, unconfirmed], showsNothing).map((a) => a.id)
    ).toEqual(["confirmed"]);
  });

  test("the rule runs before the page is sliced, so the slots fill", () => {
    // Twenty-four slots, of which only one story has a confirmed
    // picture: the page shows that one story and no other. The
    // over-fetch is what puts the other twenty-three stories in the
    // window, and this is the pass that lets them through.
    const rows = [
      ...Array.from({ length: 23 }, (_, i) =>
        article({ id: `u${i}`, image_verified_at: null })
      ),
      confirmed,
    ];
    expect(dropUnconfirmed(rows, showsNothing)).toHaveLength(1);
  });

  test("a feed of nothing but unconfirmed stories is still a feed", () => {
    const rows = [unconfirmed, article({ id: "u2", image_verified_at: null })];
    expect(dropUnconfirmed(rows, showsNothing)).toHaveLength(2);
  });

  test("a story with a video is kept even without a picture", () => {
    const video = article({
      id: "video",
      image_verified_at: null,
      video_url: "https://x.test/a.mp4",
    });
    expect(dropUnconfirmed([video, unconfirmed], showsNothing)).toHaveLength(1);
  });
});
