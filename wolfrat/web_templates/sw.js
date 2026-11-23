/* WolfRAT Web Admin service worker: keeps the app shell (page, script, styles,
 * icons) so the installed app opens even when WolfRAT is out of reach, and
 * shows WolfRAT's notifications. Network first, so a WolfRAT update shows at
 * once. Never touches /api/* or /ws: those always go straight to WolfRAT and
 * are never stored. */
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
const ICON = '/static/icon-192.png';
const BADGE = '/static/badge-96.png';

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

/** A page of this WolfRAT to open: its own addresses only, else the start page. */
function pageUrl(value) {
    try {
        const url = new URL(typeof value === 'string' && value ? value : '/', self.location.origin);
        if (url.origin === self.location.origin) return url.href;
    } catch (error) { /* not an address */ }
    return new URL('/', self.location.origin).href;
}

// A notification from WolfRAT: {title, body, tag, url}.
self.addEventListener('push', event => {
    let data = {};
    try {
        data = event.data ? event.data.json() : {};
    } catch (error) {
        data = {body: event.data ? event.data.text() : ''};
    }
    if (!data || typeof data !== 'object') data = {};
    event.waitUntil(self.registration.showNotification(String(data.title || 'WolfRAT'), {
        body: String(data.body || ''),
        icon: ICON,
        badge: BADGE,
        tag: String(data.tag || 'wolfrat'),       // renotify needs a tag
        renotify: true,
        data: {url: typeof data.url === 'string' ? data.url : '/'},
    }));
});

// A tap on it: bring an open WolfRAT page forward on that screen, else open one.
self.addEventListener('notificationclick', event => {
    event.notification.close();
    const target = pageUrl(event.notification.data && event.notification.data.url);
    event.waitUntil(
        self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(list => {
            const open = list.find(client => client.focused) || list[0];
            if (!open) return self.clients.openWindow(target);
            return Promise.resolve()
                .then(() => open.focus())
                .catch(() => open)
                .then(client => {
                    const page = client || open;
                    if (page.url === target || typeof page.navigate !== 'function') return page;
                    return page.navigate(target).catch(() => page);
                });
        }),
    );
});
