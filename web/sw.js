// Service worker for the installed dashboard. Pages and assets come from the network first, so a new version shows
// at once; the cached copy is used only without a connection. Device data never passes here: it is a WebSocket to
// the online relay, signed with the device key.
const CACHE = 'coc-dashboard-v54';
const SHELL = ['upgrade-times.js?v=2', 'upgrade-progress.js?v=3', 'upgrade-sprites.js?v=2', 'upgrade-availability.js?v=1', 'upgrade-max-levels.js', 'icons/upgrades/goblin_helper.png', 'upgrade-portrait.js?v=6', 'upgrade-icons.js?v=3', 'icons/upgrades/buildings.webp', 'icons/upgrades/lab.webp', 'icons/upgrades/pets.png', 'icons/upgrades/equipment.webp', 'icons/upgrades/builder.webp', 'icons/upgrades/star.webp', 'icons/stats/attacks.png', 'icons/stats/walls.webp', 'stats.js?v=17', 'discord.js?v=7', 'visible-poller.js?v=2', 'select.js?v=2', 'icons/resources/elixir_cart.png', './', 'index.html', 'style.css?v=accounts-1', 'app.js?v=accounts-1', 'buildings.js?v=23', 'accounts.js?v=1', 'i18n.js?v=5', 'realtime.js', 'deploy.js?v=3', 'settings.js?v=10',
  'messages.js?v=5', 'messages_en.json?v=4',
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
