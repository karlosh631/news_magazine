import { expect, test } from "@playwright/test";
import { createElement } from "react";
import { renderToString } from "react-dom/server";

import { dropImageless } from "../lib/feed-images";
import { loadComponent } from "./support/tsx-module";

/**
 * A card with no picture.
 *
 * THE BUG THIS EXISTS FOR
 * -----------------------
 * The operator's brief, repeated: never show a picture that belongs to
 * another story, and it is perfectly fine for a story to have no
 * picture at all. The card used to answer a missing picture with a grey
 * panel that said "No image available" — honest, and useless, because
 * the reader scrolled to a headline and got told nothing about the
 * story.
 *
 * The summarizer already lifted four sentences out of the outlet's own
 * article and stored them on the row. Those sentences ARE the story, in
 * miniature, in the outlet's own words. So the card now shows them
 * where the picture would be.
 *
 * The other half of the brief is that a picture which is merely
 * BROKEN is treated the same as one that is missing: the reader cannot
 * tell the difference, and neither can the card.
 */

const { NewsCard } = loadComponent<
  typeof import("@/components/NewsCard")
>("@/components/NewsCard");

type CardArticle = Parameters<typeof NewsCard>[0]["article"];

function article(overrides: Partial<CardArticle> = {}): CardArticle {
  return {
    id: "a1",
    slug: "a-story",
    headline: "What is PHP, and why are security flaws putting European government websites at risk?",
    excerpt: null,
    summary_points: null,
    featured_image_url: null,
    image_highres_url: null,
    video_url: null,
    video_embed_url: null,
    audio_url: null,
    gif_url: null,
    published_at: "2026-10-22T13:11:00Z",
    source_name_snapshot: "Kantipur",
    source_article_url: null,
    source_count: 1,
    ...overrides,
  } as CardArticle;
}

function render(overrides: Partial<CardArticle> = {}): string {
  return renderToString(
    createElement(NewsCard, { article: article(overrides) })
  );
}

test.describe("a card with no picture", () => {
  test("it shows the story's own key points", () => {
    const html = render({
      summary_points: [
        "नेपालले आज पनि विदेशी मुद्राको सटोर बढाउने निर्णय गर्‍यो।",
        "The central bank said the intervention would continue until the rate stabilised.",
      ],
    });

    // The points are there, in the card.
    expect(html).toContain("नेपालले आज पनि विदेशी मुद्राको सटोर बढाउने निर्णय गर्‍यो।");
    expect(html).toContain(
      "The central bank said the intervention would continue until the rate stabilised."
    );

    // And the grey panel is not.
    expect(html).not.toContain("No image available");
  });

  test("it says what the block is, so it is not read as a caption", () => {
    const html = render({ summary_points: ["A point from the article."] });

    expect(html).toContain("Key points");
  });

  test("with no points of its own it still says so honestly", () => {
    const html = render({ summary_points: null });

    expect(html).toContain("No image available");
  });

  test("blank points are not points", () => {
    const html = render({ summary_points: ["", "   ", null as never] });

    expect(html).toContain("No image available");
  });

  test("a card with a picture does not also show the points", () => {
    const html = render({
      featured_image_url: "https://x.test/a-real-photograph.jpg",
      summary_points: ["A point from the article."],
    });

    expect(html).toContain("a-real-photograph");
    expect(html).not.toContain("Key points");
  });
});

test.describe("the server-side pass", () => {
  test("a story with no picture is dropped, points or not", () => {
    // The operator's rule: a story whose picture is missing is a
    // story whose picture was not found, and it does not go on the
    // page. The card's own key points are a better card than a grey
    // box, but they are not a reason to keep a story whose picture
    // the pipeline could not place.
    const rows = [
      {
        id: "a",
        featured_image_url: null,
        image_highres_url: null,
        gif_url: null,
        summary_points: ["A point from the article."],
      },
      {
        id: "b",
        featured_image_url: "https://x.test/photo.jpg",
        image_highres_url: null,
        gif_url: null,
        summary_points: null,
      },
    ];

    const kept = dropImageless(rows);

    expect(kept.map((r) => r.id)).toEqual(["b"]);
  });

  test("a story with no picture and no points is dropped", () => {
    const rows = [
      {
        id: "a",
        featured_image_url: null,
        image_highres_url: null,
        gif_url: null,
        summary_points: null,
      },
      {
        id: "b",
        featured_image_url: "https://x.test/photo.jpg",
        image_highres_url: null,
        gif_url: null,
        summary_points: null,
      },
    ];

    const kept = dropImageless(rows);

    expect(kept.map((r) => r.id)).toEqual(["b"]);
  });

  test("a page of nothing but empty cards is still a page", () => {
    // The floor: an empty page because the site decided pictures
    // matter more than news is a worse answer than an honest panel.
    const rows = [
      {
        id: "a",
        featured_image_url: null,
        image_highres_url: null,
        gif_url: null,
        summary_points: null,
      },
      {
        id: "b",
        featured_image_url: null,
        image_highres_url: null,
        gif_url: null,
        summary_points: null,
      },
    ];

    expect(dropImageless(rows)).toHaveLength(2);
  });
});
