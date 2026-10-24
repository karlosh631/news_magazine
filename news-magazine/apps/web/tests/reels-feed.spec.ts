import { expect, test } from "@playwright/test";

import {
  audioState,
  posterWidthFor,
  sortByArrival,
  storyLink,
  storyMedia,
  WIKIMEDIA_THUMBNAIL_WIDTHS,
  type VideoStory,
} from "@/lib/reel-codec";

/**
 * The reels feed, and the three rules it depends on.
 *
 * THE OPERATOR'S COMPLAINTS, IN THEIR OWN WORDS
 * ----------------------------------------------
 *
 *   "instead of showing reels videos showing news pic fix that must
 *    show the middle reels must only show the video 1440 hq best
 *    audio, and auto updating and always make sure sound is clean"
 *
 * Three of those are pinned here, because they are the three that can
 * be pinned without a browser:
 *
 *   1. THE POSTER IS AT THE WIDTH THE PROVIDER HAS. Wikimedia serves
 *      a fixed ladder of thumbnail widths and answers HTTP 400 for
 *      anything else, so "the best one" is a lookup in that ladder
 *      and not a number anybody chose.
 *
 *   2. A CLIP WITH NO SOUND MUST NOT SHOW A SOUND CONTROL. The
 *      browser knows whether a file has an audio track; the reels
 *      table has no column for it and cannot have one without a
 *      migration.
 *
 *   3. THE NEWEST CLIP IS AT THE HEAD. The feed is read in two
 *      queries, so the two halves are merged by the moment the worker
 *      stored the row.
 *
 * These are plain functions over plain data, so they run in
 * milliseconds without a browser — which is what this repository's
 * suite does.
 */

test.describe("the poster is at the width the provider actually has", () => {
  test("every width on the ladder is one Wikimedia serves", () => {
    // Measured against the live CDN, one width at a time. A width that
    // is not on this ladder answers HTTP 400 with an HTML body, which
    // Chrome blocks outright rather than showing a broken image.
    expect([...WIKIMEDIA_THUMBNAIL_WIDTHS]).toEqual([
      120, 250, 330, 500, 960, 1280, 1920, 3840,
    ]);
  });

  test("a wide clip gets the best rung at or below 1440", () => {
    // 1440 is what the operator asked for and it is NOT a rung: the
    // best rung at or below it is 1280.
    expect(posterWidthFor(1920)).toBe(1280);
    expect(posterWidthFor(3840)).toBe(1280);
    expect(posterWidthFor(1918)).toBe(1280);
  });

  test("a clip narrower than 1080 settles for the rung below it", () => {
    // The best rung at or below 1080 is 960.
    expect(posterWidthFor(1080)).toBe(960);
    expect(posterWidthFor(1000)).toBe(960);
  });

  test("the poster is never wider than the picture it is cut from", () => {
    // Wikimedia's thumb handler will happily scale a 720-wide frame
    // up to 1280 — measured — and a poster that claims detail the
    // footage does not have is a picture of something that is not
    // there.
    for (const source of [320, 640, 720, 854, 960, 1280, 1920, 3840]) {
      const width = posterWidthFor(source);
      expect(width).toBeLessThanOrEqual(source);
      expect(WIKIMEDIA_THUMBNAIL_WIDTHS).toContain(width);
    }
  });

  test("an unknown width is answered conservatively", () => {
    // We do not know how wide the picture is, so the answer is the
    // smaller one: a smaller real picture beats a larger invented one.
    expect(posterWidthFor(null)).toBe(960);
    expect(posterWidthFor(undefined)).toBe(960);
    expect(posterWidthFor(0)).toBe(960);
  });

  test("the smallest rung is used for a picture smaller than all of them", () => {
    expect(posterWidthFor(80)).toBe(120);
  });
});

test.describe("a clip with no sound must not offer a sound control", () => {
  /** A video element stand-in with the properties a browser exposes. */
  function videoElement(properties: Record<string, unknown>) {
    return {
      currentTime: 0,
      ...properties,
    } as unknown as HTMLVideoElement;
  }

  test("Firefox answers outright", () => {
    expect(
      audioState(videoElement({ mozHasAudio: true })),
    ).toBe("yes");
    expect(
      audioState(videoElement({ mozHasAudio: false })),
    ).toBe("no");
  });

  test("a browser that counts decoded audio bytes knows once it has played", () => {
    // Chrome and Safari expose webkitAudioDecodedByteCount.
    const element = videoElement({ webkitAudioDecodedByteCount: 4096 });
    expect(audioState(element)).toBe("yes");

    // Zero only means "silent" once the clip has been playing: before
    // that, nothing has been decoded at all.
    const fresh = videoElement({ webkitAudioDecodedByteCount: 0 });
    expect(audioState(fresh)).toBe("unknown");

    const played = videoElement({ webkitAudioDecodedByteCount: 0 });
    (played as unknown as { currentTime: number }).currentTime = 1.2;
    expect(audioState(played)).toBe("no");
  });

  test("the standard track list is used where an engine has one", () => {
    expect(
      audioState(videoElement({ audioTracks: { length: 1 } })),
    ).toBe("yes");
    expect(
      audioState(videoElement({ audioTracks: { length: 0 } })),
    ).toBe("no");
  });

  test("a browser that says nothing is not a promise either way", () => {
    expect(audioState(videoElement({}))).toBe("unknown");
  });
});

test.describe("a clip built from one of our stories links to the story", () => {
  const STORY_UUID = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d";

  const story = (overrides: Partial<VideoStory> = {}): VideoStory => ({
    id: STORY_UUID,
    slug: "koshi-flood-relief",
    video_url: null,
    video_embed_url: null,
    ...overrides,
  });

  test("the article's uuid is the reel's external_id", () => {
    // That identity is what makes the resolution possible at all: the
    // feed asks the articles table for the stories that carry a video,
    // and matches the reels to them by this id.
    const stories = new Map([[STORY_UUID, story()]]);
    expect(storyLink(STORY_UUID, stories)).toBe("/article/koshi-flood-relief");
  });

  test("a clip that is not one of our stories has no link", () => {
    const stories = new Map([[STORY_UUID, story()]]);
    expect(storyLink("some-other-id", stories)).toBeNull();
    expect(storyLink(null, stories)).toBeNull();
    expect(storyLink("", stories)).toBeNull();
  });

  test("a story with a video of its own is played as that video", () => {
    const media = storyMedia(
      story({ video_url: "https://cdn.example.test/clip.mp4" })
    );
    expect(media.videoUrl).toBe("https://cdn.example.test/clip.mp4");
    // A direct file plays in a <video>; there is no player involved.
    expect(media.embedUrl).toBeNull();
  });

  test("a story with a player is played in an iframe", () => {
    // A player URL is an HTML document, and `<video src>` refuses one.
    const media = storyMedia(
      story({ video_embed_url: "https://www.youtube.com/embed/abc123" })
    );
    expect(media.videoUrl).toBe("https://www.youtube.com/embed/abc123");
    expect(media.embedUrl).toBe("https://www.youtube.com/embed/abc123");
  });

  test("a URL that is not a media file is not a clip", () => {
    // The ingestion stores whatever `og:video` pointed at, and that is
    // sometimes a share page. A `<video>` pointed at an HTML document
    // never plays.
    const media = storyMedia(
      story({ video_url: "https://example.test/watch/the-story" })
    );
    expect(media.videoUrl).toBeNull();
    expect(media.embedUrl).toBeNull();
  });

  test("a story with no video at all is not a clip", () => {
    const media = storyMedia(story());
    expect(media.videoUrl).toBeNull();
    expect(media.embedUrl).toBeNull();
  });
});

test.describe("the newest clip is at the head of the feed", () => {  test("the order is the moment the worker stored the row", () => {
    const reels = [
      { id: "old", ingested_at: "2026-10-20T10:00:00Z", published_at: null },
      { id: "new", ingested_at: "2026-10-24T10:00:00Z", published_at: null },
      { id: "middle", ingested_at: "2026-10-22T10:00:00Z", published_at: null },
    ];

    expect(sortByArrival(reels).map((reel) => reel.id)).toEqual([
      "new",
      "middle",
      "old",
    ]);
  });

  test("two clips stored in the same second keep a stable order", () => {
    // Providers publish at whole-second resolution, so thirty clips
    // routinely share a timestamp. Without a second key the order
    // would shuffle between renders and the same clip would appear on
    // two pages.
    const reels = [
      {
        id: "a",
        ingested_at: "2026-10-24T10:00:00Z",
        published_at: "2026-01-02T03:04:05Z",
      },
      {
        id: "b",
        ingested_at: "2026-10-24T10:00:00Z",
        published_at: "2026-01-02T03:04:06Z",
      },
    ];

    expect(sortByArrival(reels).map((reel) => reel.id)).toEqual(["b", "a"]);
    // And it is the same order every time.
    expect(sortByArrival([...reels].reverse()).map((reel) => reel.id)).toEqual([
      "b",
      "a",
    ]);
  });

  test("a row with no timestamp does not jump the queue", () => {
    const reels = [
      { id: "undated", ingested_at: null, published_at: null },
      { id: "dated", ingested_at: "2026-10-24T10:00:00Z", published_at: null },
    ];

    expect(sortByArrival(reels).map((reel) => reel.id)).toEqual([
      "dated",
      "undated",
    ]);
  });
});
