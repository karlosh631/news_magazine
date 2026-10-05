"use client";

import { useState } from "react";

type SafeImageProps = {
  src: string;
  alt: string;
  className?: string;
  priority?: boolean;
};

const FALLBACK_IMAGE =
  "https://images.unsplash.com/photo-1504711434969-e33886168f5c?auto=format&fit=crop&w=1600&q=85";

export function SafeImage({ src, alt, className, priority = false }: SafeImageProps) {
  const [imageSrc, setImageSrc] = useState(src.trim() || FALLBACK_IMAGE);
  const [failed, setFailed] = useState(false);

  if (failed) {
    return <div className={className || "h-full w-full"} aria-label="Image unavailable" />;
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={imageSrc}
      alt={alt}
      className={className}
      loading={priority ? "eager" : "lazy"}
      fetchPriority={priority ? "high" : "auto"}
      onError={() => {
        if (imageSrc !== FALLBACK_IMAGE) setImageSrc(FALLBACK_IMAGE);
        else setFailed(true);
      }}
    />
  );
}
