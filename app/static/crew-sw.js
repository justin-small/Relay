/* The crew page's service worker (#226). It keeps the page's own static files so the shell opens on a weak
   connection, and answers a page request it cannot reach with a short offline page. The signed-in page and every
   API answer are never stored: they belong to one person and change. */
const CACHE = 'relay-crew-v1';
const SHELL = ['/static/crew.js', '/static/viewer.css', '/static/brand/relay-mark.svg',
  '/static/brand/icon-192.png', '/static/brand/icon-512.png'];
const OFFLINE = '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
  '<title>Relay Crew</title><body style="font:16px system-ui;background:#000;color:#eee;padding:24px">' +
  '<h1 style="font-size:20px">Can\'t reach Relay</h1><p>Check that you are on the church network, then reload.</p>';

self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(SHELL); }).then(function () {
    return self.skipWaiting();
  }));
});

self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener('fetch', function (e) {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin) return;
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).catch(function () {
      return new Response(OFFLINE, { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
    }));
    return;
  }
  if (SHELL.indexOf(url.pathname) !== -1) {
    e.respondWith(caches.match(req).then(function (hit) {
      const fresh = fetch(req).then(function (res) {
        if (res.ok) caches.open(CACHE).then(function (c) { c.put(req, res.clone()); });
        return res;
      });
      return hit || fresh;
    }));
  }
});
