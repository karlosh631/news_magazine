import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { createServerSupabaseClient } from "@/lib/supabase/server";
import { ShareButtons } from "@/components/ShareButtons";

interface Props {
  params: { slug: string };
}

async function getArticle(slug: string) {
  const supabase = createServerSupabaseClient();
  const { data } = await supabase
    .from("articles")
    .select("*")
    .eq("slug", slug)
    .eq("status", "published")
    .single();
  return data;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const article = await getArticle(params.slug);
  if (!article) return {};

  return {
    title: article.headline,
    description: article.excerpt ?? undefined,
    alternates: { canonical: `/article/${article.slug}` },
    openGraph: {
      type: "article",
      title: article.headline,
      description: article.excerpt ?? undefined,
      images: article.featured_image_url ? [article.featured_image_url] : undefined,
      publishedTime: article.published_at ?? undefined,
    },
    twitter: { card: "summary_large_image", title: article.headline },
  };
}

export default async function ArticlePage({ params }: Props) {
  const article = await getArticle(params.slug);
  if (!article) notFound();

  const supabase = createServerSupabaseClient();
  // Fire-and-forget view increment via the SECURITY DEFINER function —
  // never trusts a client-supplied view count.
  void supabase.rpc("increment_article_view", { p_article_id: article.id });

  const structuredData = {
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    headline: article.headline,
    datePublished: article.published_at,
    dateModified: article.updated_at,
    image: article.featured_image_url ? [article.featured_image_url] : undefined,
  };

  return (
    <article className="max-w-3xl mx-auto px-4 py-8">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(structuredData) }}
      />
      <h1 className="text-3xl md:text-4xl font-headline font-bold leading-tight">
        {article.headline}
      </h1>
      {article.subtitle && (
        <p className="mt-2 text-lg text-gray-600">{article.subtitle}</p>
      )}

      {article.featured_image_url && (
        // eslint-disable-next-line @next/next/no-img-element -- swap for next/image once remotePatterns are configured per source
        <img
          src={article.featured_image_url}
          alt={article.featured_image_alt ?? ""}
          className="mt-6 w-full rounded-lg"
        />
      )}

      {/* Body: full_body_html only exists when the source explicitly
          granted republish permission; otherwise we show the permitted
          excerpt plus a clear, prominent link back to the original. */}
      {article.body_html ? (
        <div
          className="prose prose-lg mt-6 max-w-none"
          // body_html is sanitized server-side at ingestion/publish time
          // (see apps/ingestion/app/services/sanitizer.py) — never render
          // raw external HTML that hasn't passed through that step.
          dangerouslySetInnerHTML={{ __html: article.body_html }}
        />
      ) : (
        <div className="mt-6 space-y-4">
          <p className="text-lg">{article.excerpt}</p>
          {article.source_article_url && (
            <a
              href={article.source_article_url}
              target="_blank"
              rel="noopener noreferrer nofollow"
              className="inline-block font-semibold underline"
            >
              Read the full story at {article.source_name_snapshot ?? "the original source"} →
            </a>
          )}
        </div>
      )}

      <ShareButtons url={`/article/${article.slug}`} title={article.headline} />
    </article>
  );
}
