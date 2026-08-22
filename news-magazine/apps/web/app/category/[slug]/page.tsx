import { notFound } from "next/navigation";
import Link from "next/link";
import { createServerSupabaseClient } from "@/lib/supabase/server";

const VALID_CATEGORIES = [
  "national",
  "politics",
  "business",
  "technology",
  "sports",
  "entertainment",
] as const;

type Category = (typeof VALID_CATEGORIES)[number];

interface PageProps {
  params: Promise<{
    slug: string;
  }>;
}

export default async function CategoryPage({ params }: PageProps) {
  const { slug } = await params;

  if (!VALID_CATEGORIES.includes(slug as Category)) {
    notFound();
  }

  const category = slug as Category;

  const supabase = createServerSupabaseClient();

  const { data: articles, error } = await supabase
    .from("articles")
    .select("*")
    .eq("status", "published")
    .eq("category", category)
    .order("published_at", { ascending: false })
    .limit(30);

  if (error) {
    console.error("Category page error:", error);
  }

  const title =
    category.charAt(0).toUpperCase() + category.slice(1);

  return (
    <main className="mx-auto max-w-6xl px-4 py-8">
      {/* Header */}
      <div className="mb-8 border-b pb-5">
        <p className="mb-2 text-sm font-medium uppercase tracking-wide text-muted-foreground">
          Nepal News & Magazine
        </p>

        <h1 className="text-3xl font-bold sm:text-4xl">
          {title} News
        </h1>

        <p className="mt-2 text-muted-foreground">
          Latest {title.toLowerCase()} news and updates.
        </p>
      </div>

      {/* Error / Empty State */}
      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-center">
          <h2 className="font-semibold">
            Unable to load news
          </h2>

          <p className="mt-2 text-sm text-muted-foreground">
            Please try again later.
          </p>
        </div>
      ) : !articles || articles.length === 0 ? (
        <div className="rounded-lg border p-10 text-center">
          <h2 className="text-xl font-semibold">
            No articles found
          </h2>

          <p className="mt-2 text-muted-foreground">
            There are currently no published articles in this category.
          </p>

          <Link
            href="/"
            className="mt-5 inline-block rounded-md border px-4 py-2 text-sm font-medium hover:bg-muted"
          >
            Back to Home
          </Link>
        </div>
      ) : (
        /* Articles */
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {articles.map((article) => (
            <article
              key={article.id}
              className="overflow-hidden rounded-lg border bg-background transition-shadow hover:shadow-md"
            >
              {article.featured_image_url && (
                <Link href={`/article/${article.slug}`}>
                  <img
                    src={article.featured_image_url}
                    alt={article.title}
                    loading="lazy"
                    className="h-52 w-full object-cover"
                  />
                </Link>
              )}

              <div className="p-5">
                <p className="mb-2 text-xs font-medium uppercase text-muted-foreground">
                  {title}
                </p>

                <h2 className="line-clamp-2 text-xl font-bold">
                  <Link
                    href={`/article/${article.slug}`}
                    className="hover:underline"
                  >
                    {article.title}
                  </Link>
                </h2>

                {article.excerpt && (
                  <p className="mt-3 line-clamp-3 text-sm text-muted-foreground">
                    {article.excerpt}
                  </p>
                )}

                <div className="mt-4 flex items-center justify-between text-xs text-muted-foreground">
                  {article.published_at && (
                    <time dateTime={article.published_at}>
                      {new Date(
                        article.published_at
                      ).toLocaleDateString("en-NP", {
                        year: "numeric",
                        month: "short",
                        day: "numeric",
                      })}
                    </time>
                  )}

                  <Link
                    href={`/article/${article.slug}`}
                    className="font-medium hover:underline"
                  >
                    Read more →
                  </Link>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </main>
  );
}
