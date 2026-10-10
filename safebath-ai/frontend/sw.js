/* SafeBath Caretaker service worker.
 *
 * Responsibilities:
 *   1. Web push — show a notification for emergencies pushed from the edge
 *      server (pywebpush), even when the caretaker tab is closed.
 *   2. Offline app shell — network-first with a cache fallback so the UI still
 *      loads on a flaky bath-room Wi-Fi / when the Pi is briefly unreachable.
 *
 * The edge server is the source of truth; this worker never mutates data.
 */
const CACHE = "safebath-caretaker-v1";
const SHELL = ["./caretaker.html", "./dist/caretaker-bundle.js", "./manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) =>
      Promise.all(SHELL.map((u) => c.add(u).catch(() => null)))
    )
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;       // never touch the API
  if (url.pathname.includes("/caretaker/") || url.pathname.startsWith("/api/")) return;

  event.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match("./caretaker.html")))
  );
});

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { title: "SafeBath", body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "SafeBath emergency";
  const severity = data.severity || "high";
  const options = {
    body: data.body || data.message || "A caregiver alert was raised.",
    tag: data.event_id || "safebath-emergency",
    renotify: true,
    requireInteraction: severity === "critical" || severity === "high",
    data: { url: data.url || "./caretaker.html", event_id: data.event_id },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "./caretaker.html";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const c of clients) {
        if ("focus" in c) return c.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow(target);
    })
  );
});
