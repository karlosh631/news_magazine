"use client";

import Image from "next/image";

import { canOptimizeImage } from "@/lib/image-hosts";

type OptimizedImageProps = {
  src: string;
  alt: string;
  /**
   * The `sizes` attribute, and the whole point of the exercise.
   *
   * Without it `next/image` assumes `100vw` for every image, which on a
   * three-column grid downloads a full-width picture into a third of the
   * page — it would still be smaller than the outlet's original file, but
   * it would be three times the bytes it needs to be.
   */
  sizes: string;
  /**
   * Always laid out with `fill`, so the element is positioned by its parent
   * and cannot push anything around when the picture arrives. The parent
   * must therefore be `position: relative` and must already have a height
   * from an aspect ratio; every caller here does.
   */
  className?: string;
  onError?: () => void;
  /**
   * Only the plain `<img>` path sends this. Through the optimizer the
   * browser never talks to the outlet — it talks to this origin, and the
   * server fetches the original — so there is nothing for the outlet's
   * referer policy to object to.
   */
  referrerPolicy?: React.HTMLAttributeReferrerPolicy;
};

/**
 * A remote photograph, resized by the Next.js optimizer when we are allowed
 * to and left alone when we are not.
 *
 * `next/image` is what turns a 553 KB outlet original into a 40 KB AVIF, and
 * it is the single biggest thing this site can do for its weight. But it
 * THROWS while rendering if the host is not in `images.remotePatterns`, so
 * handing it a URL that is not on the list would take the whole card down
 * rather than fall back.
 *
 * Hence the branch. The allowlist is checked here, against the same JSON the
 * config is built from, and a host that is not on it gets the plain `<img>`
 * the card used to render — unoptimised, but working, which is what an
 * outlet added since the last deploy gets until the list catches up.
 */
export function OptimizedImage({
  src,
  alt,
  sizes,
  className,
  onError,
  referrerPolicy,
}: OptimizedImageProps) {
  if (canOptimizeImage(src)) {
    return (
      <Image
        src={src}
        alt={alt}
        fill
        sizes={sizes}
        className={className}
        onError={onError}
      />
    );
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={alt}
      loading="lazy"
      referrerPolicy={referrerPolicy}
      onError={onError}
      className={className}
    />
  );
}
