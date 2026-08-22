"use client";

import Link from "next/link";
import Image from "next/image";
import { useState } from "react";
import type { Article } from "@/types/database";

type NewsCardArticle = Pick<
  Article,
  | "id"
  | "slug"
  | "headline"
  | "excerpt"
  | "featured_image_url"
  | "published_at"
>;

const FALLBACK_IMAGE = "/default-news.jpg";

export function NewsCard({
  article,
}: {
  article: NewsCardArticle;
}) {
  return (
    <Link
      href={`/article/${article.slug}`}
      className="group block"
      aria-label={`Read: ${article.headline}`}
    >
      {/* IMAGE */}
      <div className="relative aspect-video overflow-hidden rounded-md bg-gray-100">
        <NewsCardImage
          src={article.featured_image_url}
          alt={article.headline}
        />
      </div>

      {/* HEADLINE */}
      <h3 className="mt-3 font-headline text-lg font-semibold leading-snug group-hover:underline">
        {article.headline}
      </h3>

      {/* EXCERPT */}
      {article.excerpt && (
        <p className="mt-1 line-clamp-2 text-sm text-gray-600">
          {article.excerpt}
        </p>
      )}

      {/* DATE */}
      {article.published_at && (
        <time
          className="mt-1 block text-xs text-gray-400"
          dateTime={article.published_at}
        >
          {formatNewsDate(article.published_at)}
        </time>
      )}
    </Link>
  );
}

/* =========================================================
   IMAGE
   ========================================================= */

function NewsCardImage({
  src,
  alt,
}: {
  src: string | null;
  alt: string;
}) {
  const [imageSrc, setImageSrc] = useState(
    src || FALLBACK_IMAGE
  );

  return (
    <Image
      src={imageSrc}
      alt={alt}
      fill
      sizes="(max-width: 768px) 100vw, (max-width: 1200px) 50vw, 33vw"
      className="object-cover transition-transform duration-300 group-hover:scale-105"
      loading="lazy"
      onError={() => {
        if (imageSrc !== FALLBACK_IMAGE) {
          setImageSrc(FALLBACK_IMAGE);
        }
      }}
    />
  );
}

/* =========================================================
   DATE
   ========================================================= */

function formatNewsDate(date: string) {
  try {
    return new Intl.DateTimeFormat("ne-NP", {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: "Asia/Kathmandu",
    }).format(new Date(date));
  } catch {
    return date;
  }
}
