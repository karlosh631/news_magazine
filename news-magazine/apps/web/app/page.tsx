import { createServerSupabaseClient } from "@/lib/supabase/server";
import { NewsCard } from "@/components/NewsCard";
import { BreakingNewsTicker } from "@/components/BreakingNewsTicker";
import { EmptyState } from "@/components/EmptyState";

export const revalidate = 60; // ISR — homepage regenerates at most once a minute

async function getHomepageData() {
  const supabase = createServerSupabaseClient();

  const [{ data: breaking }, { data: latest }] = await Promise.all([
    supabase
      .from("articles")
      .select("id, slug, headline, featured_image_url, published_at")
      .eq("status", "published")
      .eq("is_breaking", true)
      .order("published_at", { ascending: false })
      .limit(8),
    supabase
      .from("articles")
      .select("id, slug, headline, excerpt, featured_image_url, published_at, primary_category_id")
      .eq("status", "published")
      .order("published_at", { ascending: false })
      .limit(20),
  ]);

  return { breaking: breaking ?? [], latest: latest ?? [] };
}

export default async function HomePage() {
  const { breaking, latest } = await getHomepageData();

  return (
    <>
      {breaking.length > 0 && <BreakingNewsTicker items={breaking} />}

      <section className="max-w-6xl mx-auto px-4 py-8">
        <h2 className="text-2xl font-headline font-bold mb-4">ताजा समाचार · Latest News</h2>

        {latest.length === 0 ? (
          <EmptyState
            title="No articles yet"
            description="Once sources are enabled and the ingestion service runs its first sync, articles will appear here automatically."
          />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
            {latest.map((article) => (
              <NewsCard key={article.id} article={article} />
            ))}
          </div>
        )}
      </section>

      {/* Category rail sections (National, Politics, Business, ...) follow
          the same server-fetch pattern as `getHomepageData` above — each
          queries `.eq("primary_category_id", categoryId)` and renders via
          <NewsCard>. Omitted here for brevity in this scaffold; wire up
          CategorySection.tsx per category using this same query shape. */}
    </>
  );
}
