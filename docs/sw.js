/* Can Radar – Service Worker
 * - App-Dateien: erst Netz (damit Updates sofort da sind), bei Funkloch aus dem Speicher -> startet auch offline.
 * - Schriften und Scanner-Bibliotheken: einmal geladen, dann aus dem Speicher.
 * - Angebote (deals.json), ntfy und Open Food Facts gehen immer direkt ins Netz (hier nichts zwischenspeichern). */
const V = 'canradar-1.9.0';
const SHELL = ['./', 'index.html', 'bridge.js', 'manifest.json',
  'icons/apple-touch-icon.png', 'icons/icon-192.png', 'icons/icon-512.png', 'icons/favicon-32.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(V)
    .then(c => c.addAll(SHELL.map(u => new Request(u, { cache: 'reload' }))))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks.filter(k => k.startsWith('canradar-') && k !== V).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

function keep(req, res) {
  if (res && res.ok) { const c = res.clone(); caches.open(V).then(ca => ca.put(req, c)).catch(() => {}); }
  return res;
}

function netFirst(req) {
  return new Promise(resolve => {
    let done = false;
    const fromCache = () => caches.match(req, { ignoreSearch: true })
      .then(m => m || (req.mode === 'navigate' ? caches.match('index.html') : null));
    const t = setTimeout(() => fromCache().then(m => { if (m && !done) { done = true; resolve(m); } }), 3500);
    fetch(req, { cache: 'no-cache' })
      .then(res => { clearTimeout(t); keep(req, res); if (!done) { done = true; resolve(res); } })
      .catch(() => { clearTimeout(t); fromCache().then(m => { if (!done) { done = true; resolve(m || Response.error()); } }); });
  });
}

function cacheFirst(req) {
  return caches.match(req).then(m => m || fetch(req).then(res => keep(req, res)));
}

self.addEventListener('fetch', e => {
  const r = e.request;
  if (r.method !== 'GET') return;
  const u = new URL(r.url);
  if (u.origin === location.origin) { e.respondWith(netFirst(r)); return; }
  if (/(^|\.)fonts\.(googleapis|gstatic)\.com$/.test(u.hostname) || u.hostname === 'cdn.jsdelivr.net' || u.hostname === 'unpkg.com') {
    e.respondWith(cacheFirst(r));
  }
});
