"use client";

export default function CategoryError({ reset }: { reset: () => void }) {
  return (
    <main className="mx-auto flex max-w-3xl flex-col items-center gap-4 px-4 py-24 text-center">
      <h1 className="text-2xl font-bold text-gray-900">This category is temporarily unavailable</h1>
      <p className="text-sm text-gray-600">The latest articles could not be loaded. Please try again.</p>
      <button
        type="button"
        onClick={reset}
        className="rounded-md bg-black px-4 py-2 text-sm font-medium text-white hover:bg-gray-800"
      >
        Try again
      </button>
    </main>
  );
}