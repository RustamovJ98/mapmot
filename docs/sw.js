/* MapMot service worker: offline app shell + on-demand tile cache. */
const VERSION = '9365c318de';
const SHELL = 'mapmot-shell-' + VERSION;
const TILES = 'mapmot-tiles-v1';
const PACK = 'mapmot-pack-v1'; // "whole Tashkent offline": filled by the page, never trimmed, survives app updates
const TILE_HOSTS = ['tile.openstreetmap.org', 'tiles.openfreemap.org'];
const MAX_TILES = 6000;
const SHELL_URLS = ['./', './index.html', './vendor/leaflet.min.js', './vendor/leaflet.min.css',
  './vendor/maplibre-gl.js', './vendor/maplibre-gl.css', './vendor/leaflet-maplibre-gl.js', './style/liberty-ru.json', './ru-names.json', './marks.json',
  './manifest.webmanifest', './icons/moto.svg', './icons/icon-192.png', './icons/icon-512.png', './icons/icon-512-maskable.png'];

self.addEventListener('install', e => {
  // cache: 'reload' bypasses the browser HTTP cache (GitHub Pages sends max-age=600);
  // without it a new version could store the previous deploy's files
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(SHELL_URLS.map(u => new Request(u, {cache: 'reload'})))).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k.startsWith('mapmot-shell-') && k !== SHELL).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (TILE_HOSTS.includes(url.hostname)) { e.respondWith(tileHandler(e.request, url)); return; }
  if (url.origin === self.location.origin) { e.respondWith(shellHandler(e.request, url)); }
});

async function shellHandler(req, url) {
  const cache = await caches.open(SHELL);
  const isPage = req.mode === 'navigate' || url.pathname.endsWith('/') || url.pathname.endsWith('/index.html');
  const isMarks = url.pathname.endsWith('/marks.json'); // shared sign reports: the bot updates them between builds
  if (isPage || isMarks) {
    // network first so updates arrive when online; cached page when offline.
    // cache: 'no-cache' revalidates with the server (ETag / Last-Modified) instead of trusting
    // the HTTP cache, so a fresh deploy shows up on the next open, not 10 minutes later
    try {
      const r = await fetch(req.url, {cache: 'no-cache', credentials: 'same-origin'});
      if (r.ok) cache.put(isMarks ? './marks.json' : './index.html', r.clone());
      return r;
    } catch (err) {
      if (isMarks) return (await cache.match('./marks.json')) || new Response('[]', {headers: {'Content-Type': 'application/json'}});
      return (await cache.match('./index.html')) || (await cache.match('./')) || Response.error();
    }
  }
  const hit = await cache.match(req, {ignoreSearch: true});
  if (hit) return hit;
  const r = await fetch(req);
  if (r.ok) cache.put(req, r.clone());
  return r;
}

// vector tiles of the offline pack are stored without the weekly build version in the path,
// so they keep working after OpenFreeMap publishes a newer build
function packKey(url) {
  const m = url.hostname === 'tiles.openfreemap.org' && url.pathname.match(/^\/planet\/[^/]+\/(\d+)\/(\d+)\/(\d+)\.pbf$/);
  return m ? 'https://tiles.openfreemap.org/planet/pack/' + m[1] + '/' + m[2] + '/' + m[3] + '.pbf' : null;
}

let putCounter = 0;
async function tileHandler(req, url) {
  if (url.searchParams.has('mmpack')) return fetch(req); // pack download: the page stores the response itself
  const pack = await caches.open(PACK), cache = await caches.open(TILES);
  if (url.hostname === 'tiles.openfreemap.org' && url.pathname === '/planet') {
    // TileJSON names the current weekly tile build: network first, a stored copy when offline
    try {
      const r = await fetch(req);
      if (r.ok) cache.put(req, r.clone());
      return r;
    } catch (err) {
      return (await pack.match(req)) || (await cache.match(req)) || new Response('', {status: 503, statusText: 'offline'});
    }
  }
  const packHit = await pack.match(packKey(url) || req);
  if (packHit) return packHit;
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
