"use client";

interface PaginationProps {
  currentPage: number;
  totalPages: number;
  onPageChange: (page: number) => void;
}

export function Pagination({
  currentPage,
  totalPages,
  onPageChange,
}: PaginationProps) {
  if (totalPages <= 1) return null;

  const hasPrev = currentPage > 1;
  const hasNext = currentPage < totalPages;

  return (
    <div className="mt-10 flex items-center justify-center gap-3">
      <button
        type="button"
        disabled={!hasPrev}
        onClick={() => onPageChange(currentPage - 1)}
        className={`rounded-md border px-4 py-2 text-sm font-medium transition ${
          hasPrev
            ? "border-gray-300 text-gray-700 hover:border-black hover:bg-gray-900 hover:text-white"
            : "cursor-not-allowed border-gray-200 text-gray-300"
        }`}
      >
        ← Previous
      </button>

      <span className="text-sm text-gray-600">
        Page <span className="font-semibold text-gray-900">{currentPage}</span> of{" "}
        <span className="font-semibold text-gray-900">{totalPages}</span>
      </span>

      <button
        type="button"
        disabled={!hasNext}
        onClick={() => onPageChange(currentPage + 1)}
        className={`rounded-md border px-4 py-2 text-sm font-medium transition ${
          hasNext
            ? "border-gray-300 text-gray-700 hover:border-black hover:bg-gray-900 hover:text-white"
            : "cursor-not-allowed border-gray-200 text-gray-300"
        }`}
      >
        Next →
      </button>
    </div>
  );
}
