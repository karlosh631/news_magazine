/**
 * Headed live monitor for new24hr.vercel.app.
 *
 * Opens a real Chromium window you can watch and click, and reports what
 * it finds back here. This exists because the failures that matter are the
 * ones only a browser sees: a <video> the browser refuses to decode, an
 * <img> that resolves to zero natural width, a card whose poster never
 * arrives. curl cannot observe any of that — it gets the HTML and stops.
 *
 * Run:  node watch.js [url] [--seconds=N]
 * Stop: Ctrl+C in this terminal, or close the window.
 */

const { chromium } = require("playwright");

const TARGET = process.argv[2]?.startsWith("http")
  ? process.argv[2]
  : "https://new24hr.vercel.app/category/reels";

const SECONDS = Number(
  (process.argv.find((a) => a.startsWith("--seconds=")) || "").split("=")[1] || 180
);

const INTERVAL = 5000;

/**
 * What the page looks like right now, judged the way a reader would.
 *
 * Evaluated in the browser rather than scraped from the HTML, because every
 * interesting property here — readyState, naturalWidth, decoded audio
 * bytes — is only observable after the browser has actually tried.
 */
const PROBE = () => {
  const out = { videos: [], images: [], cards: 0, at: new Date().toISOString() };

  for (const v of document.querySelectorAll("video")) {
    const src = v.currentSrc || v.src || v.querySelector("source")?.src || "";
    // `webkitAudioDecodedByteCount` is the only reliable cross-check that a
    // file carries sound; `duration` and `videoWidth` are satisfied by a
    // silent clip just as happily.
    const audioBytes =
      v.webkitAudioDecodedByteCount ??
      (v.mozHasAudio != null ? (v.mozHasAudio ? 1 : 0) : null);
    out.videos.push({
      src: src.split("?")[0].slice(-60),
      ext: (src.split("?")[0].split(".").pop() || "").toLowerCase(),
      readyState: v.readyState, // 0 = nothing, 4 = fully playable
      networkState: v.networkState,
      duration: Number.isFinite(v.duration) ? Math.round(v.duration) : null,
      w: v.videoWidth || 0,
      h: v.videoHeight || 0,
      // 1 = HAVE_METADATA (dimensions + duration known), 2 = HAVE_CURRENT_DATA
      playing: v.currentTime > 0,
      paused: v.paused,
      muted: v.muted,
      audioBytes,
      error: v.error ? { code: v.error.code, message: v.error.message } : null,
      visible: !!(v.offsetWidth || v.offsetHeight),
    });
  }

  for (const img of document.querySelectorAll("img")) {
    if (!img.offsetWidth && !img.offsetHeight) continue; // not rendered, skip
    out.images.push({
      // naturalWidth === 0 after a completed load means the resource
      // decoded to nothing. A still-loading image is not yet broken.
      broken: img.complete && img.naturalWidth === 0,
      loading: !img.complete,
      nat: img.naturalWidth,
      alt: (img.alt || "").slice(0, 40),
      src: (img.currentSrc || img.src || "").split("?")[0].slice(-50),
    });
  }

  out.cards = document.querySelectorAll("a[href^='/article/']").length;
  out.placeholderText = /NO IMAGE AVAILABLE/i.test(document.body.innerText || "");

  return out;
};

(async () => {
  const browser = await chromium.launch({ headless: false });
  const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });

  const consoleErrors = [];
  const failedRequests = [];

  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text().slice(0, 160));
  });
  page.on("requestfailed", (r) => {
    failedRequests.push({
      url: r.url().split("?")[0].slice(-60),
      why: r.failure()?.errorText,
    });
  });

  console.log(`\n  Watching ${TARGET}`);
  console.log(`  Window is live and interactive. Ctrl+C here to stop.\n`);

  await page.goto(TARGET, { waitUntil: "domcontentloaded" });

  const seen = { videoSrcs: new Set(), brokenImgSrcs: new Set(), placeholderSeen: false };

  const tick = async () => {
    let s;
    try {
      s = await page.evaluate(PROBE);
    } catch (err) {
      console.log(`  [probe failed] ${err.message}`);
      return;
    }

    const brokenVideos = s.videos.filter(
      (v) => v.error || (v.readyState === 0 && v.visible)
    );
    const silent = s.videos.filter(
      (v) => v.readyState > 0 && v.audioBytes !== null && v.audioBytes === 0
    );
    const stuck = s.videos.filter((v) => v.readyState === 1 && v.visible);
    const brokenImgs = s.images.filter((i) => i.broken);
    const lowRes = s.videos.filter((v) => v.h > 0 && v.h < 720);

    for (const v of s.videos) if (v.src) seen.videoSrcs.add(v.src);
    for (const i of brokenImgs) if (i.src) seen.brokenImgSrcs.add(i.src);
    if (s.placeholderText) seen.placeholderSeen = true;

    const parts = [
      `cards=${s.cards}`,
      `videos=${s.videos.length}`,
      `imgs=${s.images.length}`,
    ];
    if (brokenVideos.length) parts.push(`!! BROKEN-VIDEO=${brokenVideos.length}`);
    if (silent.length) parts.push(`!! SILENT=${silent.length}`);
    if (stuck.length) parts.push(`!! STUCK-META=${stuck.length}`);
    if (lowRes.length) parts.push(`lowres=${lowRes.length}`);
    if (brokenImgs.length) parts.push(`!! BROKEN-IMG=${brokenImgs.length}`);
    if (s.placeholderText) parts.push("!! NO-IMAGE-PLACEHOLDER");

    console.log(`  ${s.at.slice(11, 19)}  ${parts.join("  ")}`);

    for (const v of brokenVideos) {
      console.log(
        `       video ${v.ext || "?"} readyState=${v.readyState} ` +
          `error=${v.error ? v.error.code : "-"} ${v.src}`
      );
    }
    for (const v of stuck) console.log(`       video stuck on metadata: ${v.src}`);
    for (const v of silent) console.log(`       video has no audio track: ${v.src}`);
    for (const i of brokenImgs) console.log(`       img broken: ${i.src} alt="${i.alt}"`);
    if (consoleErrors.length) {
      for (const e of consoleErrors.splice(0, 3)) console.log(`       console: ${e}`);
    }
    if (failedRequests.length) {
      for (const f of failedRequests.splice(0, 5)) {
        console.log(`       request failed (${f.why}): ${f.url}`);
      }
    }
  };

  await tick();
  const timer = setInterval(tick, INTERVAL);

  // Reload on a longer cycle so "always fresh" is actually demonstrated
  // rather than merely asserted — a page that only ever shows its first
  // paint proves nothing about polling.
  const rotate = setInterval(async () => {
    console.log(`\n  ---- reloading to re-check a cold first paint ----`);
    await page.reload({ waitUntil: "domcontentloaded" }).catch(() => {});
  }, Math.max(SECONDS, 60) * 1000);

  setTimeout(async () => {
    clearInterval(timer);
    clearInterval(rotate);
    console.log(`\n  ---- summary after ${SECONDS}s ----`);
    console.log(`  distinct video files seen : ${seen.videoSrcs.size}`);
    console.log(`  distinct broken images    : ${seen.brokenImgSrcs.size}`);
    console.log(`  no-image placeholder seen  : ${seen.placeholderSeen ? "YES" : "no"}`);
    await browser.close();
    process.exit(0);
  }, SECONDS * 1000);
})();
