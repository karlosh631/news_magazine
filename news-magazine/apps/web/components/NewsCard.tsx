import Link from "next/link";
import Image from "next/image";
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
      {/* -------------------------------------------------- */}
      {/* IMAGE */}
      {/* -------------------------------------------------- */}
      <div className="relative aspect-video overflow-hidden rounded-md bg-gray-100">
        <NewsCardImage
          src={article.featured_image_url}
          alt={article.headline}
        />
      </div>

      {/* -------------------------------------------------- */}
      {/* HEADLINE */}
      {/* -------------------------------------------------- */}
      <h3 className="mt-3 font-headline text-lg font-semibold leading-snug group-hover:underline">
        {article.headline}
      </h3>

      {/* -------------------------------------------------- */}
      {/* EXCERPT */}
      {/* -------------------------------------------------- */}
      {article.excerpt && (
        <p className="mt-1 line-clamp-2 text-sm text-gray-600">
          {article.excerpt}
        </p>
      )}

      {/* -------------------------------------------------- */}
      {/* DATE */}
      {/* -------------------------------------------------- */}
      {article.published_at && (
        <time
          className="mt-1 block text-xs text-gray-400"
          dateTime={article.published_at}
        >
          {new Date(article.published_at).toLocaleString("ne-NP")}
        </time>
      )}
    </Link>
  );
}

/* =========================================================
   IMAGE COMPONENT
   ========================================================= */

function NewsCardImage({
  src,
  alt,
}: {
  src: string | null;
  alt: string;
}) {
  const fallbackImage = "/default-news.jpg";

  return (
    <Image
      src={src || fallbackImage}
      alt={alt}
      fill
      sizes="(max-width: 768px) 100vw, (max-width: 1200px) 50vw, 33vw"
      className="object-cover transition-transform duration-300 group-hover:scale-105"
      loading="lazy"
      onError={(event) => {
        const image = event.currentTarget;

        // Prevent an infinite fallback loop.
        if (image.src.endsWith(fallbackImage)) {
          return;
        }

        image.src = fallbackImage;
      }}
    />
  );
}
