import Link from "next/link";
import Image from "next/image";
import { notFound } from "next/navigation";
import type { Metadata } from "next";

import { createServerSupabaseClient } from "@/lib/supabase/server";
import { ShareButtons } from "@/components/ShareButtons";

export const revalidate = 60;

/* =========================================================
   TYPES
   ========================================================= */

interface Article {
  id: string;
  slug: string;
  headline: string;
  subtitle?: string | null;
  excerpt?: string | null;

  body_html?: string | null;

  featured_image_url?: string | null;
  featured_image_alt?: string | null;

  published_at?: string | null;
  updated_at?: string | null;

  author_name?: string | null;
  author_id?: string | null;

  primary_category_id?: string | null;

  source_id?: string | null;
  source_article_url?: string | null;
  source_name_snapshot?: string | null;

  video_url?: string | null;
  video_type?: string | null;
  video_thumbnail_url?: string | null;

  canonical_url?: string | null;
}

/* =========================================================
   PARAMS
   ========================================================= */

interface Props {
  params: Promise<{
    slug: string;
  }>;
}

/* =========================================================
   GET ARTICLE
   ========================================================= */

async function getArticle(
  slug: string
): Promise<Article | null> {
  const supabase = createServerSupabaseClient();

  const { data, error } = await supabase
    .from("articles")
    .select("*")
    .eq("slug", slug)
    .eq("status", "published")
    .maybeSingle();

  if (error) {
    console.error(
      "[Article] Failed to load article:",
      error
    );

    return null;
  }

  return data as Article | null;
}

/* =========================================================
   GET CATEGORY
   ========================================================= */

async function getCategory(
  categoryId?: string | null
) {
  if (!categoryId) {
    return null;
  }

  const supabase = createServerSupabaseClient();

  const { data, error } = await supabase
    .from("categories")
    .select("id, slug, name")
    .eq("id", categoryId)
    .maybeSingle();

  if (error) {
    console.error(
      "[Article] Failed to load category:",
      error
    );

    return null;
  }

  return data;
}

/* =========================================================
   GET RELATED NEWS
   ========================================================= */

async function getRelatedArticles(
  categoryId: string | null | undefined,
  articleId: string
) {
  if (!categoryId) {
    return [];
  }

  const supabase = createServerSupabaseClient();

  const { data, error } = await supabase
    .from("articles")
    .select(
      "id, slug, headline, excerpt, featured_image_url, published_at"
    )
    .eq("status", "published")
    .eq("primary_category_id", categoryId)
    .neq("id", articleId)
    .order("published_at", {
      ascending: false,
      nullsFirst: false,
    })
    .limit(6);

  if (error) {
    console.error(
      "[Article] Failed to load related articles:",
      error
    );

    return [];
  }

  return data ?? [];
}

/* =========================================================
   METADATA
   ========================================================= */

export async function generateMetadata({
  params,
}: Props): Promise<Metadata> {
  const { slug } = await params;

  const article = await getArticle(slug);

  if (!article) {
    return {
      title: "Article Not Found",
    };
  }

  const description =
    article.excerpt ||
    article.subtitle ||
    article.headline;

  const canonical =
    article.canonical_url ||
    `/article/${article.slug}`;

  return {
    title: article.headline,

    description,

    alternates: {
      canonical,
    },

    openGraph: {
      type: "article",

      title: article.headline,

      description,

      url: canonical,

      publishedTime:
        article.published_at ?? undefined,

      modifiedTime:
        article.updated_at ?? undefined,

      authors: article.author_name
        ? [article.author_name]
        : undefined,

      images: article.featured_image_url
        ? [
            {
              url: article.featured_image_url,
              alt:
                article.featured_image_alt ||
                article.headline,
            },
          ]
        : undefined,
    },

    twitter: {
      card: "summary_large_image",
      title: article.headline,
      description,

      images: article.featured_image_url
        ? [article.featured_image_url]
        : undefined,
    },
  };
}

/* =========================================================
   DATE FORMATTER
   ========================================================= */

function formatDate(
  date: string | null | undefined
) {
  if (!date) {
    return null;
  }

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

/* =========================================================
   ARTICLE PAGE
   ========================================================= */

export default async function ArticlePage({
  params,
}: Props) {
  const { slug } = await params;

  const article = await getArticle(slug);

  if (!article) {
    notFound();
  }

  /* -------------------------------------------------------
     Fetch additional article information
     ------------------------------------------------------- */

  const [category, relatedArticles] =
    await Promise.all([
      getCategory(article.primary_category_id),
      getRelatedArticles(
        article.primary_category_id,
        article.id
      ),
    ]);

  /* -------------------------------------------------------
     Increment views
     ------------------------------------------------------- */

  const supabase = createServerSupabaseClient();

  void supabase.rpc(
    "increment_article_view",
    {
      p_article_id: article.id,
    }
  );

  /* -------------------------------------------------------
     Structured data
     ------------------------------------------------------- */

  const structuredData = {
    "@context": "https://schema.org",
    "@type": "NewsArticle",

    headline: article.headline,

    description:
      article.excerpt ||
      article.subtitle ||
      undefined,

    datePublished:
      article.published_at || undefined,

    dateModified:
      article.updated_at ||
      article.published_at ||
      undefined,

    author: article.author_name
      ? {
          "@type": "Person",
          name: article.author_name,
        }
      : undefined,

    image: article.featured_image_url
      ? [article.featured_image_url]
      : undefined,

    mainEntityOfPage: {
      "@type": "WebPage",
      "@id":
        article.canonical_url ||
        `/article/${article.slug}`,
    },

    publisher: {
      "@type": "Organization",
      name: "Nepal News & Magazine",
    },
  };

  const formattedDate = formatDate(
    article.published_at
  );

  return (
    <>
      {/* =================================================
          STRUCTURED DATA
          ================================================= */}

      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(
            structuredData
          ),
        }}
      />

      {/* =================================================
          ARTICLE
          ================================================= */}

      <main className="mx-auto max-w-5xl px-4 py-8 md:py-12">
        <article>

          {/* =================================================
              CATEGORY
              ================================================= */}

          {category && (
            <div className="mb-4">
              <Link
                href={`/category/${category.slug}`}
                className="text-sm font-semibold uppercase tracking-wide hover:underline"
              >
                {category.name}
              </Link>
            </div>
          )}

          {/* =================================================
              HEADLINE
              ================================================= */}

          <h1 className="font-headline text-3xl font-bold leading-tight md:text-5xl">
            {article.headline}
          </h1>

          {/* =================================================
              SUBTITLE
              ================================================= */}

          {article.subtitle && (
            <p className="mt-4 text-lg leading-relaxed text-gray-600 md:text-xl">
              {article.subtitle}
            </p>
          )}

          {/* =================================================
              ARTICLE META
              ================================================= */}

          <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2 border-b pb-5 text-sm text-gray-500">
            {article.author_name && (
              <span>
                By{" "}
                <strong className="text-gray-700">
                  {article.author_name}
                </strong>
              </span>
            )}

            {formattedDate && (
              <time
                dateTime={
                  article.published_at ||
                  undefined
                }
              >
                {formattedDate}
              </time>
            )}

            {article.updated_at &&
              article.updated_at !==
                article.published_at && (
                <span>
                  Updated{" "}
                  {formatDate(
                    article.updated_at
                  )}
                </span>
              )}
          </div>

          {/* =================================================
              FEATURED IMAGE
              ================================================= */}

          {article.featured_image_url && (
            <figure className="mt-8">
              <div className="relative aspect-video overflow-hidden rounded-xl bg-gray-100">
                <Image
                  src={
                    article.featured_image_url
                  }
                  alt={
                    article.featured_image_alt ||
                    article.headline
                  }
                  fill
                  priority
                  sizes="(max-width: 768px) 100vw, 1024px"
                  className="object-cover"
                />
              </div>

              {article.featured_image_alt && (
                <figcaption className="mt-2 text-sm text-gray-500">
                  {article.featured_image_alt}
                </figcaption>
              )}
            </figure>
          )}

          {/* =================================================
              VIDEO
              ================================================= */}

          {article.video_url && (
            <section className="mt-8">
              <h2 className="mb-4 font-headline text-xl font-bold">
                Video
              </h2>

              {article.video_type ===
                "youtube" ? (
                <div className="relative aspect-video overflow-hidden rounded-xl">
                  <iframe
                    src={article.video_url}
                    title={article.headline}
                    className="absolute inset-0 h-full w-full"
                    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                    allowFullScreen
                  />
                </div>
              ) : (
                <video
                  controls
                  preload="metadata"
                  poster={
                    article.video_thumbnail_url ||
                    undefined
                  }
                  className="w-full rounded-xl bg-black"
                >
                  <source
                    src={article.video_url}
                    type={
                      article.video_type ===
                      "mp4"
                        ? "video/mp4"
                        : undefined
                    }
                  />

                  Your browser does not support
                  video playback.
                </video>
              )}
            </section>
          )}

          {/* =================================================
              ARTICLE BODY
              ================================================= */}

          {article.body_html ? (
            <div
              className="prose prose-lg mt-8 max-w-none"
              dangerouslySetInnerHTML={{
                __html: article.body_html,
              }}
            />
          ) : article.excerpt ? (
            <div className="mt-8">
              <p className="text-lg leading-8 text-gray-800">
                {article.excerpt}
              </p>
            </div>
          ) : null}

          {/* =================================================
              SOURCE ATTRIBUTION
              ================================================= */}

          {article.source_article_url && (
            <div className="mt-8 rounded-lg border bg-gray-50 p-5">
              <p className="text-sm text-gray-600">
                Source
              </p>

              <p className="mt-1 font-semibold">
                {article.source_name_snapshot ||
                  "Original source"}
              </p>

              <a
                href={article.source_article_url}
                target="_blank"
                rel="noopener noreferrer nofollow"
                className="mt-3 inline-block font-semibold underline"
              >
                Read original source →
              </a>
            </div>
          )}

          {/* =================================================
              SHARE
              ================================================= */}

          <div className="mt-8 border-t pt-6">
            <ShareButtons
              url={`/article/${article.slug}`}
              title={article.headline}
            />
          </div>
        </article>

        {/* =================================================
            RELATED NEWS
            ================================================= */}

        {relatedArticles.length > 0 && (
          <section className="mt-14 border-t pt-8">
            <div className="mb-5 flex items-center justify-between">
              <h2 className="font-headline text-2xl font-bold">
                Related News
              </h2>

              {category && (
                <Link
                  href={`/category/${category.slug}`}
                  className="text-sm font-medium hover:underline"
                >
                  More {category.name} →
                </Link>
              )}
            </div>

            <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
              {relatedArticles.map(
                (related) => (
                  <Link
                    key={related.id}
                    href={`/article/${related.slug}`}
                    className="group block"
                  >
                    {related.featured_image_url && (
                      <div className="relative aspect-video overflow-hidden rounded-lg bg-gray-100">
                        <Image
                          src={
                            related.featured_image_url
                          }
                          alt={
                            related.headline
                          }
                          fill
                          sizes="(max-width: 768px) 100vw, 33vw"
                          className="object-cover transition-transform group-hover:scale-105"
                        />
                      </div>
                    )}

                    <h3 className="mt-3 font-headline text-lg font-semibold leading-snug group-hover:underline">
                      {related.headline}
                    </h3>

                    {related.excerpt && (
                      <p className="mt-1 line-clamp-2 text-sm text-gray-600">
                        {related.excerpt}
                      </p>
                    )}

                    {related.published_at && (
                      <time
                        dateTime={
                          related.published_at
                        }
                        className="mt-2 block text-xs text-gray-400"
                      >
                        {formatDate(
                          related.published_at
                        )}
                      </time>
                    )}
                  </Link>
                )
              )}
            </div>
          </section>
        )}
      </main>
    </>
  );
}
