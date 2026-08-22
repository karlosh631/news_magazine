import Link from "next/link";
import Image from "next/image";
import type { Article } from "@/types/database";

export function NewsCard({ article }: { article: Pick<Article, "id" | "slug" | "headline" | "excerpt" | "featured_image_url" | "published_at"> }) {
  return (
    <Link href={`/article/${article.slug}`} className="group block">
      {article.featured_image_url && (
        <div className="relative aspect-video overflow-hidden rounded-md bg-gray-100">
          <Image
            src={article.featured_image_url}
            alt=""
            fill
            sizes="(max-width: 768px) 100vw, 33vw"
            className="object-cover transition-transform group-hover:scale-105"
            loading="lazy"
          />
        </div>
      )}
      <h3 className="mt-3 font-headline text-lg font-semibold leading-snug group-hover:underline">
        {article.headline}
      </h3>
      {article.excerpt && (
        <p className="mt-1 text-sm text-gray-600 line-clamp-2">{article.excerpt}</p>
      )}
      {article.published_at && (
        <time className="mt-1 block text-xs text-gray-400" dateTime={article.published_at}>
          {new Date(article.published_at).toLocaleString("ne-NP")}
        </time>
      )}
    </Link>
  );
}
