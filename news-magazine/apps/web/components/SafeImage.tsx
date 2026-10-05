"use client";

import { useState } from "react";

type SafeImageProps = {
  src: string;
  alt: string;
  className?: string;
  priority?: boolean;
};

export function SafeImage({ src, alt, className, priority = false }: SafeImageProps) {
  const [failed, setFailed] = useState(false);

  if (failed || !src.trim()) {
    return <div className={className || "h-full w-full"} aria-label="Image unavailable" />;
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={alt}
      className={className}
      loading={priority ? "eager" : "lazy"}
      fetchPriority={priority ? "high" : "auto"}
      onError={() => setFailed(true)}
    />
  );
}
