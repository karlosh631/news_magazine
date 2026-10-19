"use client";

import Link from "next/link";
import { useEffect, useState, type MouseEvent } from "react";

import { formatNewsDate, type SiteLanguage } from "@/lib/date";

export type NewsCardArticle = {
  id: string;
  slug: string;
  headline: string;
  excerpt: string | null;
  /** Four points, verbatim from the source article. Shown on the article page, not here. */
  summary_points: string[] | null;
  featured_image_url: string | null;
  image_highres_url: string | null;
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
  const candidates = [
    article.image_highres_url,
    article.featured_image_url,
    article.gif_url,
  ].filter((u): u is string => Boolean(u) && /^https?:\/\//i.test(u as string));

  // Add the size-stripped form of each, so a dead "-1200x675" rendition
  // can fall back to the original file at its own path.
  for (const url of [...candidates]) {
    const stripped = withoutSizeSuffix(url);
    if (stripped !== url && !candidates.includes(stripped)) {
      candidates.push(stripped);
    }
  }

  return [...new Set(candidates)];
}

/** Strip a width suffix so a dead "-1200x675" rendition falls back to its source. */
function withoutSizeSuffix(url: string): string {
  return url
    .replace(/[-_]\d{2,4}x\d{2,4}(?=\.(?:jpg|jpeg|png|webp))/i, "")
    .replace(/[-_](?:w|width|resize)[-_]?\d{2,4}(?=[x_.]|\.(?:jpg|jpeg|png|webp))/i, "")
    .replace(/[?&]w=\d{2,4}\b/i, "");
}

export function NewsCard({
  article,
  language = "en",
  onImageUnavailable,
}: {
  article: NewsCardArticle;
  language?: SiteLanguage;
  onImageUnavailable?: (articleId: string) => void;
}) {
  const candidates = pickImages(article);
  const [imageIndex, setImageIndex] = useState(0);
  const image = candidates[imageIndex] ?? null;
  const [imageFailed, setImageFailed] = useState(false);

  const embed = embeddableVideo(article.video_embed_url || article.video_url);
  const file = directVideo(article.video_embed_url || article.video_url);

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
  // The card copes on its own — it renders an honest "No image available"
  // panel — but on a feed that has already pulled a deep window of newer
  // articles, that panel is a worse answer than simply showing the next
  // story instead. Telling the parent lets it swap this one out; a feed
  // with nothing left to swap back to simply keeps the honest panel.
  //
  // Only fires for a genuinely imageless story. A dead single rendition
  // falls through to the next candidate and never reaches here.
  const hasMedia = Boolean(embed || file);
  const noArtwork = !hasMedia && (imageFailed || candidates.length === 0);

  useEffect(() => {
    if (!onImageUnavailable || !noArtwork) return;
    onImageUnavailable(article.id);
  }, [noArtwork, onImageUnavailable, article.id]);

  return (
    <div className="group relative flex flex-col overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm transition-all duration-200 hover:-translate-y-1 hover:border-gray-300 hover:shadow-lg">
      <Link
        href={`/article/${article.slug}`}
        className="flex flex-1 flex-col focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600"
      >
        {/* ================= MEDIA ================= */}
        <div className="relative aspect-video w-full overflow-hidden bg-gray-100">
          {embed ? (
            <iframe
              src={embed}
              title={article.headline}
              className="h-full w-full"
              allow="accelerometer; clipboard-write; encrypted-media; picture-in-picture"
              allowFullScreen
              loading="lazy"
            />
          ) : file ? (
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
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={image}
              alt={article.headline}
              loading="lazy"
              // Several Nepali CDNs refuse a request carrying a foreign
              // Referer; sending none lets their images through.
              referrerPolicy="no-referrer"
              onError={handleImageError}
              className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
            />
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
        </div>

        {/* ================= BODY =================
            The four-point summary is intentionally NOT here. It is shown
            on the article page, where there is room to read it. */}
        <div className="flex flex-1 flex-col justify-between p-4">
          <h2 className="font-headline text-base font-bold leading-snug text-gray-900 transition group-hover:text-blue-600 sm:text-lg">
            {article.headline}
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