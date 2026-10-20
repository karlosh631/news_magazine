import { defineConfig } from "@playwright/test";

/**
 * The web app had no test harness at all, despite `npm test` pointing
 * at Playwright.
 *
 * These are unit tests over pure functions — no browser, no server, no
 * network. They run in milliseconds and they are the only thing standing
 * between a one-character regex change and a silently broken feed.
 */
export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "list",
  // No `use` block on purpose: none of these specs launch a browser.
  // Adding a `projects` entry would start one for every test, which is
  // the difference between a 200ms suite and a 90-second one.
});
