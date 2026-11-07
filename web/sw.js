// Service worker for the installed dashboard. Pages and assets come from the network first, so a new version shows
// at once; the cached copy is used only without a connection. Device data never passes here: it is a WebSocket to
// the online relay, signed with the device key.
const CACHE = 'coc-dashboard-v25';
const SHELL = ['icons/stats/attacks.png', 'icons/stats/walls.webp', 'stats.js?v=8', 'discord.js?v=4', 'visible-poller.js?v=2', 'select.js', 'icons/resources/elixir_cart.png', './', 'index.html', 'style.css?v=buildings-2', 'app.js?v=buildings-2', 'buildings.js?v=2', 'i18n.js', 'realtime.js', 'deploy.js', 'settings.js?v=3',
  'messages.js', 'messages_en.json',
  'icons/resources/builder_gold.webp', 'icons/resources/builder_elixir.webp', 'icons/resources/gold.png', 'icons/resources/elixir.png', 'icons/resources/dark_elixir.png', 'fonts/lilita-one-latin.woff2', 'config.js', 'favicon.svg', 'manifest.webmanifest', 'icons/icon-192.png', 'icons/icon-512.png'];

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
  if (new URL(request.url).pathname.startsWith('/api/')) return;
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
