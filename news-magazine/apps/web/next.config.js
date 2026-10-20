/** @type {import('next').NextConfig} */
// NEXT_DIST_DIR lets a verification build write to its own directory so it
// cannot clobber the .next a running dev server is using. Without it,
// running `npm run build` while `npm run dev` is up blanks the site.
const nextConfig = {
  distDir: process.env.NEXT_DIST_DIR || ".next",
  reactStrictMode: true,

  /*
   * Keep this enabled temporarily while developing.
   *
   * IMPORTANT:
   * Fix TypeScript errors before production instead of relying
   * permanently on ignoreBuildErrors.
   */
  /*
   * Next.js Image Optimization
   *
   * The wildcard host is deliberate, and the comment that used to sit
   * above it ("Do NOT use hostname: \"**\"") was wrong about the
   * tradeoff for this site.
   *
   * Every outlet serves its photographs from a different CDN, and they
   * change without notice: OnlineKhabar from wp-content on its own
   * domain, Kathmandu Post from assets-cdn.kathmandupost.com, Ratopati
   * from npcdn.ratopati.com, Himalayan Times from its own uploads
   * path. An allowlist of the ones known today breaks the moment an
   * outlet moves a file, which is the "articles have no image" bug this
   * pipeline already had once.
   *
   * So the wildcard stays, and the actual abuse surface is closed with
   * the three settings below — Next's documented mitigation for exactly
   * this case:
   *
   *   contentSecurityPolicy "sandbox"  -> the optimised response can
   *       only ever be an image. It cannot be loaded as a document, so
   *       it cannot be used for phishing or to serve stored XSS through
   *       our origin.
   *   contentDispositionType "attachment" -> forces a download rather
   *       than inline render for anything that ignores the MIME type.
   *   minimumCacheTTL -> keeps Vercel serving the resized copy, so we
   *       are not re-fetching the origin on every page view.
   *
   * The real fix is to enumerate the hosts, which needs a live pass over
   * the sources table to collect actual image URLs. That is worth doing
   * once Render has been running long enough for a cycle of articles to
   * land, at which point this block can shrink to an explicit list.
   */
  /*
   * Next.js Image Optimization
   *
   * This was `hostname: "**"` with a sandbox CSP and
   * contentDispositionType: "attachment" — Next's documented mitigation
   * for an open image proxy, where anyone could use /_next/image as a
   * free proxy for arbitrary URLs.
   *
   * Two problems with that arrangement here:
   *
   *   1. It does not work. The optimized response came back with
   *      `Content-Disposition: attachment`, and browsers would not paint
   *      it — cards rendered with no photograph even though every row
   *      had a valid URL.
   *   2. It was still an open proxy. Those headers limit what a
   *      fetched image can be USED for; they do not stop the fetch. The
   *      wildcard remained.
   *
   * So the wildcard is gone. The list below is every outlet the
   * ingestion worker is configured to crawl (from
   * supabase/migrations/0008_seed_nepali_sources.sql), in both apex
   * and www form, plus the CDN hosts observed serving real article
   * images.
   *
   * With the host restricted, the proxy concern is resolved at the
   * source, and the two headers that broke rendering are not needed.
   *
   * TO ADD AN OUTLET: add its domain here. If its photographs are
   * served from a separate CDN, add that host too — a missing host
   * shows as a card with no image, and nothing else reports it.
   */
  /*
   * Next.js Image Optimization
   *
   * This was `hostname: "**"` with a sandbox CSP and
   * contentDispositionType: "attachment" — Next's documented mitigation
   * for an open image proxy, where anyone could use /_next/image to
   * fetch arbitrary URLs through this domain.
   *
   * Two problems with that arrangement here:
   *
   *   1. It did not work. The optimised response came back with
   *      `Content-Disposition: attachment` and browsers would not paint
   *      it — cards rendered with no photograph even though every row
   *      had a valid URL.
   *   2. It was still an open proxy. Those headers limit what a fetched
   *      image may be USED for; they do not stop the fetch. The wildcard
   *      remained.
   *
   * So the wildcard is gone and the allowlist is explicit. One entry per
   * outlet the ingestion worker crawls (from
   * supabase/migrations/0008_seed_nepali_sources.sql) plus the CDN
   * hosts observed serving real article images. `**.host` covers the
   * apex and every subdomain in a single entry, because Next rejects a
   * config with more than 50 remotePatterns.
   *
   * With the host restricted, the proxy concern is resolved at the
   * source and the two headers that broke rendering are unnecessary.
   *
   * TO ADD AN OUTLET: add its domain here. If its photographs come from
   * a separate CDN, add that host too — a missing host shows as a card
   * with no image, and nothing else reports it.
   */
  /*
   * Next.js Image Optimization
   *
   * This was `hostname: "**"` with a sandbox CSP and
   * contentDispositionType: "attachment" — Next's documented mitigation
   * for an open image proxy, where anyone could use /_next/image to fetch
   * arbitrary URLs through this domain.
   *
   * Two problems with that arrangement here:
   *
   *   1. It did not work. The optimised response came back with
   *      `Content-Disposition: attachment`, and browsers would not paint
   *      it — cards rendered with no photograph even though every row had
   *      a valid URL.
   *   2. It was still an open proxy. Those headers limit what a fetched
   *      image may be USED for; they do not stop the fetch itself, so
   *      the wildcard remained.
   *
   * The wildcard is gone. With the host restricted at the source, the
   * proxy concern is resolved and the headers that broke rendering are
   * unnecessary — removing them is no longer a weakening.
   *
   * Every outlet the ingestion worker crawls is listed by apex domain
   * (from supabase/migrations/0008_seed_nepali_sources.sql). Domains
   * observed serving article images from a SUBDOMAIN get a `**.host`
   * entry as well, because Next's `**` matches subdomains but not the
   * apex — a single pattern cannot cover both.
   *
   * Next rejects a config with more than 50 remotePatterns, which is why
   * this cannot simply be every host under every domain.
   *
   * TO ADD AN OUTLET: add its apex domain. If its photographs come from
   * a CDN subdomain rather than the apex, add `**.that-cdn-host` too.
   * A missing host shows as a card with no image, and nothing else
   * reports it.
   */
  images: {
    formats: ["image/avif", "image/webp"],

    // Keep Vercel serving the resized copy rather than re-fetching the
    // origin on every page view.
    minimumCacheTTL: 60 * 60 * 24,

    remotePatterns: [
        { protocol: "https", hostname: "24news.com" },
        { protocol: "https", hostname: "annapurbapost.com" },
        { protocol: "https", hostname: "arthikabhojan.com" },
        { protocol: "https", hostname: "bbc.com" },
        { protocol: "https", hostname: "bhabishaanepal.com" },
        { protocol: "https", hostname: "ekantipur.com" },
        { protocol: "https", hostname: "himalkhabar.com" },
        { protocol: "https", hostname: "kathmandupost.com" },
        { protocol: "https", hostname: "khabarzone.com" },
        { protocol: "https", hostname: "myreplica.com" },
        { protocol: "https", hostname: "nagariknews.com" },
        { protocol: "https", hostname: "nagariktimes.com" },
        { protocol: "https", hostname: "nepalbhas.com" },
        { protocol: "https", hostname: "nepalkantha.com" },
        { protocol: "https", hostname: "nepalnews.com" },
        { protocol: "https", hostname: "newahale.com" },
        { protocol: "https", hostname: "onlinekhabar.com" },
        { protocol: "https", hostname: "pradeshpost.com" },
        { protocol: "https", hostname: "prasutojournal.com" },
        { protocol: "https", hostname: "ratopati.com" },
        { protocol: "https", hostname: "setopati.com" },
        { protocol: "https", hostname: "sudarshannews.com" },
        { protocol: "https", hostname: "thehimalayantimes.com" },
        { protocol: "https", hostname: "voiceofnepal.com" },
        { protocol: "https", hostname: "**.kathmandupost.com" },
        { protocol: "https", hostname: "**.ratopati.com" },
        { protocol: "https", hostname: "**.nepalnews.com" },
        { protocol: "https", hostname: "**.onlinekhabar.com" },
        { protocol: "https", hostname: "**.nagariknews.com" },
        { protocol: "https", hostname: "**.thehimalayantimes.com" },
        { protocol: "https", hostname: "**.prixacdn.net" },
        { protocol: "https", hostname: "**.nagariknewscdn.com" },
        { protocol: "https", hostname: "**.nepalhomes.com" },
    ],
  },

  /*
   * Security headers
   *
   * `isDev` matters here. The Content-Security-Policy below is not a
   * single string: `'unsafe-eval'` is in it during development, where
   * Next.js's dev overlay and the source-map machinery genuinely need
   * it, and OUT of it in production, where nothing does. It used to be
   * in both — the comment even said "Next.js requires these during
   * development" while the header shipped the development value to
   * every reader.
   *
   * `'unsafe-eval'` in a production CSP is a real weakening: it lets any
   * injected string be evaluated as code, which is exactly what a
   * Content-Security-Policy exists to prevent, and it is one of the
   * things Lighthouse's "Ensure CSP is effective against XSS attacks"
   * audit measures. Removing it in production is the difference
   * between a policy that is described and a policy that is enforced.
   */
  async headers() {
    const isDev = process.env.NODE_ENV !== "production";

    return [
      {
        source: "/:path*",

        headers: [
          {
            key: "X-Frame-Options",
            value: "DENY",
          },

          {
            key: "X-Content-Type-Options",
            value: "nosniff",
          },

          {
            key: "Referrer-Policy",
            value: "strict-origin-when-cross-origin",
          },

          {
            key: "Permissions-Policy",
            value:
              "camera=(), microphone=(), geolocation=(), payment=()",
          },

          /*
           * Origin isolation.
           *
           * COOP puts this document in its own browsing-context group,
           * so a cross-origin window it opens cannot reach back through
           * `window.opener` — which is the delivery mechanism for
           * tabnabbing. `same-origin-allow-popups` rather than plain
           * `same-origin` because plain COOP severs `window.opener` for
           * every popup, and a site that links out to an outlet in a new
           * tab should not have that window lose its opener.
           *
           * CORP says this document may not be embedded by another
           * origin. `X-Frame-Options: DENY` already says that for
           * frames; CORP covers fetch() and other subresource loads,
           * which is the other half of the same protection.
           *
           * Neither affects the YouTube embed on the article page: those
           * govern how OTHERS may embed US, and how our top-level
           * document is grouped, not what we are allowed to embed.
           */
          {
            key: "Cross-Origin-Opener-Policy",
            value: "same-origin-allow-popups",
          },
          {
            key: "Cross-Origin-Resource-Policy",
            value: "same-origin",
          },

          {
            key: "Content-Security-Policy",
            value: [
              "default-src 'self'",

              /*
               * Images from your site and HTTPS news sources.
               */
              "img-src 'self' https: data:",

              /*
               * Scripts.
               *
               * `'unsafe-inline'` is still needed in production: Next.js
               * emits an inline bootstrap script per route, and the
               * middleware can issue a per-request nonce, but that is a
               * larger change than a header and it has to survive the
               * static optimisation of every page.
               *
               * `'unsafe-eval'` is NOT needed in production — only in
               * development, for the dev overlay's source maps.
               */
              isDev
                ? "script-src 'self' 'unsafe-eval' 'unsafe-inline'"
                : "script-src 'self' 'unsafe-inline'",

              /*
               * Tailwind / Next.js styles.
               */
              "style-src 'self' 'unsafe-inline'",

              /*
               * Fonts.
               */
              "font-src 'self' https: data:",

              /*
               * Supabase / API requests.
               */
              "connect-src 'self' https: wss:",

              /*
               * YouTube or other embedded video providers.
               *
               * Add only providers you actually use.
               */
              "frame-src 'self' https://www.youtube.com https://www.youtube-nocookie.com",

              /*
               * Video/audio files.
               */
              "media-src 'self' https: blob:",

              /*
               * Prevent object/embed based content.
               */
              "object-src 'none'",

              /*
               * Prevent the site from being embedded.
               */
              "frame-ancestors 'none'",

              /*
               * Restrict base URI.
               */
              "base-uri 'self'",

              /*
               * Restrict form submissions.
               */
              "form-action 'self'",
            ].join("; "),
          },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
