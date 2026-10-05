import { LiveNewsFeed } from "@/components/LiveNewsFeed";

export const dynamic = "force-dynamic";

export default function HomePage() {
  return (
    <main className="mx-auto max-w-7xl px-4 py-8">
      <LiveNewsFeed category="all" />
    </main>
  );
}
