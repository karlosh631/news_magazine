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
  images: {
    formats: ["image/avif", "image/webp"],

    minimumCacheTTL: 60 * 60 * 24,
    contentDispositionType: "attachment",
    contentSecurityPolicy: "default-src 'self'; script-src 'none'; sandbox;",

    remotePatterns: [
      {
        protocol: "https",
        hostname: "**",
      },
    ],
  },

  /*
   * Security headers
   */
  async headers() {
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

          {
            key: "Content-Security-Policy",
            value: [
              "default-src 'self'",

              /*
               * Images from your site and HTTPS news sources.
               */
              "img-src 'self' https: data:",

              /*
               * Next.js requires these during development.
               */
              "script-src 'self' 'unsafe-eval' 'unsafe-inline'",

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
