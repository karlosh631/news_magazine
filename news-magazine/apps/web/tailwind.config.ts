import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],

  // Tailwind defaults to `darkMode: "media"`, which follows the OS
  // `prefers-color-scheme`. This site does not use the OS — it has its
  // own toggle, and themeScript writes `.dark` / `.light` onto <html>.
  //
  // With the default, the two systems disagreed: a reader whose OS was
  // dark but who had picked light on the site got LIGHT backgrounds
  // from the base classes and DARK overrides from every `dark:`
  // utility. That is what rendered the summary panel solid black
  // behind near-white headline text on a white page.
  //
  // `class` points both systems at the same class. themeScript already
  // resolves the stored preference first and falls back to the OS, so
  // first-time visitors on a dark OS still get dark mode, and the
  // toggle now governs every utility as well.
  darkMode: "class",

  theme: {
    extend: {
      fontFamily: {
        // Editorial serif for headlines, clean sans for body/UI —
        // swap for the brand's actual typefaces (see frontend-design skill).
        headline: ["Georgia", "serif"],
        body: ["system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
export default config;
