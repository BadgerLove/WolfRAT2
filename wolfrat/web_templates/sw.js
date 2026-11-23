/* WolfRAT Web Admin service worker: keeps the app shell (page, script, styles,
 * icons) so the installed app opens even when WolfRAT is out of reach.
 * Network first, so a WolfRAT update shows at once. Never touches /api/* or
 * /ws: those always go straight to WolfRAT and are never stored. */
'use strict';

const CACHE = 'wolfrat-shell-v1';
const SHELL = [
    '/',
    '/static/app.js',
    '/static/style.css',
    '/manifest.webmanifest',
    '/static/icon-192.png',
    '/static/icon-512.png',
    '/static/icon-maskable-512.png',
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE)
            .then(cache => cache.addAll(SHELL))
            .catch(() => undefined)
            .then(() => self.skipWaiting()),
    );
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys()
            .then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
            .then(() => self.clients.claim()),
    );
});

self.addEventListener('fetch', event => {
    const request = event.request;
    if (request.method !== 'GET') return;
    const url = new URL(request.url);
    if (url.origin !== self.location.origin) return;
    if (url.pathname.startsWith('/api/') || url.pathname === '/ws') return;
    if (SHELL.indexOf(url.pathname) < 0) return;
    event.respondWith(
        fetch(request)
            .then(response => {
                if (response && response.ok) {
                    const copy = response.clone();
                    caches.open(CACHE).then(cache => cache.put(url.pathname, copy)).catch(() => undefined);
                }
                return response;
            })
            .catch(() => caches.match(url.pathname).then(hit => hit || Response.error())),
    );
});
