"use client";

export function ShareButtons({ url, title }: { url: string; title: string }) {
  const fullUrl = typeof window !== "undefined" ? `${window.location.origin}${url}` : url;
  return (
    <div className="mt-8 flex items-center gap-3 text-sm">
      {/*
        48 pixels tall.

        These are two links side by side, and at the height of a line
        of text they were 20 pixels tall with twelve pixels between
        them - the shape of a mistake, because a finger going for one
        can land on the other. The padding gives each one a target
        the size of the rest of the site's controls.
      */}
      <a
        href={`https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(fullUrl)}`}
        target="_blank" rel="noopener noreferrer"
        className="inline-flex min-h-[48px] items-center underline"
      >Share on Facebook</a>
      <a
        href={`https://twitter.com/intent/tweet?url=${encodeURIComponent(fullUrl)}&text=${encodeURIComponent(title)}`}
        target="_blank" rel="noopener noreferrer"
        className="inline-flex min-h-[48px] items-center underline"
      >Share on X</a>
    </div>
  );
}
