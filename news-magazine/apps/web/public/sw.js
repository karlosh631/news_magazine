/*
 * The service worker.
 *
 * WHAT IT IS FOR
 * --------------
 * A reader who wants to be told when the news arrives, on whichever
 * device they are reading on. The notification belongs in the device's
 * own notification bar - the one on a phone and the one on a laptop -
 * not in a banner the reader has to be looking at the page to see.
 *
 * TWO WAYS A NOTIFICATION REACHES THIS FILE
 * ------------------------------------------
 *
 *   1. THE PAGE TELLS US. The site already polls its own feed every
 *      few seconds, so the page knows the moment a story arrives.
 *      It posts the story here and this file raises the notification.
 *      That path needs no key, no service and no server: it is the
 *      browser's own Notification API, and it works on every device
 *      that has a notification bar.
 *
 *   2. THE SERVER PUSHES IT. When the site is configured with a push
 *      key, the server can raise a notification with the tab closed.
 *      The handler is here so that path works the day it is switched
 *      on, without touching anything else.
 *
 * WHY IT IS A SEPARATE FILE
 * -------------------------
 * A service worker has to be at the root of the site to control it, it
 * cannot import anything from the application's bundle, and it has to
 * be plain JavaScript. So it is a file of its own, served from
 * /sw.js, and it is deliberately small.
 */

self.addEventListener("install", (event) => {
  // Take over straight away: a reader who has just turned
  // notifications on should not have to wait for a second reload.
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

/**
 * The notification itself.
 *
 * One shape, used by both paths, so a reader cannot tell which one
 * raised it.
 */
function show(story) {
  const title = story.title || "नयाँ समाचार";
  const options = {
    body: story.body || "",
    icon: story.icon || "/icon-192.png",
    badge: story.badge || "/icon-192.png",
    // The tag is the story's own id, so the same story never stacks up
    // as five identical notifications, and a new story with the same
    // headline replaces the old one.
    tag: story.tag || "news",
    data: { url: story.url || "/" },
    // The notification stays until it is read on a phone, and fades
    // on its own on a desktop.
    requireInteraction: false,
    renotify: true,
    // A tap opens the story, so the notification is a link and not
    // just a line of text.
    timestamp: story.timestamp || Date.now(),
  };

  return self.registration.showNotification(title, options);
}

// ---------------------------------------------------------------------
// 1. THE PAGE TELLS US
// ---------------------------------------------------------------------
self.addEventListener("message", (event) => {
  const data = event.data;
  if (!data || data.type !== "news:new") return;

  const stories = Array.isArray(data.stories) ? data.stories : [];
  if (stories.length === 0) return;

  // One notification, not one per story: a reader who has had ten
  // notifications in a minute has stopped reading them.
  const first = stories[0];
  const others = stories.length - 1;

  event.waitUntil(
    show({
      title: first.title,
      body:
        others > 0
          ? `${first.body ? first.body + "\n" : ""}+ ${others} more`
          : first.body || "",
      url: first.url,
      tag: first.tag,
      icon: first.icon,
      timestamp: first.timestamp,
    })
  );
});

// ---------------------------------------------------------------------
// 2. THE SERVER PUSHES IT
// ---------------------------------------------------------------------
self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (err) {
    payload = { title: event.data ? event.data.text() : "नयाँ समाचार" };
  }

  event.waitUntil(
    show({
      title: payload.title || "नयाँ समाचार",
      body: payload.body || "",
      url: payload.url || "/",
      tag: payload.tag || "news",
      icon: payload.icon || "/icon-192.png",
      timestamp: payload.timestamp || Date.now(),
    })
  );
});

// ---------------------------------------------------------------------
// THE NOTIFICATION IS A LINK
// ---------------------------------------------------------------------
self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const url = (event.notification.data && event.notification.data.url) || "/";

  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((list) => {
        // A tab that already has the site open is brought to the
        // front and sent to the story, rather than a second copy of
        // the site being opened.
        for (const client of list) {
          if ("focus" in client) {
            client.navigate(url);
            return client.focus();
          }
        }
        return self.clients.openWindow(url);
      })
  );
});
