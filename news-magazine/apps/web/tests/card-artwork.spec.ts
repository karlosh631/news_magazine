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

  test("a picture that was never confirmed is not shown, but the story is", () => {
    // The marker is null: nobody has proved the picture belongs to
    // this story. The CARD refuses to render it (see
    // NewsCard.pickImages), so the feed must not keep the story FOR
    // the picture - but the story is still news, and it is still
    // shown. The card puts the story's own key points where the
    // picture would have been.
    expect(
      showsNothing(
        article({
          featured_image_url: "https://x.test/photo.jpg",
          image_verified_at: null,
        })
      )
    ).toBe(false);
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

  test("a story whose picture was never confirmed is still shown", () => {
    // The picture is not shown; the story is. 278 stories were lost
    // this way - every one of them from a publisher whose pages are
    // rendered in JavaScript, so the picture could not be proven and
    // the story went with it.
    expect(
      dropUnconfirmed([confirmed, unconfirmed], showsNothing).map((a) => a.id)
    ).toEqual(["confirmed", "unconfirmed"]);
  });

  test("the rule runs before the page is sliced, so the slots fill", () => {
    // Twenty-four slots, of which only one story has a confirmed
    // picture. The over-fetch is what puts the other twenty-three
    // stories in the window, and this is the pass that decides which
    // of them the page shows. A story with no picture at all is still
    // left out; a story whose PICTURE is unproven is not.
    const rows = [
      ...Array.from({ length: 23 }, (_, i) =>
        article({
          id: `u${i}`,
          image_verified_at: null,
          featured_image_url: "https://x.test/photo.jpg",
        })
      ),
      confirmed,
    ];
    expect(dropUnconfirmed(rows, showsNothing)).toHaveLength(24);
  });

  test("a feed of nothing but empty stories is still a feed", () => {
    // The floor: two stories with no picture and no video would both
    // be dropped, and a page with nothing on it is worse than a page
    // of honest empty cards.
    const rows = [
      article({ id: "bare-1" }),
      article({ id: "bare-2" }),
    ];
    expect(dropUnconfirmed(rows, showsNothing)).toHaveLength(2);
  });

  test("a story with a video is kept even without a picture", () => {
    const video = article({
      id: "video",
      image_verified_at: null,
      video_url: "https://x.test/a.mp4",
    });
    expect(dropUnconfirmed([video, unconfirmed], showsNothing)).toHaveLength(2);
  });

  test("a story with no picture at all is still left out", () => {
    // The rule that is NOT about the marker: a story with no picture,
    // no video and nothing to show is the one the feed may swap for
    // another story.
    const bare = article({ id: "bare", image_verified_at: null });
    expect(dropUnconfirmed([confirmed, bare], showsNothing)).toHaveLength(1);
  });
});
