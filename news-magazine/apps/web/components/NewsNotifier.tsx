"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Tell the reader when the news arrives.
 *
 * WHAT THIS IS
 * ------------
 * A bell in the header that, once the reader has turned it on, raises
 * a notification in the device's OWN notification bar - the one on a
 * phone, the one on a laptop - the moment a story arrives. The reader
 * does not have to be looking at the page, and they do not have to
 * refresh it.
 *
 * WHY THE READER HAS TO ASK
 * -------------------------
 * Every browser in the world requires the reader's permission before a
 * site may raise a notification, and no site may ask without a
 * gesture. So the bell is OFF until it is clicked, and the click is
 * the ask. There is no way round this and no clever way past it: a
 * site that asks for notifications on load is a site nobody allows.
 *
 * HOW IT KNOWS A STORY ARRIVED
 * ----------------------------
 * The site already polls its own feed every few seconds, and this
 * polls the same endpoint once a minute. When the newest story is not
 * the one it saw last time, a story has arrived, and the story is
 * posted to the service worker, which raises the notification.
 *
 * One notification, not one per story: a reader who has had ten
 * notifications in a minute has stopped reading them.
 *
 * WHY IT WORKS WITHOUT A SERVER
 * -----------------------------
 * A notification raised this way needs no push key, no push service
 * and no subscription store. It is the browser's own Notification API
 * and it works on every device with a notification bar. The service
 * worker also handles a server-pushed notification, so the day the
 * site is given a push key the same bell starts working with the tab
 * closed, with nothing else to change.
 *
 * IT IS QUIET WHEN IT IS OFF
 * --------------------------
 * The poll only runs when the reader has asked for notifications, and
 * a reader who has not asked is never polled, never notified, and
 * never sees anything about it.
 */

/** Where the service worker is served from. */
const SERVICE_WORKER_URL = "/sw.js";

/** How often the feed is checked for something new. */
const POLL_MS = 60_000;

/** The key under which the reader's choice is remembered. */
const STORAGE_KEY = "news-notifications";

type NotifierState = "unsupported" | "off" | "on" | "blocked";

interface NewStory {
  id: string;
  headline: string;
  slug: string;
  image: string | null;
}

export function NewsNotifier() {
  const [state, setState] = useState<NotifierState>("off");

  /*
    THE NEWEST STORY SEEN.

    It is set the moment the reader turns the bell on, so the stories
    that arrived while they were not listening are not announced to
    them afterwards.
  */
  const lastSeen = useRef<string | null>(null);
  const registration = useRef<ServiceWorkerRegistration | null>(null);

  const register = useCallback(async () => {
    if (!("serviceWorker" in navigator)) return null;
    try {
      const reg = await navigator.serviceWorker.register(
        SERVICE_WORKER_URL,
        { scope: "/" }
      );
      registration.current = reg;
      return reg;
    } catch {
      // A browser that will not register a service worker still has
      // the Notification API, and the notification can be raised from
      // the page itself.
      return null;
    }
  }, []);

  /*
    THE STATE ON LOAD.

    A reader who turned the bell on yesterday and comes back today
    must not have to turn it on again, and a reader who blocked it
    must not be asked again on every page.
  */
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!("Notification" in window)) {
      setState("unsupported");
      return;
    }

    const stored = window.localStorage.getItem(STORAGE_KEY);
    const permission = Notification.permission;

    if (permission === "denied") {
      setState("blocked");
      return;
    }

    if (stored === "on" && permission === "granted") {
      setState("on");
      void register();
    } else {
      setState("off");
    }
  }, [register]);

  /*
    RAISE THE NOTIFICATION.

    The service worker raises it when there is one, because a
    notification raised by the service worker carries the site's icon
    and opens in the site's own window. Without one the page raises it
    itself, which is the same notification in a different place.
  */
  const notify = useCallback(async (stories: NewStory[]) => {
    if (stories.length === 0) return;
    const first = stories[0];
    if (!first) return;
    const payload = {
      type: "news:new",
      stories: [
        {
          title: first.headline,
          body: first.image ? "" : "नयाँ समाचार",
          url: `/article/${first.slug}`,
          tag: `news-${first.id}`,
          icon: first.image ?? "/icon-192.png",
          timestamp: Date.now(),
        },
      ],
    };

    const reg = registration.current;
    if (reg && reg.active) {
      reg.active.postMessage(payload);
      return;
    }
    const worker = reg?.waiting ?? reg?.installing;
    if (worker) {
      worker.postMessage(payload);
      return;
    }

    // No service worker: the page raises it.
    try {
      const registration2 = await navigator.serviceWorker?.getRegistration();
      if (registration2?.active) {
        registration2.active.postMessage(payload);
        return;
      }
    } catch {
      // Fall through to the direct notification.
    }

    try {
      new Notification(first.headline, {
        body: first.image ? "" : "नयाँ समाचार",
        icon: first.image ?? "/icon-192.png",
        tag: `news-${first.id}`,
        data: { url: `/article/${first.slug}` },
      });
    } catch {
      // Nothing more to try.
    }
  }, []);

  /*
    THE POLL.

    It runs only when the bell is on, and it stops the moment the bell
    is off. It asks for the three newest stories, which is enough to
    see that something has arrived, and it asks the server for them
    uncached.
  */
  useEffect(() => {
    if (state !== "on") return;

    let stopped = false;

    const check = async () => {
      try {
        const response = await fetch(
          `/api/live-news?limit=3&t=${Date.now()}`,
          { cache: "no-store" }
        );
        if (!response.ok || stopped) return;
        const payload = (await response.json()) as {
          articles?: Array<{
            id: string;
            headline: string;
            slug: string;
            featured_image_url?: string | null;
            image_highres_url?: string | null;
          }>;
        };

        const articles = payload.articles ?? [];
        if (articles.length === 0) return;

        const newest = articles[0];
        if (!newest) return;

        if (lastSeen.current === null) {
          // The first check only sets the mark. The stories that
          // arrived before the bell was turned on are not announced.
          lastSeen.current = newest.id;
          return;
        }
        if (newest.id === lastSeen.current) return;

        // Something arrived. Everything newer than what was seen is
        // announced, newest first.
        const fresh = articles.filter(
          (article) => article.id !== lastSeen.current
        );
        lastSeen.current = newest.id;

        await notify(
          fresh.map((article) => ({
            id: article.id,
            headline: article.headline,
            slug: article.slug,
            image: article.image_highres_url ?? article.featured_image_url ?? null,
          }))
        );
      } catch {
        // Offline, or the server is busy. The next tick tries again.
      }
    };

    void check();
    const timer = window.setInterval(() => void check(), POLL_MS);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [state, notify]);

  /*
    THE BELL
    ---------
    Off: it asks. On: it turns off. Blocked: it says so, once, and
    stops asking, because a site that keeps asking after a refusal is
    a site that has been told no.
  */
  const toggle = useCallback(async () => {
    if (!("Notification" in window)) return;

    if (state === "on") {
      window.localStorage.setItem(STORAGE_KEY, "off");
      setState("off");
      lastSeen.current = null;
      return;
    }

    let permission = Notification.permission;
    if (permission === "default") {
      try {
        permission = await Notification.requestPermission();
      } catch {
        permission = "denied";
      }
    }

    if (permission !== "granted") {
      window.localStorage.setItem(STORAGE_KEY, "off");
      setState("blocked");
      return;
    }

    window.localStorage.setItem(STORAGE_KEY, "on");
    setState("on");
    await register();
    // The mark is set by the first poll, so nothing that arrived
    // before this moment is announced.
    lastSeen.current = null;
  }, [state, register]);

  // The bell is in the server-rendered HTML, not something that
  // appears a moment after the page has loaded. A control that is
  // not there until the page settles is a control nobody finds, and
  // it moves the page when it appears.
  if (state === "unsupported") return null;

  const on = state === "on";
  const blocked = state === "blocked";

  return (
    <button
      type="button"
      onClick={() => void toggle()}
      aria-pressed={on}
      aria-label={
        on
          ? "नयाँ समाचारको सूचना बन्द गर्नुहोस्"
          : "नयाँ समाचारको सूचना चालू गर्नुहोस्"
      }
      title={
        blocked
          ? "The browser will not allow notifications from this site"
          : on
            ? "Notifications are on"
            : "Tell me when the news arrives"
      }
      className={[
        "flex h-12 w-12 items-center justify-center rounded-full border transition active:scale-95",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600",
        on
          ? "border-blue-600 bg-blue-600 text-white hover:bg-blue-700"
          : blocked
            ? "border-gray-200 text-gray-300 hover:text-gray-400 dark:border-gray-700 dark:text-gray-600"
            : "border-gray-300 text-gray-700 hover:border-black hover:bg-gray-900 hover:text-white",
      ].join(" ")}
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        className="h-5 w-5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
        <path d="M13.7 21a2 2 0 0 1-3.4 0" />
      </svg>
      {on ? (
        <span
          aria-hidden="true"
          className="absolute mt-[-18px] ml-[18px] h-2 w-2 rounded-full bg-emerald-400"
        />
      ) : null}
    </button>
  );
}
