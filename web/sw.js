// Service worker for the installed dashboard. Pages and assets come from the network first, so a new version shows
// at once; the cached copy is used only without a connection. Device data never passes here: it is a WebSocket to
// Supabase, signed with the device key.
const CACHE = 'coc-dashboard-v1';
const SHELL = ['./', 'index.html', 'style.css', 'app.js', 'i18n.js', 'realtime.js', 'deploy.js', 'settings.js',
  'config.js', 'favicon.svg', 'manifest.webmanifest', 'icons/icon-192.png', 'icons/icon-512.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys()
    .then((names) => Promise.all(names.filter((name) => name !== CACHE).map((name) => caches.delete(name))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET' || new URL(request.url).origin !== self.location.origin) return;
  event.respondWith(fetch(request)
    .then((response) => {
      if (response.ok) {
        const copy = response.clone();
        event.waitUntil(caches.open(CACHE).then((cache) => cache.put(request, copy)));
      }
      return response;
    })
    .catch(() => caches.match(request).then((cached) => cached || caches.match('./'))));
});
