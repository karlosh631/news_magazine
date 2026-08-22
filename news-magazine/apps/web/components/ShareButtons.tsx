"use client";

export function ShareButtons({ url, title }: { url: string; title: string }) {
  const fullUrl = typeof window !== "undefined" ? `${window.location.origin}${url}` : url;
  return (
    <div className="mt-8 flex gap-3 text-sm">
      <a
        href={`https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(fullUrl)}`}
        target="_blank" rel="noopener noreferrer" className="underline"
      >Share on Facebook</a>
      <a
        href={`https://twitter.com/intent/tweet?url=${encodeURIComponent(fullUrl)}&text=${encodeURIComponent(title)}`}
        target="_blank" rel="noopener noreferrer" className="underline"
      >Share on X</a>
    </div>
  );
}
