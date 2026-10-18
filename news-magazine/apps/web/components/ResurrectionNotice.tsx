/**
 * Explains what the on-demand scrape did, so the user knows whether they
 * are looking at freshly-recovered stories or a stale cached set.
 */
export function ResurrectionNotice({
  query,
  resurrectedCount,
  note,
  rateLimited,
}: {
  query: string;
  resurrectedCount: number;
  note: string | null;
  rateLimited: boolean;
}) {
  if (!resurrectedCount && !note && !rateLimited) return null;

  const message = rateLimited
    ? (note ?? "That search has been run several times already.")
    : resurrectedCount > 0
      ? `${resurrectedCount} ${resurrectedCount === 1 ? "story" : "stories"} matching “${query}” had already been pruned after 48 hours. They were re-fetched from the original sources just now.`
      : (note ?? `Nothing matching “${query}” was found on any source.`);

  return (
    <div
      role="status"
      className={`mb-6 rounded-lg border p-4 text-sm ${
        resurrectedCount > 0
          ? "border-blue-200 bg-blue-50 text-blue-900"
          : "border-amber-200 bg-amber-50 text-amber-900"
      }`}
    >
      {message}
    </div>
  );
}