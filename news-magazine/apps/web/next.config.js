/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  /*
   * Keep this enabled temporarily while developing.
   *
   * IMPORTANT:
   * Fix TypeScript errors before production instead of relying
   * permanently on ignoreBuildErrors.
   */
  typescript: {
    ignoreBuildErrors: true,
  },

  /*
   * Next.js Image Optimization
   *
   * Add the REAL image hosts used by your RSS/news sources here.
   *
   * Do NOT use:
   * hostname: "**"
   *
   * until you know exactly which domains your ingestion service uses.
   */
  images: {
    formats: ["image/avif", "image/webp"],

    remotePatterns: [
      /*
       * Example:
       *
       * {
       *   protocol: "https",
       *   hostname: "example.com",
       *   pathname: "/**",
       * },
       */
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
