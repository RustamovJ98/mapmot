/* MapMot service worker: offline app shell + on-demand tile cache. */
const VERSION = 'ed84176f72';
const SHELL = 'mapmot-shell-' + VERSION;
const TILES = 'mapmot-tiles-v1';
const TILE_HOSTS = ['tile.openstreetmap.org', 'tiles.openfreemap.org'];
const MAX_TILES = 6000;
const SHELL_URLS = ['./', './index.html', './vendor/leaflet.min.js', './vendor/leaflet.min.css',
  './vendor/maplibre-gl.js', './vendor/maplibre-gl.css', './vendor/leaflet-maplibre-gl.js', './style/liberty-ru.json',
  './manifest.webmanifest', './icons/moto.svg', './icons/icon-192.png', './icons/icon-512.png', './icons/icon-512-maskable.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(SHELL_URLS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k.startsWith('mapmot-shell-') && k !== SHELL).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (TILE_HOSTS.includes(url.hostname)) { e.respondWith(tileHandler(e.request)); return; }
  if (url.origin === self.location.origin) { e.respondWith(shellHandler(e.request, url)); }
});

async function shellHandler(req, url) {
  const cache = await caches.open(SHELL);
  const isPage = req.mode === 'navigate' || url.pathname.endsWith('/') || url.pathname.endsWith('/index.html');
  if (isPage) {
    // network first so updates arrive when online; cached page when offline
    try {
      const r = await fetch(req);
      if (r.ok) cache.put('./index.html', r.clone());
      return r;
    } catch (err) {
      return (await cache.match('./index.html')) || (await cache.match('./')) || Response.error();
    }
  }
  const hit = await cache.match(req, {ignoreSearch: true});
  if (hit) return hit;
  const r = await fetch(req);
  if (r.ok) cache.put(req, r.clone());
  return r;
}

let putCounter = 0;
async function tileHandler(req) {
  const cache = await caches.open(TILES);
  const hit = await cache.match(req);
  if (hit) return hit;
  try {
    const r = await fetch(req);
    if (r.ok) {
      cache.put(req, r.clone());
      if (++putCounter % 100 === 0) trimTiles(cache);
    }
    return r;
  } catch (err) {
    return new Response('', {status: 503, statusText: 'offline'});
  }
}

async function trimTiles(cache) {
  const keys = await cache.keys();
  if (keys.length <= MAX_TILES) return;
  const drop = keys.slice(0, keys.length - MAX_TILES + 500); // oldest first
  await Promise.all(drop.map(k => cache.delete(k)));
}

self.addEventListener('message', async e => {
  const msg = e.data || {};
  const reply = data => e.source && e.source.postMessage(Object.assign({re: msg.type}, data));
  if (msg.type === 'tiles-count') {
    const cache = await caches.open(TILES);
    reply({count: (await cache.keys()).length});
  } else if (msg.type === 'tiles-clear') {
    await caches.delete(TILES);
    reply({ok: true});
  } else if (msg.type === 'version') {
    reply({version: VERSION});
  }
});
