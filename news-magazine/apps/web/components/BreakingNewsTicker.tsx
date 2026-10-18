import Link from "next/link";

/**
 * Breaking-news ticker.
 *
 * Reads real articles with is_breaking set by the ingestion worker.
 * Stage 1 rendered this from /api/live-news, so it showed whatever the
 * Gemini synthesis happened to emit rather than anything an editor or
 * the pipeline had actually flagged.
 */
export type TickerArticle = {
  id: string;
  slug: string;
  headline: string;
};

export function BreakingNewsTicker({
  articles,
}: {
  articles: TickerArticle[];
}) {
  if (!articles || articles.length === 0) return null;

  return (
    <section
      aria-label="Breaking news"
      className="mb-6 overflow-hidden rounded-lg border border-red-200 bg-red-50"
    >
      <div className="flex items-center gap-3 border-b border-red-200 px-4 py-2">
        <span className="inline-flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-red-700">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-500 opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-red-600" />
          </span>
          Breaking
        </span>
      </div>
      <ul className="divide-y divide-red-100">
        {articles.map((article) => (
          <li key={article.id} className="px-4 py-2.5">
            <Link
              href={`/article/${article.slug}`}
              className="text-sm font-medium text-gray-800 hover:text-red-700 hover:underline"
            >
              {article.headline}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}