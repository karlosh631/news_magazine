"use client";

import { usePathname, useRouter } from "next/navigation";

type PaginationProps = {
  currentPage: number;
  totalPages: number;
  onPageChange?: (page: number) => void;
  basePath?: string;
  searchParams?: Record<string, string | undefined>;
};

export function Pagination({
  currentPage,
  totalPages,
  onPageChange,
  basePath,
  searchParams,
}: PaginationProps) {
  const router = useRouter();
  const pathname = usePathname();

  const goToPage = (page: number) => {
    if (onPageChange) {
      onPageChange(page);
      return;
    }

    const params = new URLSearchParams();
    Object.entries(searchParams ?? {}).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    params.set("page", String(page));
    router.push(`${basePath ?? pathname}?${params.toString()}`);
  };

  if (totalPages <= 1) return null;

  return (
    <nav aria-label="Pagination" className="mt-8 flex items-center justify-center gap-2">
      <button
        type="button"
        disabled={currentPage === 1}
        onClick={() => goToPage(currentPage - 1)}
        className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 transition hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50"
      >
        Previous
      </button>

      <span className="px-3 text-sm text-gray-600">
        Page <strong className="text-gray-900">{currentPage}</strong> of{" "}
        <strong className="text-gray-900">{totalPages}</strong>
      </span>

      <button
        type="button"
        disabled={currentPage === totalPages}
        onClick={() => goToPage(currentPage + 1)}
        className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 transition hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50"
      >
        Next
      </button>
    </nav>
  );
}
