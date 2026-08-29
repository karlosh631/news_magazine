"use client";

import Link from "next/link";
import { MouseEvent } from "react";

export type NewsCardArticle = {
  id: string;
  slug: string;
  headline: string;
  excerpt: string | null;
  featured_image_url: string | null;
  video_url?: string | null;
  audio_url?: string | null;
  gif_url?: string | null;
  published_at: string | null;
  sector?: string;
};

const FALLBACK_IMAGE =
  "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=1200&q=80";

function formatNewsDate(dateString: string | null): string {
  if (!dateString) return "";
  try {
    const date = new Date(dateString);
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat("ne-NP", {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: "Asia/Kathmandu",
    }).format(date);
  } catch {
    return dateString;
  }
}

export function NewsCard({ article }: { article: NewsCardArticle }) {
  const imageSrc = article.gif_url || article.featured_image_url || FALLBACK_IMAGE;

  // Prevents media control clicks (play/pause/seek) from navigating the parent Link component
  const handleMediaClick = (e: MouseEvent) => {
    e.stopPropagation();
  };

  return (
    <div className="group flex flex-col overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm transition-all duration-200 hover:-translate-y-1 hover:border-gray-300 hover:shadow-lg">
      <Link href={`/article/${article.slug}`} className="flex flex-1 flex-col">
        {/* ================= MEDIA CONTAINER ================= */}
        <div className="relative aspect-video w-full overflow-hidden bg-gray-100">
          {article.video_url ? (
            <video
              src={article.video_url}
              poster={article.featured_image_url || undefined}
              className="h-full w-full object-cover transition duration-300 group-hover:scale-105"
              muted
              loop
              playsInline
              controls
              onClick={handleMediaClick}
            />
          ) : (
            /* Using standard img tag to bypass Next.js hostname whitelist issues for scraped content */
            <img
              src={imageSrc}
              alt={article.headline}
              loading="lazy"
              className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
              onError={(e) => {
                const target = e.currentTarget;
                if (target.src !== FALLBACK_IMAGE) {
                  target.src = FALLBACK_IMAGE;
                }
              }}
            />
          )}

          {/* BADGES (GIF & SECTOR) */}
          <div className="absolute left-2 top-2 flex flex-wrap gap-1.5">
            {article.sector && (
              <span className="rounded bg-black/75 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white backdrop-blur-sm">
                {article.sector}
              </span>
            )}
            {article.gif_url && !article.video_url && (
              <span className="rounded bg-blue-600/90 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-white backdrop-blur-sm">
                GIF
              </span>
            )}
          </div>
        </div>

        {/* ================= CARD BODY ================= */}
        <div className="flex flex-1 flex-col justify-between p-4">
          <div>
            <h2 className="font-headline text-base font-bold leading-snug text-gray-900 transition group-hover:text-blue-600 sm:text-lg">
              {article.headline}
            </h2>

            {article.excerpt && (
              <p className="mt-2 line-clamp-3 text-sm text-gray-600">
                {article.excerpt}
              </p>
            )}
          </div>

          {/* AUDIO PLAYER */}
          {article.audio_url && (
            <div className="mt-3 border-t pt-2" onClick={handleMediaClick}>
              <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-400">
                Audio Recording
              </span>
              <audio
                src={article.audio_url}
                controls
                className="mt-1 h-8 w-full"
              />
            </div>
          )}

          {/* FOOTER & DATE */}
          <div className="mt-4 flex items-center justify-between border-t border-gray-100 pt-3 text-xs text-gray-400">
            {article.published_at && (
              <time dateTime={article.published_at}>
                {formatNewsDate(article.published_at)}
              </time>
            )}
            <span className="font-semibold text-blue-600 group-hover:underline">
              Read More →
            </span>
          </div>
        </div>
      </Link>
    </div>
  );
}
