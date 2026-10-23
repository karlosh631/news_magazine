"use client";

import Link from "next/link";
import { useEffect, useState, type MouseEvent } from "react";

import { OptimizedImage } from "@/components/OptimizedImage";
import { formatNewsDate, type SiteLanguage } from "@/lib/date";
import { cleanHeadline } from "@/lib/headline";
import { withoutSizeSuffix } from "@/lib/image-url";
import { isPlainActivation } from "@/lib/story-dialog";

/**
 * How wide a card's picture is, as the browser should be told.
 *
 * The grid is `grid-cols-1 sm:grid-cols-2 lg:grid-cols-3` inside a
 * `max-w-7xl` with `px-4`, so a card is the full width on a phone, half of
 * it from Tailwind's `sm` (640px), and a third from `lg` (1024px) — which
 * is 400px of 1248 at the widest. `33vw` / `50vw` / `100vw` describes that
 * closely enough for a srcset and is what decides which width the browser
 * downloads.
 *
 * It is the difference between one picture per card and one per card per
 * screen. With `sizes` left at its default of `100vw` the browser picks the
 * 640w rendition for a 400px slot on a desktop — 60% more bytes than the
 * slot can show, on 24 cards, for nothing.
 */
const CARD_IMAGE_SIZES = "(min-width: 1024px) 33vw, (min-width: 640px) 50vw, 100vw";

export type NewsCardArticle = {
  id: string;
  slug: string;
  headline: string;
  excerpt: string | null;
  /**
   * Four points, verbatim from the source article.
   *
   * Shown on the article page in full, and here when the story has no
   * picture: a card with no photograph shows the story's own points
   * where the photograph would be, rather than a grey box.
   */
  summary_points: string[] | null;
  /**
   * The language the outlet published the story in.
   *
   * Optional, and deliberately so: this type is what the cards are
   * given, and some callers build a card from a row that does not
   * carry a language at all.
   */
  language?: "ne" | "en";
  featured_image_url: string | null;
  image_highres_url: string | null;
  /**
   * When this story's picture was confirmed against the story's own
   * page.
   *
   * Null means it never was, and the card then shows no picture at
   * all. Optional, because some callers build a card from a row that
   * does not carry the column — and an ABSENT marker is not the same
   * as a marker that says "not confirmed".
   */
  image_verified_at?: string | null;
  video_url: string | null;
  video_embed_url: string | null;
  audio_url: string | null;
  gif_url: string | null;
  published_at: string | null;
  source_name_snapshot: string | null;
  /** Link out to the original article on the outlet's own site. */
  source_article_url: string | null;
  source_count: number;
  category_slug?: string | null;
  category_name?: string | null;
};

/**
 * Only YouTube/Vimeo embeds are allowed in an iframe.
 *
 * The ingestion worker stores whatever og:video pointed at, and an
 * arbitrary value rendered into <iframe src> would be a script injection
 * vector. Direct .mp4/.webm/.m3u8 URLs are not iframed; they become a
 * <video> element instead.
 */
function embeddableVideo(url: string | null): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.replace(/^www\./, "");
    if (host === "youtube.com" || host === "youtube-nocookie.com" || host === "youtu.be") {
      const id =
        host === "youtu.be"
          ? parsed.pathname.slice(1)
          : (parsed.searchParams.get("v") ??
            parsed.pathname.split("/").filter(Boolean).pop() ??
            "");
      if (id) return `https://www.youtube-nocookie.com/embed/${id}`;
    }
    if (host === "player.vimeo.com") return url;
  } catch {
    return null;
  }
  return null;
}

function directVideo(url: string | null): string | null {
  if (!url || embeddableVideo(url)) return null;
  return /\.(mp4|webm|mov|ogg|ogv)(\?|$)/i.test(url) ? url : null;
}

/**
 * Every usable image URL for this story, best first.
 *
 * A list rather than a single pick, because an outlet CDN can 404 one
 * specific rendition: some articles had a live og:image and a dead
 * image_highres_url, so preferring highres alone rendered a broken tile.
 * The card walks this list and falls through on error.
 *
 * A story with a real photograph must never fall back to a stock photo
 * of newspapers — Stage 1's SafeImage did exactly that, so every article
 * without a feed thumbnail rendered the same unrelated image.
 */
function pickImages(article: NewsCardArticle): string[] {
  /*
    A PICTURE THAT WAS NEVER CONFIRMED IS NOT THIS STORY'S PICTURE
    -----------------------------------------------------------
    The database records, in `image_verified_at`, whether the picture
    was checked against the story's OWN page. When it was not — the
    picture came from the publisher's feed, or from a scan of the page
    — the card must not show it.

    What it looked like in production: the story "What is PHP, and why
    are security flaws putting European government websites at risk?"
    was illustrated with a black-and-white photograph of an elephant
    lifted by an adult elephant's trunk. It was not a broken URL; it
    was a real image, of something else entirely — the "Most Read
    from World" thumbnail that Kathmandu Post puts on every one of its
    article pages. The URL loaded, so nothing on the page could tell
    the reader. The marker can.

    `undefined` (the column was not selected) is NOT treated as
    "unconfirmed", so a caller that does not carry the column is
    unaffected. Only an explicit `null` hides the picture.
  */
  if (article.image_verified_at === null) {
    return [];
  }

  const candidates = [
    article.image_highres_url,
    article.featured_image_url,
    article.gif_url,
  ]
    .map(toHttps)
    .filter((u): u is string => Boolean(u) && /^https?:\/\//i.test(u as string));

  // Add the size-stripped form of each, so a dead "-1200x675" rendition
  // can fall back to the original file at its own path.
  //
  // `withoutSizeSuffix` lives in lib/image-url.ts, not here, because the
  // read path imports it too: lib/feed-images.ts has to decide whether
  // two cards show the same photograph, and it can only do that with the
  // exact rules this fallback uses. Two copies of the regex would drift,
  // and a drift there means the feed compares something other than what
  // the card renders.
  for (const url of [...candidates]) {
    const stripped = withoutSizeSuffix(url);
    if (stripped !== url && !candidates.includes(stripped)) {
      candidates.push(stripped);
    }
  }

  return [...new Set(candidates)];
}

/**
 * Force https on an image URL.
 *
 * A stored `http://` image is blocked twice over on an https page: the
 * browser treats it as mixed content, and the site's own CSP
 * (`img-src 'self' https: data:`) rejects it. The browser already
 * refuses plain http on an https origin, so rewriting to https cannot
 * make a working image worse — it can only turn a blocked one into a
 * rendered one. Every outlet CDN the site uses serves both schemes.
 */
function toHttps(url: string | null | undefined): string | null {
  if (!url) return null;
  return url.trim().replace(/^http:\/\//i, "https://");
}

export function NewsCard({
  article,
  language = "en",
  onImageUnavailable,
  onOpen,
  priority = false,
  isNew = false,
}: {
  article: NewsCardArticle;
  language?: SiteLanguage;
  onImageUnavailable?: (articleId: string) => void;
  /**
   * This story arrived after the page was painted.
   *
   * The card says so, because a live feed that changes silently is
   * indistinguishable from a live feed that is broken: the reader
   * cannot tell "nothing new" from "the new thing is not showing".
   */
  isNew?: boolean;
  /**
   * This card's picture is the page's Largest Contentful Paint.
   *
   * The browser only finds out which picture that is after it has laid
   * the page out, so it starts every image lazily and then goes back
   * for the one that mattered - which on a throttled connection is a
   * visible delay. The FIRST card in a list is known before the first
   * byte of HTML is sent, so its picture is fetched eagerly, at high
   * priority.
   */
  priority?: boolean;
  /**
   * Open the story where the reader already is, instead of navigating
   * to it.
   *
   * The drifting columns on the reels page use this: a headline there
   * opens a dialog over the feed, so the reader does not lose their
   * place in the video. The card stays a link underneath — a modified
   * click still opens a new tab, the address can still be copied, and
   * the middle mouse still does what the middle mouse does.
   */
  onOpen?: (article: NewsCardArticle, trigger: HTMLAnchorElement) => void;
}) {
  const candidates = pickImages(article);
  const [imageIndex, setImageIndex] = useState(0);
  const image = candidates[imageIndex] ?? null;
  const [imageFailed, setImageFailed] = useState(false);

  const embed = embeddableVideo(article.video_embed_url || article.video_url);
  const file = directVideo(article.video_embed_url || article.video_url);

  /*
    A CARD DOES NOT PLAY THE VIDEO. IT SHOWS THE PICTURE OF IT.
    -----------------------------------------------------------
    A story with a YouTube video used to put the YouTube player in the
    card's picture slot. Three things are wrong with that:

      - the card is in a LIST. Twenty-four cards on a homepage, each
        with a third-party player in it, is twenty-four players, each
        with its own script, its own cookies and its own main-thread
        work. The reel card's own comments say the same thing about
        the reels page, and this was the same mistake in the feed.
      - it breaks the best-practices audit: a YouTube frame sets a
        cookie, and Chrome's Issues panel reports it, and Lighthouse
        fails the page for it.
      - it is the single heaviest thing on the page, and the reader
        wanted to READ the news.

    So the card shows the story's picture with a play badge over it,
    and the story's own page plays the video. The card is a link
    either way, and the link is where the reader was going.
  */
  const hasVideo = Boolean(embed || file);

  // Media controls must not navigate the card.
  const handleMediaClick = (e: MouseEvent) => e.stopPropagation();

  // Walk to the next candidate; give up only when they all fail.
  const handleImageError = () => {
    if (imageIndex + 1 < candidates.length) {
      setImageIndex((i) => i + 1);
    } else {
      setImageFailed(true);
    }
  };

  // Every rendition 404'd and this story has no video to stand in for it.
  //
  // The card copes on its own — it shows the story's own key points, or
  // an honest "No image available" panel if there are none — but on a
  // feed that has already pulled a deep window of newer articles, a
  // story with NOTHING to show is a worse answer than simply showing
  // the next story instead. Telling the parent lets it swap this one
  // out; a feed with nothing left to swap back to simply keeps the
  // honest panel.
  //
  // Only fires for a genuinely imageless story. A dead single rendition
  // falls through to the next candidate and never reaches here.
  const hasMedia = Boolean(embed || file);
  const noArtwork = !hasMedia && (imageFailed || candidates.length === 0);

  /*
    THE STORY'S OWN POINTS, IN THE PICTURE SLOT
    -------------------------------------------
    A card with no picture used to show a grey box that said "No image
    available". That is honest and it is also useless: the reader has
    scrolled to a headline and gets told nothing about the story.

    The summarizer already lifted four sentences out of the outlet's
    own article and stored them on the row, verbatim, in the outlet's
    own words. Those sentences ARE the story, in miniature. So when
    there is no picture, the card shows the first of them where the
    picture would be.

    This is the operator's instruction, and it is the right one: a
    story with no picture is fine, a story with a picture that belongs
    to something else is not, and a story with its own key points is
    better than either.
  */
  const points = (article.summary_points ?? []).filter(
    (point): point is string => typeof point === "string" && point.trim().length > 0
  );

  // Nothing at all to put in the slot: no video, no picture, and no
  // points of its own. Only THIS is worth swapping out.
  const nothingToShow = noArtwork && points.length === 0;

  useEffect(() => {
    if (!onImageUnavailable || !nothingToShow) return;
    onImageUnavailable(article.id);
  }, [nothingToShow, onImageUnavailable, article.id]);

  return (
    <div className="group relative flex flex-col overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm transition-all duration-200 hover:-translate-y-1 hover:border-gray-300 hover:shadow-lg">
      <Link
        href={`/article/${article.slug}`}
        onClick={
          onOpen
            ? (event) => {
                /*
                  A modified click is the browser's business: ctrl-click
                  and cmd-click open a new tab, shift-click opens a new
                  window, and a middle-click pastes the link. Those are
                  things the reader asked for explicitly, so they are
                  left alone. Only a plain left click opens the story
                  where the reader is standing.
                */
                if (!isPlainActivation(event)) return;
                event.preventDefault();
                onOpen(article, event.currentTarget);
              }
            : undefined
        }
        // The card is identified so that the story it opens can hand
        // the focus back to it — and to the same story's card in the
        // visible copy of a drifting column, when the one that was
        // clicked is one of that column's hidden repeats.
        data-article-id={onOpen ? article.id : undefined}
        className="flex flex-1 flex-col focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600"
      >
        {/* ================= MEDIA ================= */}
        <div className="relative aspect-video w-full overflow-hidden bg-gray-100">
          {file && !image ? (
            /*
              The story's OWN video file, and only that. A YouTube
              embed is never played here - see the note above - but a
              file the outlet published is the story's own media, and
              it costs the page nothing but a metadata request.
            */
            <video
              src={file}
              poster={image ?? undefined}
              className="h-full w-full object-cover"
              controls
              playsInline
              preload="metadata"
              onClick={handleMediaClick}
            />
          ) : image && !imageFailed ? (
            /*
              The optimizer, where the host is on the allowlist, and the
              plain <img> where it is not — components/OptimizedImage.tsx
              picks between them.

              `onError` is the same handler as before and still walks to the
              next candidate rendition, so a 404 falls through exactly as it
              did when this was a bare <img>. Through the optimizer a dead
              original comes back as a 500 from /_next/image, which the
              browser raises as the same error event.

              `fill` rather than width/height: the picture is laid out by the
              aspect-video box around it, which already reserves the space,
              so the image can never be what moves the page. `referrerPolicy`
              only survives on the fallback path, where the browser really
              is talking to the outlet — several Nepali CDNs refuse a
              request carrying a foreign Referer.
            */
            <OptimizedImage
              src={image}
              alt={article.headline}
              sizes={CARD_IMAGE_SIZES}
              onError={handleImageError}
              referrerPolicy="no-referrer"
              priority={priority}
              className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
            />
          ) : noArtwork && points.length ? (
            /*
              The story's own key points, where the picture would be.

              Lifted verbatim from the outlet's article by the
              summarizer, so this is the story's own words and not
              something written for the card. A reader who cannot see
              a picture still gets the story.

              The label is there so the block is read as what it is —
              the story's points — and not as a caption for a picture
              that failed to load.
            */
            <div className="flex h-full w-full flex-col justify-center gap-1 bg-gradient-to-br from-gray-50 to-gray-100 px-4 py-3 dark:from-gray-900 dark:to-gray-800">
              <span className="text-[9px] font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500">
                Key points
              </span>
              {points.slice(0, 2).map((point, index) => (
                <p
                  key={index}
                  className="line-clamp-2 text-[11px] leading-snug text-gray-600 dark:text-gray-300"
                >
                  {point}
                </p>
              ))}
            </div>
          ) : (
            // No stock photo. A neutral panel says "this story has no
            // artwork", which is honest; a newspaper photo says "this
            // story is about newspapers", which is not.
            <div className="flex h-full w-full items-center justify-center bg-gradient-to-br from-gray-100 to-gray-200 px-4 text-center">
              <span className="line-clamp-2 text-xs font-medium uppercase tracking-wide text-gray-400">
                No image available
              </span>
            </div>
          )}

          {/*
            THE NEW BADGE

            The story arrived after the reader's page was painted. It
            is at the top because it is new, and this says so - a
            silent change is a change nobody notices.
          */}
          {isNew ? (
            <span className="absolute left-3 top-3 z-10 rounded-full bg-blue-600 px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest text-white shadow">
              New
            </span>
          ) : null}

          {/*
            THE PLAY BADGE

            The story has a video, and the card shows the picture of it
            rather than the player. The badge says so, because a card
            with a video and no badge reads as a card with a
            photograph.
          */}
          {hasVideo && image && !imageFailed ? (
            <span
              aria-hidden="true"
              className="pointer-events-none absolute bottom-2 right-2 flex h-9 w-9 items-center justify-center rounded-full bg-black/60 text-white backdrop-blur"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                <path d="M8 5v14l11-7z" />
              </svg>
            </span>
          ) : null}
        </div>

        {/* ================= BODY =================
            The four-point summary is intentionally NOT here. It is shown
            on the article page, where there is room to read it. */}
        <div className="flex flex-1 flex-col justify-between p-4">
          {/*
            THE HEADLINE, AT THE SIZE A HEADLINE IS.

            The operator's note: the headings were very big, and should
            be the same size as the sources' own. A card is a third of
            the page wide and holds a photograph as well, so a headline
            set at a reading size turns the card into a wall of text:
            three lines of it, and the photograph above it is a
            decoration.

            So it is set at body size - 15px on a phone, 16px from the
            `sm` breakpoint - and clamped to three lines. The clamp is
            what keeps a long headline from pushing every card below
            it down the page.

            THE HEIGHT IS FIXED AT THREE LINES, and that is a separate
            thing from the clamp. The clamp says "never more than
            three lines"; it says nothing about fewer. A one-line
            headline therefore made its card a line shorter than its
            neighbour's, and the row of cards was as tall as its
            tallest - so when a new story arrived at the top of the
            feed and took that slot, the cards around it changed size
            and the page moved. Measured on the homepage, that was the
            only layout shift on the page: 0.064, from a card's outlet
            name moving.

            `min-h-[3lh]` reserves the three lines the clamp allows, in
            the headline's own line height, so every card is exactly as
            tall as every other one and a slot that changes hands
            changes its CONTENT and nothing else.

            The SERIF is the site's own voice and stays.
          */}
          <h2 className="line-clamp-3 min-h-[3lh] font-headline text-[15px] font-semibold leading-snug text-gray-900 transition group-hover:text-blue-600 sm:text-base">
            {cleanHeadline(article.headline)}
          </h2>

          {article.audio_url && (
            <div className="mt-3 border-t pt-2" onClick={handleMediaClick}>
              <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-400">
                Audio
              </span>
              <audio
                src={article.audio_url}
                controls
                preload="none"
                className="mt-1 h-8 w-full"
              />
            </div>
          )}

          {/*
            Always the outlet name. This used to swap in "N outlets"
            whenever more than one outlet carried the story, which meant
            the merge count appeared twice on a card — as a green pill
            over the photograph and again here. The pill is gone and the
            count is gone with it; the outlet credit stays because it is
            the attribution that matters.
          */}
          <div className="mt-4 flex items-center justify-between gap-2 border-t border-gray-100 pt-3 text-xs text-gray-400">
            <span className="truncate">{article.source_name_snapshot ?? ""}</span>
            {article.published_at && (
              <time dateTime={article.published_at} className="shrink-0">
                {formatNewsDate(article.published_at, language)}
              </time>
            )}
          </div>
        </div>
      </Link>
    </div>
  );
}