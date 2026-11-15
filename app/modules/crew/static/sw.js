/* The crew page's service worker (#226). It makes the page installable and shows an offline page when Relay can't be
   reached. It never stores a page that carries a person's data or any /crew/api answer: only the offline page and the
   page's own static files. */
const VERSION = 'crew-v2';
const OFFLINE = '/crew/_/offline';
const SHELL = [OFFLINE, '/static/modules/crew/crew.css', '/static/modules/crew/crew.js'];

self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(VERSION).then(function (c) { return c.addAll(SHELL); }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== VERSION; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener('fetch', function (e) {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (req.mode === 'navigate') {
    // Always the live page (it is per person); the offline page only when the network fails.
    e.respondWith(fetch(req).catch(function () { return caches.match(OFFLINE); }));
    return;
  }
  if (SHELL.indexOf(url.pathname) > 0) {
    // The page's own files: from the network when it can, so an update shows, else the saved copy.
    e.respondWith(fetch(req).then(function (res) {
      const copy = res.clone();
      caches.open(VERSION).then(function (c) { c.put(url.pathname, copy); });
      return res;
    }).catch(function () { return caches.match(url.pathname); }));
  }
});

// A page (#228): the push carries the words and the person's own confirm link. The same tag replaces the last
// buzz, and renotify makes each re-fire buzz again until the person confirms.
self.addEventListener('push', function (e) {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = {}; }
  const url = typeof d.url === 'string' && d.url.indexOf('/crew/page/') === 0 ? d.url : '/crew';
  e.waitUntil(self.registration.showNotification(typeof d.title === 'string' ? d.title : 'Page', {
    body: typeof d.body === 'string' ? d.body : '', tag: typeof d.tag === 'string' ? d.tag : 'page', renotify: true,
    requireInteraction: true, vibrate: [300, 150, 300, 150, 300], data: { url: url },
    icon: '/static/brand/icon-192.png'
  }));
});

self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  const url = e.notification.data && e.notification.data.url || '/crew';
  e.waitUntil(self.clients.openWindow(url));
});
