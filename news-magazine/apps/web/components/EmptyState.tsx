export function EmptyState({
  title,
  description,
}: {
  title: string;
  description?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-gray-300 py-20 text-center">
      <h2 className="text-lg font-semibold text-gray-800">{title}</h2>
      {description && <p className="max-w-sm text-sm text-gray-500">{description}</p>}
    </div>
  );
}
