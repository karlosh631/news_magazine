export function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <div className="rounded-lg border border-dashed p-10 text-center text-gray-500">
      <p className="font-semibold">{title}</p>
      <p className="mt-1 text-sm">{description}</p>
    </div>
  );
}
