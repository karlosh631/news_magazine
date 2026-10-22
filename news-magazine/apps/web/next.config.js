/** @type {import('next').NextConfig} */
// NEXT_DIST_DIR lets a verification build write to its own directory so it
// cannot clobber the .next a running dev server is using. Without it,
// running `npm run build` while `npm run dev` is up blanks the site.
const nextConfig = {
  distDir: process.env.NEXT_DIST_DIR || ".next",
  reactStrictMode: true,

  /*
   * Next.js Image Optimization
   *
   * `remotePatterns` is read from lib/image-hosts.json rather than written
   * here. That file is the only place the list exists.
   *
   * The app needs the same list at RENDER time: components/OptimizedImage
   * .tsx has to know whether a host is allowed BEFORE handing a URL to
   * next/image, because next/image throws at render time for a host that
   * is not on it and would take the card down with it. Two copies of the
   * list would drift, and a host present in one and not the other is a
   * card carrying an unoptimised original with nothing reporting it. A
   * JSON file is importable by both - require() here, import in
   * lib/image-hosts.ts - so the two cannot disagree.
   *
   * How the list got here, since the story matters:
   *
   *   It was `hostname: "**"`, an open image proxy. The documented
   *   mitigation (sandbox CSP + contentDispositionType: attachment) made
   *   it worse here rather than safer: the optimised response came back
   *   with `Content-Disposition: attachment`, browsers refused to paint
   *   it, and every card rendered with no photograph at all. So both
   *   headers are gone and so is the wildcard.
   *
   * The entries are the apex domains the ingestion worker crawls (from
   * supabase/migrations/0008_seed_nepali_sources.sql) plus the CDN hosts
   * observed serving real article images. A host that serves photographs
   * from a SUBDOMAIN gets a `**.host` entry too, because Next's `**`
   * matches subdomains but NOT the apex - one pattern cannot cover both,
   * which is why `setopati.com` and `**.setopati.com` are two entries
   * and not one. Next also rejects a config with more than 50
   * remotePatterns, so this cannot be every host under every domain.
   *
   * TO ADD AN OUTLET: add its domain to lib/image-hosts.json. If its
   * photographs come from a CDN subdomain rather than the apex, add
   * `**.that-cdn-host` as well. A missing host is not a broken card - the
   * fallback in components/OptimizedImage.tsx renders a plain <img>
   * straight from the outlet - but it is an unoptimised one.
   */
  images: {
    formats: ["image/avif", "image/webp"],

    // Keep Vercel serving the resized copy rather than re-fetching the
    // origin on every page view.
    minimumCacheTTL: 60 * 60 * 24,

    remotePatterns: require("./lib/image-hosts.json"),
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
