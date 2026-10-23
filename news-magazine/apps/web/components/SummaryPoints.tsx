/**
 * The four points shown on a card and on the article page.
 *
 * Every entry is a verbatim sentence extracted from the outlet's own
 * article by the Python summariser (apps/ingestion/app/services/
 * summarizer.py). This component only renders them.
 */
export function SummaryPoints({
  points,
  compact = false,
}: {
  points: string[];
  compact?: boolean;
}) {
  if (!points || points.length === 0) return null;

  return (
    <ul className={compact ? "space-y-1.5" : "mt-3 space-y-2"}>
      {points.slice(0, 4).map((point, index) => (
        <li key={index} className="flex gap-2.5">
          <span
            aria-hidden="true"
            /*
              The number in the circle. It was 10 pixels, which is
              below the 12 pixels Lighthouse counts as legible; the
              circle is 20 pixels across, so a 12-pixel digit still
              sits inside it comfortably.
            */
            className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-600 text-xs font-bold text-white"
          >
            {index + 1}
          </span>
          <span className="text-sm leading-relaxed text-gray-800">{point}</span>
        </li>
      ))}
    </ul>
  );
}