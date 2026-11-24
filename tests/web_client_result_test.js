// Behaviour tests for the phone page's app.js, run by tests/test_web_client.py:
//     node tests/web_client_result_test.js wolfrat/web_templates/app.js
// Each case loads app.js into a fresh fake browser (a small fake DOM, storage,
// fetch and WebSocket) and drives it through its own functions. The service
// worker (sw.js, next to app.js) runs in its own fake worker scope.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const sourcePath = process.argv[2];
if (!sourcePath) {
    throw new Error('usage: node web_client_result_test.js <app.js>');
}
const SOURCE = fs.readFileSync(sourcePath, 'utf8');
const workerPath = path.join(path.dirname(sourcePath), 'sw.js');
const WORKER_SOURCE = fs.readFileSync(workerPath, 'utf8');
const UA = 'Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 '
    + '(KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36';
const IPHONE_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 '
    + '(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1';

// ------------------------------------------------------------ fake browser
class FakeClassList {
    constructor() {
        this.values = new Set();
    }

    add(...names) {
        names.forEach(name => this.values.add(name));
    }

    remove(...names) {
        names.forEach(name => this.values.delete(name));
    }

    contains(name) {
        return this.values.has(name);
    }

    toggle(name, force) {
        const want = force === undefined ? !this.values.has(name) : !!force;
        if (want) this.values.add(name);
        else this.values.delete(name);
        return want;
    }
}

class FakeElement {
    constructor(doc, id = '') {
        this.id = id;
        this.doc = doc;
        this.classList = new FakeClassList();
        this.dataset = {};
        this.style = {};
        this.value = '';
        this.textContent = '';
        this.innerHTML = '';
        this.children = [];
        this.attributes = {};
        this.listeners = {};
        this.disabled = false;
        this.maxLength = -1;
        this.placeholder = '';
        this.title = '';
    }

    get className() {
        return [...this.classList.values].join(' ');
    }

    set className(value) {
        this.classList.values = new Set(String(value).split(/\s+/).filter(Boolean));
    }

    addEventListener(type, handler) {
        (this.listeners[type] = this.listeners[type] || []).push(handler);
    }

    appendChild(child) {
        this.children.push(child);
        if (child.id) this.doc.elements.set(child.id, child);
        return child;
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }

    getAttribute(name) {
        return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
    }

    removeAttribute(name) {
        delete this.attributes[name];
    }

    focus() {
        this.doc.activeElement = this;
    }

    blur() {}

    closest() {
        return null;
    }

    scrollIntoView() {}

    click() {
        (this.listeners.click || []).forEach(fn => fn({target: this, preventDefault() {}}));
    }
}

class FakeStorage {
    constructor(initial = {}) {
        this.map = new Map(Object.entries(initial));
    }

    getItem(key) {
        return this.map.has(key) ? this.map.get(key) : null;
    }

    setItem(key, value) {
        this.map.set(key, String(value));
    }

    removeItem(key) {
        this.map.delete(key);
    }

    keys() {
        return [...this.map.keys()].sort();
    }
}

function respond(status, body, headers = {}) {
    const lower = Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
    return {
        ok: status >= 200 && status < 300,
        status,
        headers: {get: name => (Object.prototype.hasOwnProperty.call(lower, name.toLowerCase()) ? lower[name.toLowerCase()] : null)},
        json: async () => {
            if (body instanceof Error) throw body;
            return body;
        },
    };
}

/**
 * A browser's Push API for one page: a service worker registration whose
 * push manager remembers its subscription, and Notification permission.
 * `answer` = what the permission prompt answers.
 */
function fakePush({permission = 'default', answer = 'granted', subscribedWith = null} = {}) {
    const env = {subscribeCalls: [], unsubscribed: 0, permissionAsked: 0, current: null};
    const makeSub = key => ({
        endpoint: 'https://fcm.googleapis.com/fcm/send/fake-endpoint',
        options: {applicationServerKey: key},
        toJSON() {
            return {endpoint: this.endpoint, expirationTime: null, keys: {p256dh: 'BPfake', auth: 'AUfake'}};
        },
        async unsubscribe() {
            env.unsubscribed += 1;
            if (env.current === this) env.current = null;
            return true;
        },
    });
    if (subscribedWith) env.current = makeSub(subscribedWith);
    const registration = {
        pushManager: {
            async getSubscription() {
                return env.current;
            },
            async subscribe(options) {
                env.subscribeCalls.push(options);
                env.current = makeSub(options.applicationServerKey);
                return env.current;
            },
        },
    };
    env.serviceWorker = {
        register: async () => registration,
        ready: Promise.resolve(registration),
        getRegistration: async () => registration,
    };
    env.Notification = {
        permission,
        async requestPermission() {
            env.permissionAsked += 1;
            env.Notification.permission = answer;
            return answer;
        },
    };
    return env;
}

/**
 * A fresh page: app.js loaded with the given storage and address hash.
 * `serve(request)` answers fetch() calls ({path, method, headers, body}).
 * `secure` = an HTTPS page; `push` = fakePush() (a browser with the Push API);
 * `standalone` = opened as the installed app.
 */
function makePage({
    local = {}, session = {}, hash = '', serve = null,
    secure = false, push = null, ua = UA, standalone = false,
} = {}) {
    const doc = {
        elements: new Map(),
        activeElement: null,
        visibilityState: 'visible',
        listeners: {},
        documentElement: {scrollHeight: 1000},
        getElementById(id) {
            if (!this.elements.has(id)) this.elements.set(id, new FakeElement(this, id));
            return this.elements.get(id);
        },
        createElement() {
            return new FakeElement(this);
        },
        querySelectorAll() {
            return [];
        },
        addEventListener(type, handler) {
            (this.listeners[type] = this.listeners[type] || []).push(handler);
        },
    };
    doc.body = new FakeElement(doc, 'body');
    // As in index.html, the three top-level screens start hidden.
    ['signin', 'connecting', 'shell'].forEach(id => doc.getElementById(id).classList.add('hide'));

    const events = [];
    const requests = [];
    const sockets = [];
    const location = {
        protocol: 'http:', host: '192.168.1.20:8070', pathname: '/', search: '', hash,
        get href() {
            return `http://${this.host}${this.pathname}${this.search}${this.hash}`;
        },
    };
    const history = {
        calls: [],
        setUrl(url) {
            const at = String(url).indexOf('#');
            location.hash = at >= 0 ? String(url).slice(at) : '';
        },
        replaceState(state, title, url) {
            this.calls.push(['replace', url]);
            events.push(`replaceState ${url}`);
            this.setUrl(url);
        },
        pushState(state, title, url) {
            this.calls.push(['push', url]);
            this.setUrl(url);
        },
        back() {
            this.calls.push(['back']);
        },
    };

    class FakeWebSocket {
        constructor(url) {
            this.url = url;
            this.readyState = 0;
            this.sent = [];
            sockets.push(this);
            events.push(`websocket ${url}`);
        }

        send(data) {
            this.sent.push(JSON.parse(data));
        }

        close() {
            this.readyState = 3;
        }
    }
    FakeWebSocket.OPEN = 1;

    const windowListeners = {};
    const page = {
        doc, events, requests, sockets, history, location,
        local: new FakeStorage(local),
        session: new FakeStorage(session),
        serve: serve || (() => respond(500, {error: 'no server in this test'})),
        el: id => doc.getElementById(id),
        run: code => vm.runInContext(code, context),
        // A value from the page as plain JSON (objects made inside the page
        // have the page's own prototypes, which deepStrictEqual refuses).
        json: code => JSON.parse(vm.runInContext(`JSON.stringify(${code})`, context)),
        shown: id => !doc.getElementById(id).classList.contains('hide'),
        fire: (type, event) => (windowListeners[type] || []).map(handler => handler(event)),
    };
    const navigator = {userAgent: ua};
    if (push) navigator.serviceWorker = push.serviceWorker;
    const pushGlobals = push ? {PushManager: function PushManager() {}, Notification: push.Notification} : {};
    const context = vm.createContext({
        console,
        document: doc,
        localStorage: page.local,
        sessionStorage: page.session,
        location,
        history,
        navigator,
        ...pushGlobals,
        atob: text => Buffer.from(String(text), 'base64').toString('binary'),
        WebSocket: FakeWebSocket,
        fetch: async (path, options = {}) => {
            const request = {
                path,
                method: options.method || 'GET',
                headers: options.headers || {},
                body: options.body === undefined ? undefined : JSON.parse(options.body),
            };
            requests.push(request);
            events.push(`fetch ${request.method} ${path}`);
            return page.serve(request);
        },
        setTimeout: () => 0,
        clearTimeout: () => {},
        setInterval: () => 0,
        clearInterval: () => {},
        innerHeight: 800,
        scrollY: 0,
        scrollTo: () => {},
        matchMedia: query => ({matches: standalone && /display-mode: standalone/.test(query)}),
        isSecureContext: secure,
        addEventListener: (type, handler) => {
            (windowListeners[type] = windowListeners[type] || []).push(handler);
        },
    });
    context.window = context;
    vm.runInContext(SOURCE, context, {filename: sourcePath});
    page.context = context;
    return page;
}

function stateWith(extra = {}) {
    return {
        type: 'state',
        connected: true,
        chat_max: 51,
        player_count: 2,
        players: [
            {id: 7, name: 'Alice', team: '1', team_name: 'Joint Ops', kills: '4', deaths: '2', ping: '40'},
            {id: 8, name: 'Bob', team: '2', team_name: 'Rebels', kills: '1', deaths: '3', ping: '160'},
        ],
        chat: [],
        missions: [
            {queue_index: 0, filename: 'AS-One.npj', display_name: 'One', is_current: true},
            {queue_index: 1, filename: 'AS-Two.npj', display_name: 'Two', is_next: true},
        ],
        settings: {},
        ...extra,
    };
}

const ALL_FEATURES = {
    pairing: true, bans: true, warn: true, private_message: true,
    weather: true, weather_presets: [{id: 'rain', label: 'Rain'}],
    lag_comp: true, manual_switch: true, set_next: true,
    ban_expiry: [{id: '', label: 'Never'}, {id: '7d', label: '7 days'}],
};

/** Lets the page's pending promises run (fetch answers, awaits). */
async function settle(rounds = 6) {
    for (let i = 0; i < rounds; i += 1) await new Promise(resolve => setImmediate(resolve));
}

const NOW = Math.floor(Date.now() / 1000);
const EVENTS = [
    {id: 1, ts: NOW - 600, kind: 'join', text: 'Kestrel joined (1 player on)', name: 'Kestrel'},
    {id: 2, ts: NOW - 300, kind: 'map', text: 'Cold Water Gap started', name: null},
    {id: 3, ts: NOW - 120, kind: 'kick', text: 'Rook was kicked by an admin', name: 'Rook'},
];
const event4 = {id: 4, ts: NOW - 20, kind: 'leave', text: 'Kestrel left', name: 'Kestrel'};
const event5 = {id: 5, ts: NOW - 5, kind: 'ban', text: 'Ali banned (name and IP, for 7 days)', name: 'Ali'};
// WolfRAT's public key: a 65-byte uncompressed P-256 point, base64url.
const KEY_BYTES = [4, ...Array.from({length: 64}, (_, i) => (i * 7 + 3) % 256)];
const PUBLIC_KEY = Buffer.from(KEY_BYTES).toString('base64url');
const PUSH_FEATURES = {...ALL_FEATURES, push: true};

const ok = (extra = {}) => respond(200, {ok: true, accepted: true, replies: ['OK'], ...extra});
const refused = message => respond(200, {ok: false, accepted: false, replies: [message], error: message});

// GET /api/alerts/prefs: a phone's Bell and Phone choices. DEFAULT_PREFS is
// what WolfRAT gives a phone that has chosen nothing yet.
const GROUPS = ['join', 'leave', 'map', 'vote', 'admin', 'server', 'objective'];
const everyGroup = value => Object.fromEntries(GROUPS.map(group => [group, value]));
const DEFAULT_PREFS = {
    groups: GROUPS,
    bell: {join: true, leave: false, map: false, vote: false, admin: true, server: true, objective: false},
    phone: {join: true, leave: false, map: false, vote: false, admin: false, server: true, objective: false},
};
const EVERY_ALERT = {groups: GROUPS, bell: everyGroup(true), phone: everyGroup(true)};

/** A server that answers GET /api/alerts/prefs with `prefs`; `rest` gets every other request. */
function withPrefs(prefs, rest = null) {
    return request => {
        if (request.path === '/api/alerts/prefs' && request.method === 'GET') return respond(200, prefs);
        return rest ? rest(request) : respond(500, {error: 'no server in this test'});
    };
}

/**
 * A page already signed in with device key "k1" and an open socket. The
 * choices it asks for on arrival (GET /api/alerts/prefs) have been answered.
 */
async function signedInPage(state = stateWith(), options = {}) {
    const page = makePage({local: {wolfkey: 'k1'}, ...options});
    await page.run('booting');
    const socket = page.sockets[0];
    socket.readyState = 1;
    socket.onopen();
    socket.onmessage({data: JSON.stringify(state)});
    await settle();
    return page;
}

/** A tap in the app on an element that matches `selector` and carries `dataset`. */
function tap(page, selector, dataset = {}, extra = {}) {
    const el = {dataset, disabled: false, classList: new FakeClassList(), ...extra};
    const target = {closest: wanted => (wanted === selector ? el : null)};
    return page.el('shell').listeners.click.map(handler => handler({target}));
}

// ------------------------------------------------------------------ cases
const cases = [];
const test = (name, fn) => cases.push([name, fn]);

test('esc() escapes & < > " and \' and keeps 0', async () => {
    const page = makePage();
    assert.strictEqual(page.run(`esc('<a href="x">&\\'</a>')`), '&lt;a href=&quot;x&quot;&gt;&amp;&#39;&lt;/a&gt;');
    assert.strictEqual(page.run('esc(0)'), '0');
    assert.strictEqual(page.run('esc(null)'), '');
    assert.strictEqual(page.run('esc(undefined)'), '');
});

test('a pairing link signs in by itself and leaves the address at once', async () => {
    const page = makePage({
        hash: '#pair=k7q-x4m',
        serve: request => {
            assert.strictEqual(request.path, '/api/pair');
            return respond(200, {ok: true, key: 'dev-key-1', device_id: 'abcd1234'});
        },
    });
    await page.run('booting');
    const pair = page.requests.find(r => r.path === '/api/pair');
    assert(pair, 'the code must be posted to /api/pair');
    assert.strictEqual(pair.method, 'POST');
    assert.strictEqual(pair.body.code, 'K7QX4M');
    assert.strictEqual(pair.body.device_name, 'Pixel 8 (Chrome)', 'plain ASCII, 40 characters at most');
    assert.strictEqual(pair.headers.Authorization, undefined, 'pairing is a public route');
    const cleared = page.events.indexOf('replaceState /');
    assert(cleared >= 0, 'the #pair= hash must be cleared');
    assert(cleared < page.events.indexOf('fetch POST /api/pair'), 'the hash goes before the request is made');
    assert.strictEqual(page.location.hash, '');
    assert.deepStrictEqual(page.local.keys(), ['wolfkey']);
    assert.strictEqual(page.local.getItem('wolfkey'), 'dev-key-1');
    assert.strictEqual(page.sockets.length, 1);
    assert.match(page.sockets[0].url, /^ws:\/\/192\.168\.1\.20:8070\/ws\?key=dev-key-1$/);
});

test('a wrong pairing code says so; a locked address says when to try again', async () => {
    const page = makePage();
    await page.run('booting');
    assert(page.shown('signin'));
    page.serve = () => respond(401, {error: 'Wrong or expired code'});
    assert.strictEqual(await page.run(`pairWithCode('ABCDEF')`), false);
    assert.match(page.el('signin-msg').textContent, /wrong or has run out/);
    assert.strictEqual(page.local.getItem('wolfkey'), null);

    page.serve = () => respond(429, {error: 'locked', retry_after: 600}, {'Retry-After': '600'});
    await page.run(`pairWithCode('ABCDEF')`);
    assert.match(page.el('signin-msg').textContent, /locked/i);
    assert.match(page.el('signin-msg').textContent, /try again in 10 min/i);

    page.serve = () => respond(400, {error: 'Bad code'});
    await page.run(`pairWithCode('ABCDEF')`);
    assert.match(page.el('signin-msg').textContent, /not a pairing code/);

    const before = page.requests.length;
    await page.run(`pairWithCode('K7Q')`);
    assert.strictEqual(page.requests.length, before, 'a short code is not sent');
    assert.match(page.el('signin-msg').textContent, /all 6 characters/);
});

test('the access token gets a device key and only the key is stored', async () => {
    const page = makePage();
    await page.run('booting');
    page.serve = request => {
        assert.strictEqual(request.path, '/api/auth');
        assert.deepStrictEqual(
            {username: request.body.username, token: request.body.token},
            {username: 'admin', token: 'master-token'},
        );
        return respond(200, {ok: true, key: 'dev-key-3', device_id: '0a0b0c0d'});
    };
    // The form's own submit listener (added by app.js, no inline handler).
    page.el('login-user').value = ' admin ';
    page.el('login-pass').value = 'master-token';
    let prevented = false;
    const submit = page.el('token-form').listeners.submit;
    assert.strictEqual(submit.length, 1, 'the token form has one submit listener');
    assert.strictEqual(await submit[0]({preventDefault: () => { prevented = true; }}), true);
    assert(prevented, 'the form does not navigate');
    assert.strictEqual(page.el('login-pass').value, '', 'the token leaves the field once used');
    assert.deepStrictEqual(page.local.keys(), ['wolfkey']);
    assert.strictEqual(page.local.getItem('wolfkey'), 'dev-key-3');
    assert.match(page.sockets[0].url, /key=dev-key-3$/);
});

test('an old saved token is swapped once for a device key', async () => {
    const page = makePage({
        local: {wolfauth: 'tok', wolftoken: 'tok', wolfuser: 'admin'},
        serve: request => {
            assert.strictEqual(request.path, '/api/auth');
            assert.strictEqual(request.body.username, 'admin');
            assert.strictEqual(request.body.token, 'tok');
            assert(request.body.device_name);
            return respond(200, {ok: true, key: 'dev-key-2', device_id: '01020304'});
        },
    });
    await page.run('booting');
    assert.deepStrictEqual(page.local.keys(), ['wolfkey']);
    assert.strictEqual(page.local.getItem('wolfkey'), 'dev-key-2');
    assert.match(page.sockets[0].url, /key=dev-key-2$/);
    assert.strictEqual(page.requests.filter(r => r.path === '/api/auth').length, 1);
});

test('an old token that no longer works is deleted; a locked one is kept', async () => {
    const wrong = makePage({
        local: {wolfauth: 'tok', wolftoken: 'tok', wolfuser: 'admin'},
        serve: () => respond(401, {error: 'Invalid username or token'}),
    });
    await wrong.run('booting');
    assert.deepStrictEqual(wrong.local.keys(), []);
    assert(wrong.shown('signin'));
    assert.match(wrong.el('signin-msg').textContent, /no longer works/);
    assert.strictEqual(wrong.sockets.length, 0);

    const locked = makePage({
        local: {wolfauth: 'tok', wolftoken: 'tok', wolfuser: 'admin'},
        serve: () => respond(429, {error: 'locked'}, {'Retry-After': '90'}),
    });
    await locked.run('booting');
    assert.deepStrictEqual(locked.local.keys(), ['wolfauth', 'wolftoken', 'wolfuser']);
    assert.match(locked.el('signin-msg').textContent, /try again in 2 min/i);

    const sessionOnly = makePage({session: {wolfauth: 'tok'}});
    await sessionOnly.run('booting');
    assert.strictEqual(sessionOnly.requests.length, 0, 'no username: nothing to swap');
    assert.strictEqual(sessionOnly.session.getItem('wolfauth'), null);
    assert(sessionOnly.shown('signin'));
});

test('a saved device key opens the socket and the app', async () => {
    const page = await signedInPage();
    assert.match(page.sockets[0].url, /\/ws\?key=k1$/);
    assert(page.shown('shell'));
    assert(!page.shown('signin'));
    assert(!page.shown('connecting'));
    assert.strictEqual(page.el('hdr-sub').textContent.indexOf('Live'), 0);
});

test('result toasts: rejected, accepted, unverified, verified, HTTP errors', async () => {
    const page = await signedInPage();
    const toast = page.el('operation-result');
    page.run(`handleWebSocketMessage({type: 'command_result', accepted: false, replies: ['ERROR denied'], error: 'ERROR denied'})`);
    assert.strictEqual(toast.dataset.outcome, 'rejected');
    assert.match(toast.textContent, /ERROR denied/);
    assert(toast.classList.contains('show'));

    page.run(`handleWebSocketMessage({type: 'command_result', accepted: true, verified: false, replies: ['OK'], verification_error: 'readback did not confirm the mutation'})`);
    assert.strictEqual(toast.dataset.outcome, 'unverified');
    assert.match(toast.textContent, /readback did not confirm/);

    page.run(`handleWebSocketMessage({type: 'command_result', accepted: true, verified: true, replies: ['State readback matched']})`);
    assert.strictEqual(toast.dataset.outcome, 'verified');
    assert.match(toast.textContent, /Verified/);

    page.run(`handleWebSocketMessage({type: 'command_result', ok: false, accepted: true, verified: null, replies: ['OK']})`);
    assert.strictEqual(toast.dataset.outcome, 'unverified');
    assert.match(toast.textContent, /read-back did not confirm/);

    page.serve = () => ok({action: 'next_map'});
    await page.run(`quickAction('next_map', 'Next map')`);
    assert.strictEqual(toast.dataset.outcome, 'accepted');
    assert.match(toast.textContent, /Next map/);
    const request = page.requests[page.requests.length - 1];
    assert.strictEqual(request.path, '/api/action');
    assert.deepStrictEqual(request.body, {action: 'next_map'});
    assert.strictEqual(request.headers.Authorization, 'Bearer k1');

    page.serve = () => respond(502, {ok: true, accepted: true, replies: ['stale proxy body'], error: 'Upstream unavailable'});
    await page.run(`quickAction('next_map', 'Next map')`);
    assert.strictEqual(toast.dataset.outcome, 'error');
    assert.match(toast.textContent, /Upstream unavailable/);

    page.serve = () => {
        throw new Error('map request failed');
    };
    await page.run(`switchMap(9, 'CP08.BMS', 'Checkpoint')`);
    assert.strictEqual(toast.dataset.outcome, 'error');
    assert.match(toast.textContent, /map request failed/);

    page.serve = () => respond(200, new Error('not json'));
    await page.run(`quickAction('next_map', 'Next map')`);
    assert.strictEqual(toast.dataset.outcome, 'error');
    assert.match(toast.textContent, /Invalid server response/);
});

test('chat over the socket: the box clears only when its own line was taken', async () => {
    const page = await signedInPage();
    const input = page.el('chat-input');
    const socket = page.sockets[0];

    input.value = 'keep this message';
    page.run('sendChat()');
    assert.deepStrictEqual(socket.sent[0], {type: 'chat', message: 'keep this message'});
    page.run(`handleWebSocketMessage({type: 'chat_result', accepted: false, replies: ['ERROR chat disabled'], error: 'ERROR chat disabled'})`);
    assert.strictEqual(input.value, 'keep this message');

    page.run('sendChat()');
    page.run(`handleWebSocketMessage({type: 'chat_result', accepted: true, replies: ['OK - Chat sent.']})`);
    assert.strictEqual(input.value, '');

    input.value = 'first message';
    page.run('sendChat()');
    input.value = 'second message';
    page.run('sendChat()');
    page.run(`handleWebSocketMessage({type: 'chat_result', accepted: true, replies: ['OK - Chat sent.']})`);
    assert.strictEqual(input.value, 'second message', 'the first result belongs to the first line');
    page.run(`handleWebSocketMessage({type: 'chat_result', accepted: true, replies: ['OK - Chat sent.']})`);
    assert.strictEqual(input.value, '');

    input.value = 'lost in the drop';
    page.run('sendChat()');
    socket.onclose({code: 1006});
    assert.strictEqual(page.run('pendingChatTexts.length'), 0, 'a dropped socket forgets its pending lines');

    input.value = '   ';
    const before = socket.sent.length;
    page.run('sendChat()');
    assert.strictEqual(socket.sent.length, before, 'nothing to send');
});

test('chat over HTTP, typed !commands and curly quotes', async () => {
    const page = await signedInPage();
    const input = page.el('chat-input');
    page.sockets[0].readyState = 3;            // socket gone: HTTP instead

    page.serve = () => refused('ERROR chat disabled');
    input.value = 'status check';
    await page.run('sendChat()');
    assert.strictEqual(page.el('operation-result').dataset.outcome, 'rejected');
    assert.strictEqual(input.value, 'status check');

    page.serve = () => ok({command: '!next'});
    input.value = '!next';
    await page.run('sendChat()');
    const sent = page.requests[page.requests.length - 1];
    assert.strictEqual(sent.path, '/api/chat/send');
    assert.deepStrictEqual(sent.body, {message: '!next'}, 'a typed command goes as typed');
    assert.strictEqual(sent.headers.Authorization, 'Bearer k1');
    assert.strictEqual(input.value, '');

    input.value = 'it\u2019s \u201cfine\u201d \u2014 ok\u2026';
    await page.run('sendChat()');
    assert.deepStrictEqual(page.requests[page.requests.length - 1].body, {message: 'it\'s "fine" - ok...'});
});

test('the counter and the box follow chat_max; a private line allows more', async () => {
    const page = await signedInPage(stateWith({chat_max: 110, features: ALL_FEATURES}));
    const input = page.el('chat-input');
    assert.strictEqual(input.maxLength, 110);
    assert.strictEqual(page.el('chat-count').textContent, '0/110');
    input.value = 'abc';
    page.run('updateCounter()');
    assert.strictEqual(page.el('chat-count').textContent, '3/110');
    page.run(`setChatTarget({pid: 7, name: 'Alice'})`);
    assert.strictEqual(input.maxLength, 118);
    assert.match(input.placeholder, /Private to Alice/);

    page.serve = () => ok({action: 'message'});
    input.value = 'please stop';
    await page.run('sendChat()');
    const sent = page.requests[page.requests.length - 1];
    assert.strictEqual(sent.path, '/api/player/action');
    assert.deepStrictEqual(sent.body, {pid: 7, name: 'Alice', action: 'message', message: 'please stop'});
    assert.strictEqual(input.value, '');

    page.run(`updateUI(${JSON.stringify(stateWith({chat_max: 110}))})`);
    assert.strictEqual(page.run('chatTarget'), null, 'no private line on a WolfRAT without it');
    assert(page.el('chat-to').classList.contains('hide'));
});

test('a 401 signs the phone out; a 429 keeps the key and says when', async () => {
    const page = await signedInPage(stateWith({features: ALL_FEATURES}));
    page.serve = () => respond(429, {error: 'locked', retry_after: 300}, {'Retry-After': '300'});
    await page.run(`quickAction('next_map', 'Next map')`);
    assert.strictEqual(page.local.getItem('wolfkey'), 'k1');
    assert.match(page.el('operation-result').textContent, /try again in 5 min/i);
    assert(page.shown('shell'));

    page.serve = () => respond(401, {error: 'Unauthorized'});
    await page.run(`setMode('manual')`);
    assert.strictEqual(page.local.getItem('wolfkey'), null);
    assert(page.shown('signin'));
    assert(!page.shown('shell'));
    assert.match(page.el('signin-msg').textContent, /signed out/);
});

test('a socket that drops asks the API at once and signs out on 401', async () => {
    const page = await signedInPage();
    page.serve = request => {
        assert.strictEqual(request.path, '/api/status');
        assert.strictEqual(request.headers.Authorization, 'Bearer k1');
        return respond(401, {error: 'Unauthorized'});
    };
    page.sockets[0].onclose({code: 4001});
    await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));
    assert.strictEqual(page.local.getItem('wolfkey'), null);
    assert(page.shown('signin'));
});

test('a 4001 close says why: token changed signs out, internet off keeps the key', async () => {
    const changed = await signedInPage();
    changed.serve = () => respond(401, {error: 'Unauthorized'});
    changed.sockets[0].onclose({code: 4001, reason: 'token changed'});
    await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));
    assert.strictEqual(changed.local.getItem('wolfkey'), null);
    assert.match(changed.el('signin-msg').textContent, /token was changed/);

    const outside = await signedInPage();
    const gate = 'This WolfRAT only takes phones on its own network.';
    outside.serve = () => respond(403, {error: gate});
    outside.sockets[0].onclose({code: 4001, reason: 'internet access turned off'});
    await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));
    assert.strictEqual(outside.local.getItem('wolfkey'), 'k1', 'the key still works at home');
    assert(outside.shown('shell'));
    assert.strictEqual(outside.el('linkbar').textContent, gate);
    assert(outside.el('linkbar').classList.contains('bad'));
});

test('hostile names and lines are escaped everywhere', async () => {
    const evil = '<img src=x onerror=alert(1)>"\'&';
    const page = await signedInPage(stateWith({
        features: ALL_FEATURES,
        players: [{id: 9, name: evil, team: '2', kills: '1', deaths: '0', ping: '70', rank: '<b>boss</b>', country: '<i>'}],
        chat: [
            {id: 1, time: '<t>', text: `${evil}: <script>alert(2)</script>`, kind: 'player', name: evil},
            {id: 2, time: '17:00', text: '<svg onload=alert(3)>', kind: 'wolfrat', own: true},
            {id: 3, time: '17:01', text: '[ADMIN] <b>hi</b>', kind: 'wolfrat', own: true},
        ],
        missions: [{queue_index: 0, filename: '<x>.npj', display_name: '"><img>', is_current: true}],
        server: {name: '<h1>srv</h1>', map_title: '<u>map</u>'},
    }));
    const escaped = '&lt;img src=x onerror=alert(1)&gt;&quot;&#39;&amp;';
    const players = page.el('plist').innerHTML;
    assert(players.includes(escaped), players);
    assert(!players.includes('<img src=x'), 'a player name must never become markup');
    assert(players.includes('&lt;b&gt;boss&lt;/b&gt;'));
    assert(players.includes(`data-pname="${escaped}"`));

    const chat = page.el('chat-messages').innerHTML;
    assert(!chat.includes('<script>'), chat);
    assert(!chat.includes('<svg onload'), chat);
    assert(!chat.includes('<b>hi</b>'), chat);
    assert(chat.includes('&lt;script&gt;alert(2)&lt;/script&gt;'));
    assert(chat.includes('&lt;t&gt;'));

    const maps = page.el('map-list').innerHTML;
    assert(!maps.includes('"><img>'));
    assert(maps.includes('&quot;&gt;&lt;img&gt;'));
    assert(maps.includes('data-filename="&lt;x&gt;.npj"'));
    assert.strictEqual(page.el('hdr-name').textContent, '<h1>srv</h1>', 'plain text goes in through textContent');

    page.run(`openPlayerSheet(9, ${JSON.stringify(evil)})`);
    const sheet = page.el('sheet-body').innerHTML;
    assert(!sheet.includes('<img src=x'));
    assert(sheet.includes(escaped));

    page.run(`devices = [{id: '"><b>', name: ${JSON.stringify(evil)}, created: 1700000000, last_seen: 1700000000, last_ip: '<ip>'}]; renderDevices()`);
    const list = page.el('dev-list').innerHTML;
    assert(!list.includes('<img src=x'));
    assert(!list.includes('<ip>'));
    assert(list.includes('data-signout="&quot;&gt;&lt;b&gt;"'));
});

test('Kill, Swap and Zero ask first; a refusal keeps the player open', async () => {
    const page = await signedInPage(stateWith({features: ALL_FEATURES}));
    assert.strictEqual(page.run(`openPlayerSheet(7, 'Alice')`), true);
    assert(page.el('sheet').classList.contains('on'));
    assert.strictEqual(page.run('sheet.kind'), 'player');

    const before = page.requests.length;
    page.run(`sheetAct('kill')`);
    assert.strictEqual(page.run('sheet.kind'), 'confirm');
    assert.strictEqual(page.requests.length, before, 'nothing is sent before the confirm');

    let body = null;
    page.serve = request => {
        body = request.body;
        return refused('ERROR player no longer exists');
    };
    await page.run(`sheetAct('yes')`);
    assert.deepStrictEqual(body, {pid: 7, name: 'Alice', action: 'kill'});
    assert.strictEqual(page.run('sheet && sheet.kind'), 'player', 'a refused action goes back to the player');
    assert.strictEqual(page.el('operation-result').dataset.outcome, 'rejected');

    page.serve = request => {
        body = request.body;
        return ok({action: 'kill', pid: 7});
    };
    page.run(`sheetAct('kill')`);
    await page.run(`sheetAct('yes')`);
    assert.strictEqual(page.run('sheet'), null, 'an accepted action closes the sheet');
    assert(!page.el('sheet').classList.contains('on'));

    for (const action of ['swap', 'zero']) {
        page.run(`openPlayerSheet(7, 'Alice')`);
        page.run(`sheetAct('${action}')`);
        assert.strictEqual(page.run('sheet.kind'), 'confirm');
        page.run(`sheetAct('no')`);
        assert.strictEqual(page.run('sheet.kind'), 'player', 'Cancel goes back to the player');
        page.run(`sheetAct('${action}')`);
        await page.run(`sheetAct('yes')`);
        assert.deepStrictEqual(body, {pid: 7, name: 'Alice', action});
    }
});

test('Kick and Warn send the reason; Ban asks how long', async () => {
    const page = await signedInPage(stateWith({features: ALL_FEATURES}));
    let body = null;
    page.serve = request => {
        body = request.body;
        return ok();
    };
    page.run(`openPlayerSheet(8, 'Bob')`);
    page.run(`sheetAct('reason', {dataset: {reason: 'Team killing'}, classList: {contains: () => false, add() {}}})`);
    await page.run(`sheetAct('kick')`);
    assert.deepStrictEqual(body, {pid: 8, name: 'Bob', action: 'kick', reason: 'Team killing'});

    page.run(`openPlayerSheet(8, 'Bob')`);
    body = null;
    await page.run(`sheetAct('warn')`);
    assert.strictEqual(body, null, 'a warning needs a reason');
    assert.match(page.el('operation-result').textContent, /reason/);
    page.run(`sheetAct('reason', {dataset: {reason: 'Bad language'}, classList: {contains: () => false, add() {}}})`);
    await page.run(`sheetAct('warn')`);
    assert.deepStrictEqual(body, {pid: 8, name: 'Bob', action: 'warn', reason: 'Bad language'});

    page.run(`openPlayerSheet(8, 'Bob')`);
    page.run(`sheetAct('reason', {dataset: {reason: 'Cheating'}, classList: {contains: () => false, add() {}}})`);
    page.run(`sheetAct('ban')`);
    assert.strictEqual(page.run('sheet.kind'), 'confirm');
    const html = page.el('sheet-body').innerHTML;
    assert(html.includes('data-id="7d"'), 'the expiry choices come from features.ban_expiry');
    assert(html.includes('Never'));
    page.run(`sheetAct('pick', {dataset: {id: '7d'}})`);
    await page.run(`sheetAct('yes')`);
    assert.deepStrictEqual(body, {pid: 8, name: 'Bob', action: 'ban', reason: 'Cheating', expiry: '7d'});
});

test('the player sheet hides what features does not offer', async () => {
    const page = await signedInPage(stateWith());
    page.run(`openPlayerSheet(7, 'Alice')`);
    const html = page.el('sheet-body').innerHTML;
    assert(!html.includes('data-act="message"'));
    assert(!html.includes('data-act="ban"'));
    assert(!html.includes('data-act="warn"'));
    assert(html.includes('data-act="kick"'));
    assert(html.includes('data-act="kill"'));
    page.run(`sheetAct('ban')`);
    assert.strictEqual(page.run('sheet.kind'), 'player', 'no ban without features.bans');

    page.run(`closeSheet(); updateUI(${JSON.stringify(stateWith({features: ALL_FEATURES}))}); openPlayerSheet(7, 'Alice')`);
    const full = page.el('sheet-body').innerHTML;
    assert(full.includes('data-act="message"'));
    assert(full.includes('data-act="ban"'));
    assert(full.includes('data-act="warn"'));
    assert.strictEqual(page.run(`openPlayerSheet(99, 'Ghost')`), false, 'a player who left is not opened');
});

test('the page hides features this WolfRAT cannot do', async () => {
    const lean = await signedInPage(stateWith());
    for (const id of ['qa-set-next', 'qa-weather', 'chat-to', 'tile-weather', 'tile-lagcomp', 'tile-mode', 'mode-card', 'mp-change']) {
        assert(lean.el(id).classList.contains('hide'), `${id} must be hidden without its feature`);
    }
    assert(lean.shown('wx-off'));
    assert(lean.shown('lc-off'));

    const full = await signedInPage(stateWith({
        features: ALL_FEATURES,
        lag_comp: {enabled: true, tune_mode: 'watch', summary: null},
        weather: {summary: 'Rain', fog_m: 150, dynamic: false},
        manual_mode: 'manual',
    }));
    for (const id of ['qa-set-next', 'qa-weather', 'chat-to', 'tile-weather', 'tile-lagcomp', 'tile-mode', 'mode-card', 'mp-change', 'wx-on', 'lc-on', 'hdr-manual']) {
        assert(full.shown(id), `${id} must show with its feature`);
    }
    assert(full.el('wx-presets').innerHTML.includes('data-preset="rain"'));
});

test('missing and null fields from an older WolfRAT do not break the page', async () => {
    const page = await signedInPage({type: 'state'});
    page.run(`updateUI({type: 'state', players: null, chat: null, missions: null, server: null, teams: null, vote: null, weather: null, lag_comp: null, features: null, next_map: null, settings: null, events: null, events_last_id: null})`);
    page.run(`updateUI({type: 'state', connected: false, players: [{id: 'x', name: ''}, null, {id: 3}], missions: [{filename: ''}, null], chat: [null, {text: null}], settings: {GameTime: '12/30'}})`);
    assert.strictEqual(page.el('hdr-sub').textContent, 'No link to the game server');
    assert.strictEqual(page.el('now-left').textContent, '12', 'time left falls back to settings.gametime');
    assert.match(page.el('plist').innerHTML, /No players/);
    assert(!page.shown('hdr-bell'), 'no alerts from this WolfRAT: no bell');
    assert(page.shown('tile-notify'), 'the Notifications tile is always there');

    page.run(`updateUI({type: 'state', events: [null, {id: 'x'}, {id: 2.5}, {id: -1}, {id: true}, {id: 3, kind: null, text: null, name: null, ts: null}], events_last_id: 'abc', features: {push: true}, server: {secure_address: null}})`);
    assert(page.shown('hdr-bell'));
    assert.deepStrictEqual(page.json('currentEvents.map(e => e.id)'), [3], 'only whole ids from 0 up');
    page.run('openAlerts()');
    assert.match(page.el('sheet-body').innerHTML, /<div class="al"><span class="ic"><svg class="i sm"><use href="#info"\/>/);
    page.run('closeSheet()');
    page.serve = () => respond(404, {error: 'Not Found'});
    await page.run(`go('notify')`);
    await settle();
    assert.match(page.el('nt-count').textContent, /\u2014/, 'no setting from this WolfRAT');
    assert(page.el('nt-plus').disabled && page.el('nt-minus').disabled);
    assert(!page.shown('nt-secure-link'));
    assert(!page.shown('nt-prefs'), 'no Bell and Phone choices on this WolfRAT: no rows');

    // The new state keys as null, and as things they should never be.
    page.run(`updateUI({type: 'state', objective: null, sky: null, time_restore: null, features: {sky: true, sky_scenes: null, time_left: null}})`);
    page.run(`updateUI({type: 'state', objective: 'zones', sky: 'night', time_restore: 7, features: {sky: 1, sky_scenes: {a: 1}, time_left: 'yes'}})`);
    for (const kind of ['zones', 'hill_team', 'hill_solo', 'flags', 'coop', 'something new', 7]) {
        page.run(`updateUI(${JSON.stringify({type: 'state', objective: {kind}, sky: {}, time_restore: {}})})`);
        page.run(`updateUI(${JSON.stringify({type: 'state', objective: {kind, zones: [null, 5, {}], teams: [null, 'x'], players: [null, {}, {name: null}], caps: 3, goal: 'x', held: 2}})})`);
    }
    assert.match(page.el('now-teams').innerHTML, /class="team jo"/, 'an unknown kind keeps the two teams');
});

test('maps: Run now asks first and keeps the queue identity; Set next posts the map', async () => {
    const page = await signedInPage(stateWith({
        features: ALL_FEATURES,
        missions: [
            {queue_index: 4, filename: 'CP08.BMS', display_name: 'Checkpoint', is_current: true},
            {queue_index: 9, filename: 'CP08.BMS', display_name: 'Checkpoint', is_current: false},
        ],
    }));
    const rendered = page.el('map-list').innerHTML;
    assert.match(rendered, /data-queue-index="4"/);
    assert.match(rendered, /data-queue-index="9"/, 'the duplicate keeps its own queue identity');
    assert.doesNotMatch(rendered, /revision/);

    let request = null;
    page.serve = r => {
        request = r;
        return ok();
    };
    page.run(`openMapSheet(9, 'CP08.BMS')`);
    page.run(`sheetAct('run')`);
    assert.strictEqual(page.run('sheet.kind'), 'confirm');
    assert.strictEqual(request, null);
    await page.run(`sheetAct('yes')`);
    assert.strictEqual(request.path, '/api/map/switch');
    assert.deepStrictEqual(request.body, {index: 9, map: 'CP08.BMS'});

    page.run(`openMapSheet(9, 'CP08.BMS')`);
    await page.run(`sheetAct('next')`);
    assert.strictEqual(request.path, '/api/map/setnext');
    assert.deepStrictEqual(request.body, {filename: 'CP08.BMS', index: 9});

    request = null;
    page.run(`quickActionTap('next_map')`);
    assert.strictEqual(page.run('sheet.kind'), 'confirm');
    assert.strictEqual(request, null);
    await page.run(`sheetAct('yes')`);
    assert.strictEqual(request.path, '/api/action');
    assert.deepStrictEqual(request.body, {action: 'next_map'});
});

test('weather, fog, lag comp and mode post what the contract says', async () => {
    const page = await signedInPage(stateWith({
        features: ALL_FEATURES,
        lag_comp: {enabled: true, tune_mode: 'off', summary: 'On'},
    }));
    const posted = [];
    page.serve = r => {
        posted.push([r.path, r.body]);
        return ok();
    };
    await page.run(`setWeatherPreset('rain', 'Rain')`);
    await page.run('setFog(150)');
    await page.run('setFog(20)');
    await page.run('setFog(1000)');
    await page.run('setLagComp(false)');
    await page.run(`setTuneMode('watch')`);
    await page.run(`setMode('manual')`);
    await page.run(`setMode('undecided')`);
    assert.deepStrictEqual(posted, [
        ['/api/weather', {preset: 'rain'}],
        ['/api/weather', {fog_m: 150}],
        ['/api/weather', {fog_m: 60}],
        ['/api/weather', {fog_m: 400}],
        ['/api/lagcomp', {enabled: false}],
        ['/api/lagcomp', {tune_mode: 'watch'}],
        ['/api/mode', {mode: 'manual'}],
    ]);
});

test('chat lines: team colours, WolfRAT pills, admin bubbles, old servers', async () => {
    const page = await signedInPage(stateWith({
        chat: [
            {id: 1, time: '17:00', text: 'Alice: hello', kind: 'player', name: 'Alice'},
            {id: 2, time: '17:01', text: 'Bob: !rank', kind: 'player', name: 'Bob'},
            {id: 3, time: '17:02', text: 'Alice drew first blood!', kind: 'wolfrat', own: true},
            {id: 4, time: '17:03', text: '[ADMIN] be nice', kind: 'wolfrat', own: true},
            {id: 5, time: '17:04', text: 'Server restarting', kind: 'console'},
            {time: '17:05', text: 'Bob: no kind field'},
            {time: '17:06', text: 'An old WolfRAT line', own: true},
        ],
    }));
    const html = page.el('chat-messages').innerHTML;
    assert.match(html, /<div class="m jo"><div class="who">Alice/);
    assert.match(html, /<div class="m reb cmd"><div class="who">Bob/);
    assert.match(html, /<div class="sys"><img[^>]*><span>Alice drew first blood!<\/span>/);
    assert.match(html, /<div class="m me"><div class="who">Web Admin <time>17:03<\/time><\/div><div class="body">be nice<\/div>/);
    assert.match(html, /<div class="sys con">/);
    assert.match(html, /no kind field/);
    assert.match(html, /<div class="sys"><img[^>]*><span>An old WolfRAT line/);

    page.run(`setChatFilter('commands')`);
    const commands = page.el('chat-messages').innerHTML;
    assert(commands.includes('!rank'));
    assert(!commands.includes('hello'));
    page.run(`setChatFilter('wolfrat')`);
    assert(!page.el('chat-messages').innerHTML.includes('hello'));
});

test('new chat while on another screen marks the Chat tab until it is opened', async () => {
    const page = await signedInPage(stateWith({chat: [{id: 1, time: '17:00', text: 'Alice: hi', kind: 'player', name: 'Alice'}]}));
    assert(page.el('nav-chat').classList.contains('hide'), 'lines already there on arrival are not new');
    page.run(`handleWebSocketMessage({type: 'chat', chat: [{id: 1, time: '17:00', text: 'Alice: hi', kind: 'player', name: 'Alice'}, {id: 2, time: '17:01', text: 'Bob: yo', kind: 'player', name: 'Bob'}]})`);
    assert(!page.el('nav-chat').classList.contains('hide'), 'a new line marks the tab');
    assert(page.el('chat-messages').innerHTML.includes('yo'));
    page.run(`go('chat')`);
    assert(page.el('nav-chat').classList.contains('hide'), 'opening Chat clears the mark');
    assert.strictEqual(page.location.hash, '#chat');
});

test('Log out ends this key on the server and forgets it', async () => {
    const page = await signedInPage(stateWith({features: ALL_FEATURES}));
    page.serve = request => {
        assert.strictEqual(request.path, '/api/logout');
        assert.strictEqual(request.headers.Authorization, 'Bearer k1');
        return respond(200, {ok: true});
    };
    page.run('askLogout()');
    assert.strictEqual(page.run('sheet.kind'), 'confirm');
    await page.run(`sheetAct('yes')`);
    assert.strictEqual(page.local.getItem('wolfkey'), null);
    assert(page.shown('signin'));
    assert.match(page.el('signin-msg').textContent, /Signed out/);
    assert.strictEqual(page.sockets[0].onmessage, null, 'the socket is let go');
});

test('devices: the list loads with the key and another phone can be signed out', async () => {
    const page = await signedInPage(stateWith({features: ALL_FEATURES}));
    page.serve = request => {
        if (request.path === '/api/devices') {
            return respond(200, {devices: [
                {id: 'aa11bb22', name: 'Pixel 8', created: 1700000000, last_seen: Date.now() / 1000, last_ip: '192.168.1.20', this_device: true},
                {id: 'cc33dd44', name: "Tinman's phone", created: '2026-11-20T10:00:00Z', last_seen: null, last_ip: null, this_device: false},
            ]});
        }
        assert.strictEqual(request.path, '/api/devices/signout');
        assert.deepStrictEqual(request.body, {id: 'cc33dd44'});
        return respond(200, {ok: true});
    };
    await page.run('loadDevices()');
    const html = page.el('dev-list').innerHTML;
    assert(html.includes('<b>Pixel 8</b>'));
    assert(html.includes('YOU'), 'this phone is marked');
    assert(html.includes('not seen yet'));
    assert(html.includes('Paired 20 Nov'), 'an ISO date works too');
    assert(html.includes('data-signout="cc33dd44"'));
    assert(!html.includes('data-signout="aa11bb22"'), 'this phone signs out with Log out');
    page.run(`askSignOutDevice('cc33dd44', "Tinman's phone")`);
    await page.run(`sheetAct('yes')`);
    assert.strictEqual(page.el('operation-result').dataset.outcome, 'accepted');
});

// ---------------------------------------------------------- the bell
test('the bell shows a red dot for alerts this phone has not seen', async () => {
    const page = await signedInPage(stateWith({events: EVENTS, events_last_id: 3}));
    assert(page.shown('hdr-bell'));
    assert(page.shown('hdr-pip'), 'nothing seen yet on this phone');

    const seen = await signedInPage(stateWith({events: EVENTS, events_last_id: 3}),
        {local: {wolfkey: 'k1', wolfseen: '3'}, serve: withPrefs(EVERY_ALERT)});
    assert(!seen.shown('hdr-pip'), 'all seen');
    seen.run(`handleWebSocketMessage(${JSON.stringify({type: 'event', event: event4})})`);
    assert(seen.shown('hdr-pip'), 'a new alert from the socket lights it');
    assert.deepStrictEqual(seen.json('currentEvents.map(e => e.id)'), [1, 2, 3, 4]);
    assert.strictEqual(seen.local.getItem('wolfseen'), '3', 'only opening the sheet marks them read');

    const older = await signedInPage(stateWith());
    assert(!older.shown('hdr-bell'), 'a WolfRAT that sends no alerts gets no bell');
});

test('the Alerts sheet: newest first, icon per kind, name in bold; opening marks all read', async () => {
    const page = await signedInPage(stateWith({events: EVENTS, events_last_id: 3}),
        {local: {wolfkey: 'k1', wolfseen: '1'}, serve: withPrefs(EVERY_ALERT)});
    page.el('hdr-bell').click();
    assert.strictEqual(page.run('sheet.kind'), 'alerts');
    const html = page.el('sheet-body').innerHTML;
    assert.match(html, /<p class="sh-title">Alerts<\/p>/);
    assert(html.indexOf('Rook') < html.indexOf('Cold Water Gap'), 'newest first');
    assert(html.indexOf('Cold Water Gap') < html.indexOf('Kestrel'));
    assert(html.includes('<div class="tx"><b>Kestrel</b> joined (1 player on)</div><small>10 min ago</small>'), html);
    assert(html.includes('<div class="tx"><b>Rook</b> was kicked by an admin</div><small>2 min ago</small>'));
    assert(html.includes('<div class="tx">Cold Water Gap started</div><small>5 min ago</small>'));
    assert(html.includes('<span class="ic k-join"><svg class="i sm"><use href="#join"/>'));
    assert(html.includes('<span class="ic k-map"><svg class="i sm"><use href="#map"/>'));
    assert(html.includes('<span class="ic k-kick"><svg class="i sm"><use href="#kick"/>'));
    assert.strictEqual((html.match(/class="al new"/g) || []).length, 2, 'the two not seen before are marked new');
    assert.strictEqual(page.local.getItem('wolfseen'), '3');
    assert(!page.shown('hdr-pip'));

    page.run(`handleWebSocketMessage(${JSON.stringify({type: 'event', event: event4})})`);
    assert(page.el('sheet-body').innerHTML.indexOf('left') < page.el('sheet-body').innerHTML.indexOf('Rook'), 'a new one shows at the top');
    assert.strictEqual(page.local.getItem('wolfseen'), '4', 'seen while the sheet is open');
    assert(!page.shown('hdr-pip'));
    page.run('closeSheet()');
    page.run(`handleWebSocketMessage(${JSON.stringify({type: 'event', event: event5})})`);
    assert(page.shown('hdr-pip'));

    const first = await signedInPage(stateWith({events: EVENTS, events_last_id: 3}), {serve: withPrefs(EVERY_ALERT)});
    first.run('openAlerts()');
    assert(!first.el('sheet-body').innerHTML.includes('al new'), 'nothing seen before: no row is marked, all are new');
    assert.strictEqual(first.local.getItem('wolfseen'), '3');

    const empty = await signedInPage(stateWith({events: [], events_last_id: 0}));
    assert(!empty.shown('hdr-pip'));
    empty.run('openAlerts()');
    assert.match(empty.el('sheet-body').innerHTML, /<p class="empty">Nothing yet<\/p>/);
});

test('alert names, texts and kinds are escaped', async () => {
    const evil = '<img src=x onerror=alert(1)>';
    const page = await signedInPage(stateWith({
        events: [
            {id: 1, ts: NOW, kind: 'join" onclick="x', text: `${evil} joined <b>now</b>`, name: evil},
            {id: 2, ts: NOW, kind: 'warn', text: 'Warned someone', name: '"><svg onload=alert(2)>'},
        ],
        events_last_id: 2,
    }));
    page.run('openAlerts()');
    const html = page.el('sheet-body').innerHTML;
    assert(!html.includes('<img'), html);
    assert(!html.includes('<svg onload'), html);
    assert(!html.includes('onclick="'), 'an unknown kind never reaches the markup');
    assert(html.includes('<b>&lt;img src=x onerror=alert(1)&gt;</b> joined &lt;b&gt;now&lt;/b&gt;'));
    assert(html.includes('<b>&quot;&gt;&lt;svg onload=alert(2)&gt;</b> \u00b7 Warned someone'), 'a name not in the text goes in front');
    assert(html.includes('<use href="#alert"/>'), 'warn uses the warning icon');
});

test('after a reconnect the missed alerts are fetched, each kept once', async () => {
    const page = await signedInPage(stateWith({events: EVENTS, events_last_id: 3}));
    assert(!page.requests.some(r => r.path.startsWith('/api/events')), 'the first state is enough on arrival');
    page.sockets[0].onclose({code: 1006});
    page.serve = request => {
        if (request.path.startsWith('/api/events')) {
            return respond(200, {events: [EVENTS[2], event4, event5], last_id: 5});
        }
        return respond(200, stateWith({events: EVENTS, events_last_id: 3}));
    };
    page.run('connectWS()');
    const socket = page.sockets[1];
    socket.readyState = 1;
    socket.onopen();
    await settle();
    const fetched = page.requests.filter(r => r.path.startsWith('/api/events'));
    assert.strictEqual(fetched.length, 1);
    assert.strictEqual(fetched[0].path, '/api/events?after=3');
    assert.strictEqual(fetched[0].headers.Authorization, 'Bearer k1');
    socket.onmessage({data: JSON.stringify(stateWith({events: [...EVENTS, event4, event5], events_last_id: 5}))});
    socket.onmessage({data: JSON.stringify({type: 'event', event: event5})});
    assert.deepStrictEqual(page.json('currentEvents.map(e => e.id)'), [1, 2, 3, 4, 5]);
    assert(page.shown('hdr-pip'));
});

test('a WolfRAT restart starts the alerts again; a late state does not', async () => {
    const page = await signedInPage(stateWith({events: EVENTS, events_last_id: 3}), {local: {wolfkey: 'k1', wolfseen: '3'}});
    // A newer alert on the socket, then a state made a moment before it.
    page.run(`handleWebSocketMessage(${JSON.stringify({type: 'event', event: event4})})`);
    page.run(`updateUI(${JSON.stringify(stateWith({events: EVENTS, events_last_id: 3}))})`);
    assert.deepStrictEqual(page.json('currentEvents.map(e => e.id)'), [1, 2, 3, 4]);
    assert.strictEqual(page.local.getItem('wolfseen'), '3');

    // WolfRAT restarted: its ids begin at 1 again.
    const fresh = {id: 1, ts: NOW, kind: 'join', text: 'Moose.UK joined (1 player on)', name: 'Moose.UK'};
    page.run(`updateUI(${JSON.stringify(stateWith({events: [fresh], events_last_id: 1}))})`);
    assert.deepStrictEqual(page.json('currentEvents.map(e => e.text)'), ['Moose.UK joined (1 player on)']);
    assert.strictEqual(page.local.getItem('wolfseen'), null, 'the old seen mark goes');
    assert(page.shown('hdr-pip'));

    const reloaded = await signedInPage(stateWith({events: EVENTS, events_last_id: 3}), {local: {wolfkey: 'k1', wolfseen: '57'}});
    assert(reloaded.shown('hdr-pip'), 'a seen mark from before a restart does not hide new alerts');
});

// ------------------------------------------------------- notifications
test('Notifications on plain HTTP: one line, and the secure address as a link', async () => {
    const page = await signedInPage(stateWith({
        features: PUSH_FEATURES,
        server: {name: 'srv', secure_address: 'https://wolfrat.example.com'},
    }));
    assert(page.shown('tile-notify'));
    let stray = null;
    page.serve = withPrefs(DEFAULT_PREFS, request => {
        if (request.path !== '/api/push') stray = request.path;
        return respond(200, {public_key: PUBLIC_KEY, subscribed: false, first_joins: 2});
    });
    page.run(`go('notify')`);
    await settle();
    assert(page.shown('nt-insecure'));
    assert(!page.shown('nt-phone'), 'no Turn on button where it cannot work');
    assert(!page.shown('nt-install-card'), 'installing needs the secure address too');
    assert(page.shown('nt-secure-link'));
    assert.strictEqual(page.el('nt-secure-link').getAttribute('href'), 'https://wolfrat.example.com');
    assert.strictEqual(page.el('nt-secure-text').textContent, 'https://wolfrat.example.com');
    assert.strictEqual(stray, null, 'opening the screen asks for the push settings and the choices only');
    assert.strictEqual(page.el('nt-count').textContent, '2', 'the join setting works from here too');

    page.run(`updateUI(${JSON.stringify(stateWith({features: PUSH_FEATURES, server: {secure_address: 'javascript:alert(1)'}}))})`);
    assert(!page.shown('nt-secure-link'), 'only an https address becomes a link');
    assert.strictEqual(page.el('nt-secure-link').getAttribute('href'), '/');
    page.run(`updateUI(${JSON.stringify(stateWith({features: PUSH_FEATURES}))})`);
    assert(!page.shown('nt-secure-link'));
    assert(page.shown('nt-insecure'));
    page.run(`updateUI(${JSON.stringify(stateWith({features: {...ALL_FEATURES, push: false}}))})`);
    assert(page.shown('tile-notify'), 'the tile stays: the bell choices are there too');
    assert(!page.shown('nt-joins-card'), 'no first-joins number where WolfRAT cannot notify');
});

test('Notifications on HTTPS: turn on asks, subscribes with the key and posts the subscription', async () => {
    const env = fakePush();
    const page = await signedInPage(stateWith({features: PUSH_FEATURES}), {secure: true, push: env});
    const posted = [];
    let pushState = {public_key: PUBLIC_KEY, subscribed: false, first_joins: 2};
    page.serve = request => {
        posted.push([request.path, request.body]);
        if (request.path === '/api/push') return respond(200, pushState);
        if (request.path === '/api/push/subscribe') {
            pushState = {...pushState, subscribed: true};
            return respond(200, {ok: true});
        }
        if (request.path === '/api/push/unsubscribe') {
            pushState = {...pushState, subscribed: false};
            return respond(200, {ok: true});
        }
        if (request.path === '/api/push/test') return respond(200, {ok: true});
        return respond(404, {error: 'Not Found'});
    };
    page.run(`go('notify')`);
    await settle();
    assert(!page.shown('nt-insecure'));
    assert(page.shown('nt-phone'));
    assert(page.shown('nt-on'));
    assert(!page.shown('nt-test') && !page.shown('nt-off'));
    assert.match(page.el('nt-status').textContent, /^Off/);

    await page.run('turnOnNotifications()');
    assert.strictEqual(env.permissionAsked, 1);
    assert.strictEqual(env.subscribeCalls.length, 1);
    assert.strictEqual(env.subscribeCalls[0].userVisibleOnly, true);
    assert.deepStrictEqual(Array.from(env.subscribeCalls[0].applicationServerKey), KEY_BYTES, 'applicationServerKey = public_key');
    const subscribe = posted.find(([p]) => p === '/api/push/subscribe');
    assert.deepStrictEqual(subscribe[1], {subscription: {
        endpoint: 'https://fcm.googleapis.com/fcm/send/fake-endpoint',
        expirationTime: null,
        keys: {p256dh: 'BPfake', auth: 'AUfake'},
    }});
    assert(page.shown('nt-test') && page.shown('nt-off'));
    assert(!page.shown('nt-on'));
    assert.strictEqual(page.el('nt-chip').textContent, 'ON');

    await page.run('sendTestNotification()');
    assert.deepStrictEqual(posted[posted.length - 1], ['/api/push/test', {}]);
    assert.strictEqual(page.el('operation-result').dataset.outcome, 'accepted');

    await page.run('turnOffNotifications()');
    assert(posted.some(([p]) => p === '/api/push/unsubscribe'));
    assert.strictEqual(env.unsubscribed, 1, 'the browser lets go of it too');
    assert(page.shown('nt-on'));
    assert.strictEqual(page.el('nt-chip').textContent, 'OFF');

    // The buttons are wired.
    page.el('nt-on').click();
    await settle();
    assert.strictEqual(env.subscribeCalls.length, 2);
});

test('Notifications: blocked, another key, a refused subscription, no Push API', async () => {
    const blocked = fakePush({answer: 'denied'});
    const page = await signedInPage(stateWith({features: PUSH_FEATURES}), {secure: true, push: blocked});
    page.serve = request => respond(200, request.path === '/api/push'
        ? {public_key: PUBLIC_KEY, subscribed: false, first_joins: 2} : {ok: true});
    page.run(`go('notify')`);
    await settle();
    await page.run('turnOnNotifications()');
    assert.strictEqual(blocked.subscribeCalls.length, 0);
    assert(!page.requests.some(r => r.path === '/api/push/subscribe'));
    assert.match(page.el('operation-result').textContent, /blocked/);
    assert.strictEqual(page.el('nt-chip').textContent, 'BLOCKED');

    const oldKey = fakePush({permission: 'granted', subscribedWith: new Uint8Array(65).fill(9)});
    const rekey = await signedInPage(stateWith({features: PUSH_FEATURES}), {secure: true, push: oldKey});
    rekey.serve = request => respond(200, request.path === '/api/push'
        ? {public_key: PUBLIC_KEY, subscribed: true, first_joins: 2} : {ok: true});
    rekey.run(`go('notify')`);
    await settle();
    await rekey.run('turnOnNotifications()');
    assert.strictEqual(oldKey.permissionAsked, 0, 'already allowed');
    assert.strictEqual(oldKey.unsubscribed, 1, 'a subscription for another key is dropped');
    assert.strictEqual(oldKey.subscribeCalls.length, 1);

    const refused = fakePush({permission: 'granted'});
    const bad = await signedInPage(stateWith({features: PUSH_FEATURES}), {secure: true, push: refused});
    bad.serve = request => (request.path === '/api/push'
        ? respond(200, {public_key: PUBLIC_KEY, subscribed: false, first_joins: 2})
        : respond(400, {ok: false, error: 'Unknown push service'}));
    bad.run(`go('notify')`);
    await settle();
    await bad.run('turnOnNotifications()');
    assert.match(bad.el('operation-result').textContent, /Unknown push service/);
    assert(bad.shown('nt-on'), 'still off');

    const iphone = await signedInPage(stateWith({features: PUSH_FEATURES}), {secure: true, ua: IPHONE_UA});
    iphone.serve = () => respond(200, {public_key: PUBLIC_KEY, subscribed: false, first_joins: 2});
    iphone.run(`go('notify')`);
    await settle();
    assert(iphone.shown('nt-phone'));
    assert(!iphone.shown('nt-on'));
    assert.match(iphone.el('nt-status').textContent, /Home Screen/);
    assert.strictEqual(iphone.el('nt-install-text').textContent, 'On iPhone: tap Share, then Add to Home Screen.');
});

test('the first-joins stepper posts /api/push/settings and stays within 0-5', async () => {
    const page = await signedInPage(stateWith({features: PUSH_FEATURES}));
    const posted = [];
    let refuse = false;
    page.serve = withPrefs(DEFAULT_PREFS, request => {
        if (request.path === '/api/push') return respond(200, {public_key: PUBLIC_KEY, subscribed: false, first_joins: 2});
        posted.push([request.path, request.body]);
        if (refuse) return respond(200, {ok: false, error: 'Not saved'});
        return respond(200, {ok: true, first_joins: request.body.first_joins});
    });
    page.run(`go('notify')`);
    await settle();
    assert.strictEqual(page.el('nt-count').textContent, '2');
    page.el('nt-plus').click();
    await settle();
    assert.deepStrictEqual(posted, [['/api/push/settings', {first_joins: 3}]]);
    assert.strictEqual(page.el('nt-count').textContent, '3');
    await page.run('setFirstJoins(5)');
    assert(page.el('nt-plus').disabled, 'five at most');
    await page.run('setFirstJoins(9)');
    assert.strictEqual(posted.length, 2, 'already at the top: nothing sent');
    await page.run('setFirstJoins(0)');
    assert.deepStrictEqual(posted[posted.length - 1], ['/api/push/settings', {first_joins: 0}]);
    assert(page.el('nt-minus').disabled);
    assert(!page.el('nt-plus').disabled);
    refuse = true;
    page.el('nt-plus').click();
    await settle();
    assert.strictEqual(page.el('nt-count').textContent, '0', 'a refusal puts the number back');
    assert.match(page.el('operation-result').textContent, /Not saved/);
});

test('Install: the browser prompt becomes an Install app button; the app says it is installed', async () => {
    const env = fakePush();
    const page = await signedInPage(stateWith({features: PUSH_FEATURES}), {secure: true, push: env});
    page.serve = () => respond(200, {public_key: PUBLIC_KEY, subscribed: false, first_joins: 2});
    page.run(`go('notify')`);
    await settle();
    assert(!page.shown('nt-install'));
    assert.match(page.el('nt-install-text').textContent, /browser's menu/);
    let prevented = false;
    let prompted = 0;
    page.fire('beforeinstallprompt', {
        preventDefault() { prevented = true; },
        prompt: async () => { prompted += 1; },
        userChoice: Promise.resolve({outcome: 'accepted'}),
    });
    assert(prevented, 'the page shows its own button instead of the browser bar');
    assert(page.shown('nt-install'));
    page.el('nt-install').click();
    await settle();
    assert.strictEqual(prompted, 1);
    assert(!page.shown('nt-install'));
    assert.match(page.el('nt-install-text').textContent, /^Installed/);

    const app = await signedInPage(stateWith({features: PUSH_FEATURES}), {secure: true, push: fakePush(), standalone: true});
    app.serve = () => respond(200, {public_key: PUBLIC_KEY, subscribed: false, first_joins: 2});
    app.run(`go('notify')`);
    await settle();
    app.fire('beforeinstallprompt', {preventDefault() {}, prompt: async () => {}, userChoice: Promise.resolve({})});
    assert(!app.shown('nt-install'));
    assert.strictEqual(app.el('nt-install-text').textContent, 'Installed: you are using the app now.');
});

// ------------------------------------- the Now tab follows the game type
const MID = ` ${String.fromCharCode(183)} `;          // the page's dot between two facts
const LONG_DASH = String.fromCharCode(0x2014);
const EVIL = '<img src=x onerror=alert(1)>"\'&';
const EVIL_ESCAPED = '&lt;img src=x onerror=alert(1)&gt;&quot;&#39;&amp;';
const SQUAD = [
    {id: 3, name: 'Ghost-Six', team: '1', team_name: 'Joint Ops', kills: '9', deaths: '4', ping: '38'},
    {id: 11, name: 'Rook', team: '1', team_name: 'Joint Ops', kills: '3', deaths: '7', ping: '45'},
    {id: 5, name: 'Tinman', team: '1', team_name: 'Joint Ops', kills: '2', deaths: '4', ping: '112'},
    {id: 12, name: 'ValleyDog', team: '2', team_name: 'Rebels', kills: '8', deaths: '5', ping: '83'},
    {id: 7, name: 'Moose.UK', team: '2', team_name: 'Rebels', kills: '0', deaths: '9', ping: '61'},
    {id: 9, name: EVIL, team: '2', team_name: 'Rebels', kills: '3', deaths: '1', ping: '70'},
];
const TEAMS = [
    {team: 1, name: 'Joint Ops', kills: 14, players: 3},
    {team: 2, name: 'Rebels', kills: 11, players: 3},
];
/** A running match: six players, 14 v 11 kills. */
const matchState = (extra = {}) => stateWith({players: SQUAD, player_count: 6, teams: TEAMS, ...extra});
const send = (page, state) => page.run(`updateUI(${JSON.stringify(state)})`);
const count = (text, part) => text.split(part).length - 1;
// The team card as it was before objectives: kills, the kills bar, the balance line.
const KILLS_CARD = '<div class="team jo"><div class="nm">Joint Ops</div><div class="sc num">14</div>'
    + `<small>kills${MID}3 players</small></div><div class="vs">VS</div>`
    + '<div class="team reb"><div class="nm">Rebels</div><div class="sc num">11</div>'
    + `<small>kills${MID}3 players</small></div><div class="split"><i style="width:56.0%"></i></div>`
    + '<div class="balance"><span>Teams even</span><span>25 kills in all</span></div>';

test('Advance and Secure: a chip per zone under the map name, zones held as the big numbers', async () => {
    const objective = {
        kind: 'zones',
        zones: [{letter: 'A', team: 1}, {letter: 'B', team: 1}, {letter: 'C', team: 0}, {letter: 'D', team: 2}, {letter: '<b>', team: 2}],
        held: {1: 2, 2: 2}, neutral: 1, total: 5,
    };
    const page = await signedInPage(matchState({objective, server: {game_type: 'AAS'}}));
    assert(page.shown('now-objective'));
    const hero = page.el('now-objective').innerHTML;
    assert.strictEqual(count(hero, 'class="zone jo"'), 2);
    assert.strictEqual(count(hero, 'class="zone n"'), 1);
    assert.strictEqual(count(hero, 'class="zone reb"'), 2);
    assert(hero.indexOf('>A</div>') < hero.indexOf('>C</div>') && hero.indexOf('>C</div>') < hero.indexOf('>D</div>'), 'in the game\'s order');
    assert(hero.includes('>&lt;b&gt;</div>') && !hero.includes('<b>'), 'a zone letter is text');
    assert(hero.includes('<span>Joint Ops hold 2</span><span>1 neutral</span><span>Rebels hold 2</span>'), hero);

    const card = page.el('now-teams');
    assert.strictEqual(card.className, 'card teams');
    assert(card.innerHTML.includes(`<div class="sc num">2<small>zones</small></div><small>14 kills${MID}3 players</small>`), card.innerHTML);
    assert(card.innerHTML.includes(`<small>11 kills${MID}3 players</small>`));
    assert(card.innerHTML.includes('<div class="zsplit"><i class="jo" style="width:40.0%"></i><i style="width:20.0%"></i><i class="reb" style="width:40.0%"></i></div>'));
    assert(card.innerHTML.includes('<div class="balance"><span>Level on zones</span><span>Teams even</span></div>'));

    // One team ahead, and no numbers from WolfRAT: counted from the zones.
    send(page, matchState({objective: {
        kind: 'zones',
        zones: [{letter: 'A', team: 1}, {letter: 'B', team: 1}, {letter: 'C', team: 1}, {letter: 'D', team: 2}],
    }}));
    assert(page.el('now-objective').innerHTML.includes('<span>Joint Ops hold 3</span><span></span><span>Rebels hold 1</span>'), 'nothing neutral: no word about it');
    assert(page.el('now-teams').innerHTML.includes('<div class="sc num">3<small>zones</small></div>'));
    assert(page.el('now-teams').innerHTML.includes('<div class="sc num">1<small>zone</small></div>'));
    assert(page.el('now-teams').innerHTML.includes('<span>Joint Ops ahead by 2 zones</span>'));
    assert(page.el('now-teams').innerHTML.includes('<i class="jo" style="width:75.0%"></i><i style="width:0.0%"></i><i class="reb" style="width:25.0%"></i>'));

    send(page, matchState({objective: {kind: 'zones', zones: [{letter: 'A', team: 2}, {letter: 'B', team: 0}]}}));
    assert(page.el('now-teams').innerHTML.includes('<span>Rebels ahead by 1 zone</span>'));

    // The objective goes (the next map is Team Deathmatch): the card as it always was.
    send(page, matchState({objective: null, server: {game_type: 'TDM'}}));
    assert(!page.shown('now-objective'));
    assert.strictEqual(page.el('now-teams').innerHTML, KILLS_CARD);
});

const HILL = {
    kind: 'hill_team', limit_s: 420, delta: 5,
    teams: [{team: 1, hold_s: 252, left_s: 168, holders: 2}, {team: 2, hold_s: 108, left_s: 312, holders: 0}],
};
const hillWith = (one, two, extra = {}) => ({
    ...HILL, ...extra, teams: [{...HILL.teams[0], ...one}, {...HILL.teams[1], ...two}],
});

test('Team King of the Hill: who holds the hill, and each team\'s time left to win', async () => {
    const page = await signedInPage(matchState({objective: HILL, server: {game_type: 'TKOTH'}}));
    assert.strictEqual(page.el('now-objective').innerHTML,
        '<div class="hill jo"><span class="dot"></span><span class="tx">The hill: Joint Ops hold it</span><small>2 in the zone</small></div>');
    const card = page.el('now-teams').innerHTML;
    assert(card.includes(`<div class="sc num">2:48</div><small>to win${MID}2 in the zone</small><div class="tbar"><i style="width:60.0%"></i></div>`), card);
    assert(card.includes(`<div class="sc num">5:12</div><small>to win${MID}nobody in</small><div class="tbar"><i style="width:25.7%"></i></div>`), card);
    assert(card.includes('<div class="balance gap"><span>First to hold the hill for 7:00</span><span>14 v 11 kills</span></div>'));

    send(page, matchState({objective: hillWith({}, {holders: 1})}));
    assert.strictEqual(page.el('now-objective').innerHTML,
        '<div class="hill hot"><span class="dot"></span><span class="tx">The hill: contested</span><small>2 v 1 in the zone</small></div>');
    assert(page.el('now-teams').innerHTML.includes(`<small>to win${MID}1 in the zone</small>`));

    send(page, matchState({objective: hillWith({holders: 0}, {holders: 0})}));
    assert.strictEqual(page.el('now-objective').innerHTML,
        '<div class="hill"><span class="dot"></span><span class="tx">The hill: nobody on it</span></div>');

    send(page, matchState({objective: hillWith({holders: 0}, {holders: 3})}));
    assert.strictEqual(page.el('now-objective').innerHTML,
        '<div class="hill reb"><span class="dot"></span><span class="tx">The hill: Rebels hold it</span><small>3 in the zone</small></div>');

    // A server with no hill limit: the clock itself, no time to win and no bar.
    send(page, matchState({objective: hillWith({left_s: null}, {left_s: null}, {limit_s: null})}));
    const open = page.el('now-teams').innerHTML;
    assert(open.includes(`<div class="sc num">4:12</div><small>held${MID}2 in the zone</small></div>`), open);
    assert(open.includes(`<div class="sc num">1:48</div><small>held${MID}nobody in</small></div>`), open);
    assert(!open.includes('tbar'));
    assert(open.includes('<span>No time limit on the hill</span>'));
});

test('the hill clocks as the game counts them: up 1 with a holder, down by delta with none', async () => {
    const page = makePage();
    assert.strictEqual(page.run('hillSecond(252, 2, 5, 420)'), 253);
    assert.strictEqual(page.run('hillSecond(108, 0, 5, 420)'), 103);
    assert.strictEqual(page.run('hillSecond(3, 0, 5, 420)'), 0, 'not below 0');
    assert.strictEqual(page.run('hillSecond(0, 0, 5, 420)'), 0);
    assert.strictEqual(page.run('hillSecond(419, 3, 5, 420)'), 420);
    assert.strictEqual(page.run('hillSecond(420, 1, 5, 420)'), 420, 'nothing above the limit');
    assert.strictEqual(page.run('hillSecond(500, 1, 5, null)'), 501, 'no limit: it just counts');
    assert.strictEqual(page.run('hillSecond(10, 0, 0, null)'), 10, 'a delta of 0 holds the clock');

    const start = {limit: 420, delta: 5, counted: 0, teams: [{code: '1', hold: 252, holders: 2}, {code: '2', hold: 108, holders: 0}]};
    assert.deepStrictEqual(page.json(`hillTeams(${JSON.stringify(HILL)})`), start);
    assert.strictEqual(page.run(`hillTeams({kind: 'zones'})`), null);
    assert.strictEqual(page.run('hillTeams(null)'), null);
    const steps = page.json(`(() => {
        const first = hillTeams(${JSON.stringify(HILL)});
        const one = hillStep(first);
        let later = one;
        for (let i = 0; i < 29; i += 1) later = hillStep(later);
        return {first, one, later};
    })()`);
    assert.deepStrictEqual(steps.first, start, 'a step makes new clocks; the ones given are not changed');
    assert.deepStrictEqual(steps.one, {...start, counted: 1, teams: [{code: '1', hold: 253, holders: 2}, {code: '2', hold: 103, holders: 0}]});
    assert.deepStrictEqual(steps.later.teams.map(t => t.hold), [282, 0], '30 seconds on: 252 + 30, and 108 - 150 stops at 0');
    assert.strictEqual(steps.later.counted, 30);

    // Teams in any order, numbers as text, a team left out.
    assert.deepStrictEqual(
        page.json(`hillTeams({kind: 'hill_team', limit_s: '300', delta: '5', teams: [{team: 2, hold_s: '40', holders: '1'}]})`),
        {limit: 300, delta: 5, counted: 0, teams: [{code: '1', hold: 0, holders: 0}, {code: '2', hold: 40, holders: 1}]},
    );
});

test('the Now card shows the counted hill clocks; a new state replaces them; none while a link is down', async () => {
    const page = await signedInPage(matchState({objective: HILL}));
    assert(page.run('hillTimer !== null'), 'the once-a-second timer runs on a hill map');
    page.run('hillTick(); hillTick()');
    let card = page.el('now-teams').innerHTML;
    assert(card.includes('<div class="sc num">2:46</div>'), 'Joint Ops: 252 + 2 held, 166 s to win');
    assert(card.includes('<div class="sc num">5:22</div>'), 'Rebels: 108 - 10 held, 322 s to win');
    assert(card.includes('style="width:60.5%"'), 'the bar moves with the clock');

    send(page, matchState({objective: hillWith({hold_s: 300, left_s: 120, holders: 1}, {hold_s: 0, left_s: 420})}));
    card = page.el('now-teams').innerHTML;
    assert(card.includes('<div class="sc num">2:00</div>') && card.includes('<div class="sc num">7:00</div>'), 'the state\'s own values');
    assert.strictEqual(page.run('hillCount.counted'), 0);

    // The socket drops: the clocks stand still.
    page.sockets[0].onclose({code: 1006});
    page.run('hillTick(); hillTick(); hillTick()');
    assert(page.el('now-teams').innerHTML.includes('<div class="sc num">2:00</div>'));
    assert.strictEqual(page.run('hillCount.counted'), 0);

    // WolfRAT has lost the game server: no counting either.
    const lost = await signedInPage(matchState({objective: HILL, connected: false}));
    lost.run('hillTick()');
    assert(lost.el('now-teams').innerHTML.includes('<div class="sc num">2:48</div>'));

    // No new state for a long time: the counting stops by itself.
    const quiet = await signedInPage(matchState({objective: HILL}));
    quiet.run('for (let i = 0; i < 45; i += 1) hillTick()');
    assert.strictEqual(quiet.run('hillCount.counted'), 30);
    assert(quiet.el('now-teams').innerHTML.includes('<div class="sc num">2:18</div>'), '252 + 30 held');

    // Another game type: no timer, nothing counted.
    send(quiet, matchState({objective: null}));
    assert.strictEqual(quiet.run('hillTimer'), null);
    assert.strictEqual(quiet.run('hillCount'), null);
    quiet.run('hillTick()');
    assert.strictEqual(quiet.el('now-teams').innerHTML, KILLS_CARD);
});

test('King of the Hill: who is in the zone, and the players who held the hill longest', async () => {
    const objective = {
        kind: 'hill_solo', limit_s: 420, in_zone: 1,
        players: [
            {name: EVIL, hold_s: 259, left_s: 161, in_zone: true},
            {name: 'ValleyDog', hold_s: 175, left_s: 245, in_zone: false},
            {name: 'Left-Already', hold_s: 30, left_s: 390, in_zone: false},
        ],
    };
    const page = await signedInPage(matchState({objective, server: {game_type: 'KOTH'}}));
    const hero = page.el('now-objective').innerHTML;
    assert.strictEqual(hero, `<div class="hill hot"><span class="dot"></span><span class="tx">The hill: ${EVIL_ESCAPED} is in the zone</span><small>1 in the zone</small></div>`);
    const list = page.el('now-teams');
    assert.strictEqual(list.className, 'list', 'a list, not the two-team card');
    assert(!list.innerHTML.includes('<img'), 'a player name must never become markup');
    assert(list.innerHTML.includes('<div class="lead"><span class="pos num">1</span>'
        + `<div class="main"><b><span class="nm">${EVIL_ESCAPED}</span><span class="inz">IN THE ZONE</span></b>`
        + `<small>held it for 4:19${MID}3 kills</small></div><div class="tm num">2:41<small>to win</small></div></div>`), list.innerHTML);
    assert(list.innerHTML.includes('<span class="pos num">2</span><div class="main"><b><span class="nm">ValleyDog</span></b>'
        + `<small>held it for 2:55${MID}8 kills</small></div><div class="tm num">4:05<small>to win</small></div>`), 'kills come from the player list');
    assert(list.innerHTML.includes('<span class="nm">Left-Already</span></b><small>held it for 0:30</small></div><div class="tm num">6:30<small>to win</small>'), 'not on the player list: no kills');

    // Shown as read: these clocks are not counted on.
    assert.strictEqual(page.run('hillCount'), null);
    assert.strictEqual(page.run('hillTimer'), null);

    const many = Array.from({length: 8}, (_, i) => ({name: `P${i}`, hold_s: 90 - i, left_s: 330 + i, in_zone: i < 3}));
    send(page, matchState({objective: {...objective, in_zone: 3, players: many}}));
    assert.strictEqual(page.el('now-objective').innerHTML,
        '<div class="hill hot"><span class="dot"></span><span class="tx">The hill: 3 players in the zone</span></div>');
    assert.strictEqual(count(page.el('now-teams').innerHTML, 'class="lead"'), 5, 'five rows at most');
    assert.strictEqual(count(page.el('now-teams').innerHTML, 'IN THE ZONE'), 3);

    send(page, matchState({objective: {...objective, in_zone: 0, players: [{name: 'Rook', hold_s: 12, left_s: 408, in_zone: false}]}}));
    assert.strictEqual(page.el('now-objective').innerHTML,
        '<div class="hill"><span class="dot"></span><span class="tx">The hill: nobody on it</span></div>');

    // One inside who is not among the listed five.
    send(page, matchState({objective: {...objective, in_zone: 1, players: [{name: 'Rook', hold_s: 12, left_s: 408, in_zone: false}]}}));
    assert(page.el('now-objective').innerHTML.includes('The hill: 1 player in the zone'));

    send(page, matchState({objective: {kind: 'hill_solo', limit_s: null, in_zone: 0, players: [{name: 'Rook', hold_s: 75, left_s: null, in_zone: false}]}}));
    assert(page.el('now-teams').innerHTML.includes(`<small>held it for 1:15${MID}3 kills</small></div><div class="tm num">${LONG_DASH}<small>no limit</small></div>`));

    send(page, matchState({objective: {kind: 'hill_solo', limit_s: 420, in_zone: 0, players: []}}));
    assert.strictEqual(page.el('now-teams').innerHTML, '<p class="empty">Nobody has held the hill yet</p>');
});

test('Capture the Flag and Flagball: the target under the map name, captures as the big numbers', async () => {
    const ctf = {kind: 'flags', mode: 'ctf', caps: {1: 2, 2: 1}, goal: {1: 5, 2: 5}};
    const page = await signedInPage(matchState({objective: ctf, server: {game_type: 'CTF'}}));
    assert.strictEqual(page.el('now-objective').innerHTML,
        '<div class="hill"><svg class="i sm"><use href="#flag"/></svg><span class="tx">First to 5 flags</span></div>');
    const card = page.el('now-teams').innerHTML;
    assert(card.includes(`<div class="sc num">2</div><small>of 5 flags${MID}3 players</small>`), card);
    assert(card.includes(`<div class="sc num">1</div><small>of 5 flags${MID}3 players</small>`), card);
    assert(card.includes('<div class="split"><i style="width:66.7%"></i></div>'), 'the bar by captures');
    assert(card.includes('<div class="balance"><span>Teams even</span><span>14 v 11 kills</span></div>'));

    send(page, matchState({objective: {...ctf, mode: 'fb'}}));
    assert(page.el('now-objective').innerHTML.includes('First to 5 goals'));
    assert(page.el('now-teams').innerHTML.includes(`<small>of 5 goals${MID}3 players</small>`));

    send(page, matchState({objective: {...ctf, goal: {1: 5, 2: 4}}}));
    assert(page.el('now-objective').innerHTML.includes('<span class="tx">Joint Ops need 5, Rebels need 4</span>'));
    assert(page.el('now-teams').innerHTML.includes(`<small>of 4 flags${MID}3 players</small>`));

    send(page, matchState({objective: {...ctf, goal: {1: 1, 2: 1}}}));
    assert(page.el('now-objective').innerHTML.includes('First to 1 flag<'));
    assert(page.el('now-teams').innerHTML.includes(`<small>of 1 flag${MID}3 players</small>`));

    // No target: nothing under the map name, and no "of".
    send(page, matchState({objective: {kind: 'flags', mode: 'ctf', caps: {1: 0, 2: 0}, goal: null}}));
    assert(!page.shown('now-objective'));
    assert.strictEqual(page.el('now-objective').innerHTML, '');
    assert(page.el('now-teams').innerHTML.includes(`<div class="sc num">0</div><small>flags${MID}3 players</small>`));
    assert(page.el('now-teams').innerHTML.includes('<div class="split none"></div>'));
});

test('Co-op: objectives and enemies left under the map name; the team card as it was', async () => {
    const page = await signedInPage(matchState({
        objective: {kind: 'coop', objectives_done: 2, objectives_total: 5, ai_alive: 34, ai_start: 80},
        server: {game_type: 'COOP'},
    }));
    assert.strictEqual(page.el('now-objective').innerHTML,
        '<div class="hill"><svg class="i sm"><use href="#target"/></svg><span class="tx">Objectives 2 of 5</span><small>34 of 80 enemies left</small></div>');
    assert.strictEqual(page.el('now-teams').innerHTML, KILLS_CARD);

    send(page, matchState({objective: {kind: 'coop', objectives_done: 0, objectives_total: 3, ai_alive: null, ai_start: 80}}));
    assert.strictEqual(page.el('now-objective').innerHTML,
        '<div class="hill"><svg class="i sm"><use href="#target"/></svg><span class="tx">Objectives 0 of 3</span></div>', 'both enemy numbers or none');
});

test('Deathmatch lists the top five by kills; Team Deathmatch and a remote WolfRAT keep the two teams', async () => {
    const page = await signedInPage(matchState({server: {game_type: 'DM'}}));
    const list = page.el('now-teams');
    assert.strictEqual(list.className, 'list');
    assert(!page.shown('now-objective'));
    assert.strictEqual(count(list.innerHTML, 'class="lead"'), 5);
    const order = ['Ghost-Six', 'ValleyDog', EVIL_ESCAPED, 'Rook', 'Tinman'].map(name => list.innerHTML.indexOf(`<span class="nm">${name}</span>`));
    assert(order.every(at => at >= 0), list.innerHTML);
    assert.deepStrictEqual(order, order.slice().sort((a, b) => a - b), 'most kills first; on a tie, fewer deaths');
    assert(!list.innerHTML.includes('Moose.UK'), 'the sixth is left out');
    assert(!list.innerHTML.includes('<img'));
    assert(list.innerHTML.startsWith('<div class="lead"><span class="pos num">1</span><div class="main"><b><span class="nm">Ghost-Six</span></b>'
        + `<small>9 kills${MID}4 deaths</small></div><div class="tm num">9<small>kills</small></div></div>`), list.innerHTML);

    send(page, matchState({server: {game_type: 'DM'}, players: [], player_count: 0, teams: []}));
    assert.strictEqual(page.el('now-teams').innerHTML, '<p class="empty">No players on the server</p>');

    for (const server of [{game_type: 'TDM'}, {game_type: 'AAS'}, {}]) {
        send(page, matchState({server}));
        assert.strictEqual(page.el('now-teams').className, 'card teams');
        assert.strictEqual(page.el('now-teams').innerHTML, KILLS_CARD, `exactly the card of before on ${server.game_type}`);
    }
});

// ------------------------------------------------- tap the time ring
const TIMED = {game_type: 'AAS', time_left_min: 22, time_limit_min: 30};
const timedState = (extra = {}) => stateWith({features: {...ALL_FEATURES, time_left: true}, server: TIMED, ...extra});
const typeMinutes = (page, value) => page.el('sheet').listeners.input.forEach(
    handler => handler({target: {dataset: {typed: 'other'}, value}}));

test('the time ring is a button where the round timer can be set; its sheet shows the choices', async () => {
    const page = await signedInPage(timedState());
    const ring = page.el('now-ring');
    assert.strictEqual(ring.getAttribute('role'), 'button');
    assert.strictEqual(ring.getAttribute('tabindex'), '0');
    assert.strictEqual(ring.getAttribute('aria-label'), 'Change the time left');
    assert(ring.classList.contains('tap'));
    ring.click();
    assert.strictEqual(page.run('sheet.kind'), 'timeleft');
    const html = page.el('sheet-body').innerHTML;
    assert(html.includes('<p class="sh-title">Time left on this map</p>'));
    assert(html.includes('>22 of 30 minutes left now.</p>'), html);
    for (const n of [5, 10, 15, 20, 30, 45, 60]) assert(html.includes(`data-act="pick" data-min="${n}">${n} min</button>`), `${n} min`);
    assert.strictEqual(count(html, 'class="fchip on"'), 1);
    assert(html.includes('<button class="fchip on" type="button" data-act="pick" data-min="10">'), '10 minutes to begin with');
    assert(html.includes('data-typed="other"') && html.includes('data-enter="set"'), 'a box for any other number');
    assert(html.includes('data-act="set">Set 10 minutes left</button>'));
    assert(html.includes('>This round only. The next map goes back to 30 minutes.</p>'));

    // The sheet follows the state while it is up; the next map goes back to what WolfRAT remembers.
    send(page, timedState({server: {...TIMED, time_left_min: 9, time_limit_min: 10}, time_restore: {limit_min: 30}}));
    assert.strictEqual(page.el('tl-now').textContent, '9 of 10 minutes left now.');
    assert.strictEqual(page.el('tl-note').textContent, 'This round only. The next map goes back to 30 minutes.');
    send(page, timedState({server: {...TIMED, time_limit_min: 45}, time_restore: null}));
    assert.strictEqual(page.el('tl-note').textContent, 'This round only. The next map goes back to 45 minutes.');

    // No round timer to set (co-op, not connected, an older WolfRAT): the ring is just the ring.
    const plain = await signedInPage(stateWith({features: ALL_FEATURES, server: TIMED}));
    assert.strictEqual(plain.el('now-ring').getAttribute('role'), null);
    assert(!plain.el('now-ring').classList.contains('tap'));
    plain.el('now-ring').click();
    assert.strictEqual(plain.run('sheet'), null);
    send(page, stateWith({features: ALL_FEATURES, server: TIMED}));
    assert.strictEqual(page.el('now-ring').getAttribute('role'), null, 'the button goes when the feature does');
    assert.strictEqual(page.el('now-ring').getAttribute('aria-label'), null);
});

test('the time sheet posts the minutes left to /api/timeleft and closes', async () => {
    const page = await signedInPage(timedState());
    const posted = [];
    let answer = () => ok({replies: ['10 minutes left on this map. The next map goes back to 30 minutes.']});
    page.serve = request => {
        posted.push([request.path, request.method, request.body, request.headers.Authorization]);
        return answer();
    };
    page.run('openTimeSheet()');
    await page.run(`sheetAct('set')`);
    assert.deepStrictEqual(posted, [['/api/timeleft', 'POST', {minutes: 10}, 'Bearer k1']]);
    assert.strictEqual(page.run('sheet'), null, 'done: the sheet closes');
    assert.strictEqual(page.el('operation-result').textContent,
        `Accepted: Time left ${LONG_DASH} 10 minutes left on this map. The next map goes back to 30 minutes.`);

    // Another chip, then a typed number, then Enter in the box.
    page.run('openTimeSheet()');
    page.run(`sheetAct('pick', {dataset: {min: '45'}})`);
    assert.strictEqual(page.el('tl-set').textContent, 'Set 45 minutes left');
    assert.strictEqual(page.el('tl-set').disabled, false);
    typeMinutes(page, '1');
    assert.strictEqual(page.el('tl-set').textContent, 'Set 1 minute left');
    typeMinutes(page, '37');
    assert.strictEqual(page.el('tl-set').textContent, 'Set 37 minutes left');
    let prevented = false;
    await Promise.all(page.el('sheet').listeners.keydown.map(handler => handler(
        {key: 'Enter', target: {dataset: {enter: 'set'}}, preventDefault() { prevented = true; }})));
    await settle();
    assert(prevented);
    assert.deepStrictEqual(posted[1].slice(0, 3), ['/api/timeleft', 'POST', {minutes: 37}]);
    assert.strictEqual(page.run('sheet'), null);

    // Only whole numbers from 1 to 240 are sent.
    page.run('openTimeSheet()');
    for (const bad of ['0', '241', '999', '1.5', '-5', 'abc', '', ' ']) {
        typeMinutes(page, bad);
        assert.strictEqual(page.el('tl-set').disabled, true, `"${bad}" is not a time`);
        assert.strictEqual(page.el('tl-set').textContent, 'Type 1 to 240 minutes');
        await page.run(`sheetAct('set')`);
    }
    assert.strictEqual(posted.length, 2, 'nothing sent for those');
    typeMinutes(page, '240');
    assert.strictEqual(page.el('tl-set').disabled, false);

    // WolfRAT says no (a co-op map came on): the toast says why and the sheet stays.
    answer = () => respond(409, {ok: false, accepted: false, replies: [], error: 'This map has no round timer.'});
    await page.run(`sheetAct('set')`);
    assert.deepStrictEqual(posted[2].slice(0, 3), ['/api/timeleft', 'POST', {minutes: 240}]);
    assert.strictEqual(page.el('operation-result').dataset.outcome, 'error');
    assert.match(page.el('operation-result').textContent, /This map has no round timer\./);
    assert.strictEqual(page.run('sheet && sheet.kind'), 'timeleft');
    assert.strictEqual(page.el('tl-set').disabled, false, 'ready for another try');
});

// ------------------------------------------------------ quick actions
test('Balance teams and Start vote ask first, then send the command; !unban is in the list', async () => {
    const page = await signedInPage(stateWith({features: ALL_FEATURES}));
    let request = null;
    page.serve = r => {
        request = r;
        return ok();
    };
    const asks = [
        ['balance', 'Balance the teams?', 'WolfRAT moves players to even up the teams.', 'Balance teams', '!balanceteams'],
        ['vote', 'Start the map vote now?', 'Players vote for the next map in game chat.', 'Start vote', '!startvote'],
    ];
    for (const [qa, title, text, button, command] of asks) {
        request = null;
        tap(page, '[data-qa]', {qa});
        assert.strictEqual(page.run('sheet.kind'), 'confirm');
        const html = page.el('sheet-body').innerHTML;
        assert(html.includes(`<p class="sh-title">${title}</p>`), html);
        assert(html.includes(`<p class="sh-text">${text}</p>`), html);
        assert(html.includes(`data-act="yes">${button}</button>`), html);
        page.run(`sheetAct('no')`);
        assert.strictEqual(page.run('sheet'), null);
        assert.strictEqual(request, null, 'Cancel sends nothing');

        tap(page, '[data-qa]', {qa});
        await page.run(`sheetAct('yes')`);
        assert.strictEqual(request.path, '/api/chat/send');
        assert.deepStrictEqual(request.body, {message: command});
        assert.strictEqual(page.el('operation-result').textContent, `Accepted: ${button} ${LONG_DASH} OK`);
        assert.strictEqual(page.run('sheet'), null);
    }

    const names = page.json('COMMANDS.map(c => c[0])');
    assert.strictEqual(names.indexOf('!unban'), names.indexOf('!ban') + 1, '!unban comes after !ban');
    assert.deepStrictEqual(page.json(`COMMANDS.find(c => c[0] === '!unban')`),
        ['!unban', '<player>', "Take a player off WolfRAT's ban list"]);
    assert(page.el('help-cmds').innerHTML.includes('<code>!unban</code> <span class="faint">&lt;player&gt;</span>'), 'the Help page lists it');
});

// ---------------------------------------------------------------- Sky
const SKY_SCENES = [
    {id: 'bloodmoon', label: 'Blood Moon', own: false, colours: {fog: '#b41414', horizon: '#dc1e1e', sun: '#641e1e', sky: '#3c0a0a', ground: '#280505'}},
    {id: 'arctic', label: 'Arctic', own: false, colours: {horizon: '#D2EBFF'}},
    {id: 'mine', label: 'My <b>dusk</b>', own: true, colours: {fog: 'red;background:url(x)', horizon: '#12345', sun: '#646464', ground: '#323232'}},
];
const SKY_FEATURES = {...ALL_FEATURES, sky: true, sky_scenes: SKY_SCENES};
const SKY_ON = {summary: 'Blood Moon - until the map changes', scene: 'bloodmoon', problem: null};
const SKY_OWN = {summary: "The map's own sky", scene: null, problem: null};
const skyPosts = page => page.requests.filter(r => r.path === '/api/sky').map(r => r.body);

test('Sky: a button on Now, a tile under More, a row in the Server list; hidden where WolfRAT cannot do it', async () => {
    const page = await signedInPage(stateWith({features: SKY_FEATURES, sky: SKY_ON}));
    assert(page.shown('qa-sky'));
    assert(page.el('qa-sky').classList.contains('on'), 'marked while a scene is showing');
    assert(page.shown('tile-sky'));
    assert.strictEqual(page.el('tile-sky-sub').textContent, 'Blood Moon');
    const row = page.el('now-server').innerHTML;
    assert(row.includes('<button class="li" type="button" data-go="sky"><span class="ic"><svg class="i sm"><use href="#sky"/></svg></span>'
        + '<div class="main"><b>Sky</b><small>Blood Moon - until the map changes</small></div><span class="chip y">ON</span></button>'), row);

    tap(page, '[data-qa]', {qa: 'sky'});
    assert.strictEqual(page.run('currentScreen'), 'sky');
    assert.strictEqual(page.location.hash, '#sky');
    assert.deepStrictEqual(page.history.calls[page.history.calls.length - 1], ['push', '/#sky'], 'Back returns to Now, as from Weather');
    assert(page.shown('sky-on') && !page.shown('sky-off'));
    assert.strictEqual(page.el('sky-summary').textContent, 'Blood Moon - until the map changes');
    assert(page.shown('sky-chip'));
    assert(!page.shown('sky-problem'));
    assert.strictEqual(page.el('sky-count').textContent, '3 skies');

    // The map's own sky: no ON chip, the button is not marked, the tile says what WolfRAT says.
    send(page, stateWith({features: SKY_FEATURES, sky: SKY_OWN}));
    assert(!page.shown('sky-chip'));
    assert(!page.el('qa-sky').classList.contains('on'));
    assert.strictEqual(page.el('tile-sky-sub').textContent, "The map's own sky");
    assert(page.el('now-server').innerHTML.includes("<small>The map&#39;s own sky</small>"));

    // A WolfRAT that cannot do sky colours (or an older one): no button, no tile, no row.
    for (const state of [stateWith({features: {...ALL_FEATURES, sky: false, sky_scenes: []}, sky: null}), stateWith({features: ALL_FEATURES})]) {
        send(page, state);
        assert(!page.shown('qa-sky'));
        assert(!page.shown('tile-sky'));
        assert(!page.el('now-server').innerHTML.includes('<b>Sky</b>'));
        assert(page.shown('sky-off') && !page.shown('sky-on'), 'the screen itself says it is not available');
    }
});

test('Sky tiles: a swatch from the scene\'s colours, the one showing marked, built again only on a change', async () => {
    const page = await signedInPage(stateWith({features: SKY_FEATURES, sky: SKY_ON}));
    const tiles = page.el('sky-tiles');
    assert.strictEqual(count(tiles.innerHTML, 'class="skyb'), 3);
    assert(tiles.innerHTML.includes('<button class="skyb on" type="button" aria-pressed="true" data-sky="bloodmoon">'
        + '<span class="sw" style="background:linear-gradient(180deg,rgb(220,30,30) 0%,rgb(180,20,20) 60%,rgb(60,16,16) 60%,rgb(36,9,9) 100%)"></span>'
        + '<span class="nm">Blood Moon</span></button>'), tiles.innerHTML);
    assert(tiles.innerHTML.includes('<button class="skyb" type="button" aria-pressed="false" data-sky="arctic">'
        + '<span class="sw" style="background:linear-gradient(180deg,rgb(210,235,255) 0%,rgb(210,235,255) 60%,rgb(112,112,112) 60%,rgb(67,67,67) 100%)"></span>'),
    'colours the scene leaves to the map: the fog as the horizon, a grey ground');
    // A colour that is not #rrggbb never reaches the page; a label is text.
    assert(!tiles.innerHTML.includes('url('), tiles.innerHTML);
    assert(!tiles.innerHTML.includes('red;'));
    assert(tiles.innerHTML.includes('data-sky="mine"><span class="sw" style="background:linear-gradient(180deg,rgb(120,150,190) 0%,rgb(120,150,190) 60%,rgb(62,62,62) 60%,rgb(37,37,37) 100%)"></span>'
        + '<span class="nm">My &lt;b&gt;dusk&lt;/b&gt;</span>'), tiles.innerHTML);
    assert.strictEqual(page.run(`skySwatch({horizon: '#fff', fog: 'rgb(1,2,3)', sun: null, ground: 7})`),
        'linear-gradient(180deg,rgb(120,150,190) 0%,rgb(120,150,190) 60%,rgb(112,112,112) 60%,rgb(67,67,67) 100%)');
    assert.deepStrictEqual(page.json(`[hexColour('#0A0b0C'), hexColour(' #ffffff '), hexColour('#ggg000'), hexColour('#fff'), hexColour(123456), hexColour('#12345678')]`),
        [[10, 11, 12], [255, 255, 255], null, null, null, null]);

    // The same scenes again (every state carries them): the tiles are left alone.
    tiles.innerHTML = 'kept';
    send(page, stateWith({features: SKY_FEATURES, sky: SKY_ON, player_count: 5}));
    send(page, stateWith({features: SKY_FEATURES, sky: {...SKY_ON, summary: 'Blood Moon - 9 min left'}}));
    assert.strictEqual(tiles.innerHTML, 'kept');
    send(page, stateWith({features: SKY_FEATURES, sky: {summary: 'Arctic - 10 min left', scene: 'arctic', problem: null}}));
    assert(tiles.innerHTML.includes('<button class="skyb on" type="button" aria-pressed="true" data-sky="arctic">'), 'another scene showing: built again');
    assert(tiles.innerHTML.includes('<button class="skyb" type="button" aria-pressed="false" data-sky="bloodmoon">'));

    // A scene called "off" would send the map's own sky: it is not offered.
    send(page, stateWith({features: {...SKY_FEATURES, sky_scenes: [...SKY_SCENES, {id: 'off', label: 'Off'}, null, {label: 'no id'}]}, sky: SKY_OWN}));
    assert.strictEqual(count(tiles.innerHTML, 'class="skyb'), 3);
    send(page, stateWith({features: {...SKY_FEATURES, sky_scenes: []}, sky: SKY_OWN}));
    assert.strictEqual(tiles.innerHTML, '<p class="empty">No sky scenes on this WolfRAT</p>');
    assert.strictEqual(page.el('sky-count').textContent, 'Skies');
});

test('a sky tile posts the scene and how long to keep it; Back to the map\'s own sky posts "off"', async () => {
    const page = await signedInPage(stateWith({features: SKY_FEATURES, sky: SKY_OWN}));
    let reply = 'Sky: Arctic until the map changes.';
    page.serve = () => ok({replies: [reply]});
    tap(page, '[data-sky]', {sky: 'arctic'});
    await settle();
    assert.deepStrictEqual(skyPosts(page), [{scene: 'arctic', minutes: null}], 'until the map changes: no minutes');
    const sent = page.requests[page.requests.length - 1];
    assert.strictEqual(sent.method, 'POST');
    assert.strictEqual(sent.headers.Authorization, 'Bearer k1');
    assert.strictEqual(page.el('operation-result').textContent, 'Accepted: Sky: Arctic until the map changes.', 'the answer names Sky once');

    reply = 'Sky: Blood Moon for 30 min.';
    tap(page, '[data-keep]', {keep: '30'});
    assert.strictEqual(page.run('skyKeep'), 30);
    tap(page, '[data-sky]', {sky: 'bloodmoon'});
    await settle();
    assert.deepStrictEqual(skyPosts(page)[1], {scene: 'bloodmoon', minutes: 30});
    assert.strictEqual(page.el('operation-result').textContent, 'Accepted: Sky: Blood Moon for 30 min.');

    for (const [keep, minutes] of [['10', 10], ['60', 60], ['', null], ['7', null], ['abc', null]]) {
        tap(page, '[data-keep]', {keep});
        assert.strictEqual(page.run('skyKeep'), minutes, `keep "${keep}"`);
    }

    reply = "Sky: back to the map's own.";
    tap(page, '[data-keep]', {keep: '10'});
    page.el('sky-own').click();
    await settle();
    assert.deepStrictEqual(skyPosts(page)[2], {scene: 'off'}, 'no minutes with "off"');

    // A second tap while the first is still on its way is not sent twice.
    tap(page, '[data-sky]', {sky: 'arctic'});
    tap(page, '[data-sky]', {sky: 'arctic'});
    assert.strictEqual(page.el('sky-own').disabled, true);
    await settle();
    assert.strictEqual(skyPosts(page).length, 4);
    assert.deepStrictEqual(skyPosts(page)[3], {scene: 'arctic', minutes: 10});
    assert.strictEqual(page.el('sky-own').disabled, false);

    // WolfRAT refuses: the usual toast says why.
    page.serve = () => respond(409, {ok: false, accepted: false, replies: [], error: 'Sky: ready after the next map change.'});
    tap(page, '[data-sky]', {sky: 'arctic'});
    await settle();
    assert.strictEqual(page.el('operation-result').dataset.outcome, 'error');
    assert.strictEqual(page.el('operation-result').textContent, 'Error: Sky: ready after the next map change.');
    page.serve = () => respond(400, {error: 'Unknown sky scene'});
    tap(page, '[data-sky]', {sky: 'gone'});
    await settle();
    assert.strictEqual(page.el('operation-result').textContent, `Error: Sky ${LONG_DASH} Unknown sky scene`);
});

test('Sky with a problem: the page says why and its sky buttons do nothing', async () => {
    const problem = 'Sky: ready after the next map change.';
    const page = await signedInPage(stateWith({features: SKY_FEATURES, sky: {...SKY_OWN, problem}}));
    page.serve = () => ok();
    assert(page.shown('sky-problem'));
    assert.strictEqual(page.el('sky-problem').textContent, problem);
    assert.strictEqual(page.el('sky-own').disabled, true);
    assert.strictEqual(count(page.el('sky-tiles').innerHTML, ' disabled>'), 3, 'every tile is greyed');

    tap(page, '[data-sky]', {sky: 'arctic'});
    tap(page, '[data-sky]', {sky: 'arctic'}, {disabled: true});
    page.el('sky-own').click();
    tap(page, '[data-keep]', {keep: '30'});
    await settle();
    assert.deepStrictEqual(skyPosts(page), [], 'nothing is sent');
    assert.strictEqual(page.run('skyKeep'), null);
    assert.strictEqual(await page.run(`setSky('arctic')`), null);

    // A hostile reason is shown as text.
    send(page, stateWith({features: SKY_FEATURES, sky: {...SKY_OWN, problem: '<img src=x onerror=alert(1)>'}}));
    assert.strictEqual(page.el('sky-problem').textContent, '<img src=x onerror=alert(1)>');

    // The problem goes: the buttons work again.
    send(page, stateWith({features: SKY_FEATURES, sky: SKY_OWN}));
    assert(!page.shown('sky-problem'));
    assert.strictEqual(page.el('sky-own').disabled, false);
    assert.strictEqual(count(page.el('sky-tiles').innerHTML, ' disabled>'), 0);
    tap(page, '[data-sky]', {sky: 'arctic'});
    await settle();
    assert.deepStrictEqual(skyPosts(page), [{scene: 'arctic', minutes: null}]);
});

// ------------------------------------- what this phone hears about
const MIXED = [
    {id: 1, ts: NOW - 600, kind: 'join', text: 'Kestrel joined (1 player on)', name: 'Kestrel'},
    {id: 2, ts: NOW - 500, kind: 'map', text: 'Cold Water Gap started', name: null},
    {id: 3, ts: NOW - 400, kind: 'leave', text: 'Kestrel left', name: 'Kestrel'},
    {id: 4, ts: NOW - 300, kind: 'vote', text: 'Map vote started: Misty Beach', name: null},
    {id: 5, ts: NOW - 200, kind: 'kick', text: 'Rook was kicked by an admin', name: 'Rook'},
    {id: 6, ts: NOW - 100, kind: 'server', text: 'Game server stopped', name: null},
    {id: 7, ts: NOW - 50, kind: 'zone', text: 'Rebels took zone C (3 of 5)', name: null},
    {id: 8, ts: NOW - 40, kind: 'flag', text: 'Joint Ops captured a flag (2 of 5)', name: null},
    {id: 9, ts: NOW - 30, kind: 'radar', text: 'Something of a newer WolfRAT', name: null},
];
const MIXED_TEXTS = {
    join: 'joined (1 player on)', map: 'Cold Water Gap started', leave: '</b> left', vote: 'Map vote started',
    kick: 'was kicked', server: 'Game server stopped', zone: 'took zone C', flag: 'captured a flag', radar: 'Something of a newer',
};
/** The kinds of MIXED the Alerts sheet lists now. */
function listedKinds(page) {
    page.run('openAlerts()');
    const html = page.el('sheet-body').innerHTML;
    page.run('closeSheet()');
    return Object.keys(MIXED_TEXTS).filter(kind => html.includes(MIXED_TEXTS[kind]));
}
const prefsOf = (bell, phone = {}) => ({
    groups: GROUPS, bell: {...everyGroup(false), ...bell}, phone: {...everyGroup(false), ...phone},
});

test('the bell lists the kinds of alert this phone chose; WolfRAT\'s defaults until they have loaded', async () => {
    // The choices never arrive here (the request fails): the defaults.
    const page = await signedInPage(stateWith({events: MIXED, events_last_id: 9}));
    assert.strictEqual(page.run('alertPrefsState'), 'failed');
    assert.deepStrictEqual(listedKinds(page), ['join', 'kick', 'server', 'radar'], 'joins, admin actions and the server; a kind with no group is always listed');

    const loaded = await signedInPage(stateWith({events: MIXED, events_last_id: 9}), {serve: withPrefs(DEFAULT_PREFS)});
    const asked = loaded.requests.filter(r => r.path === '/api/alerts/prefs');
    assert.strictEqual(asked.length, 1, 'asked once the app is shown');
    assert.strictEqual(asked[0].method, 'GET');
    assert.strictEqual(asked[0].headers.Authorization, 'Bearer k1');
    assert.deepStrictEqual(listedKinds(loaded), ['join', 'kick', 'server', 'radar']);

    const picky = await signedInPage(stateWith({events: MIXED, events_last_id: 9}),
        {serve: withPrefs(prefsOf({map: true, objective: true, leave: true}))});
    assert.deepStrictEqual(listedKinds(picky), ['map', 'leave', 'zone', 'flag', 'radar']);
    picky.run('openAlerts()');
    const html = picky.el('sheet-body').innerHTML;
    assert(html.includes('<span class="ic k-zone"><svg class="i sm"><use href="#flag"/>'), 'a zone taken gets the flag icon');
    assert(html.includes('<span class="ic k-flag"><svg class="i sm"><use href="#flag"/>'));
    assert(html.indexOf('captured a flag') < html.indexOf('took zone C') && html.indexOf('took zone C') < html.indexOf('Cold Water Gap'), 'newest first');

    const all = await signedInPage(stateWith({events: MIXED, events_last_id: 9}), {serve: withPrefs(EVERY_ALERT)});
    assert.deepStrictEqual(listedKinds(all), Object.keys(MIXED_TEXTS));

    const none = await signedInPage(stateWith({events: MIXED.slice(0, 8), events_last_id: 8}), {serve: withPrefs(prefsOf({}))});
    none.run('openAlerts()');
    assert.match(none.el('sheet-body').innerHTML, /<p class="empty">Nothing yet<\/p>/);
    assert(!none.shown('hdr-pip'));

    // An older WolfRAT has no such choices (404): every alert, as before.
    const older = await signedInPage(stateWith({events: MIXED, events_last_id: 9}),
        {serve: () => respond(404, new Error('Not Found'))});
    assert.strictEqual(older.run('alertPrefsState'), 'missing');
    assert.deepStrictEqual(listedKinds(older), Object.keys(MIXED_TEXTS));
    assert(!older.shown('nt-prefs'), 'and no switches for them');
});

test('an alert the bell leaves out does not light the red dot, now or later', async () => {
    const byKind = kind => MIXED.find(e => e.kind === kind);
    const arrive = (page, kind) => page.run(`handleWebSocketMessage(${JSON.stringify({type: 'event', event: byKind(kind)})})`);
    const server = JSON.parse(JSON.stringify(DEFAULT_PREFS));
    const page = await signedInPage(stateWith({events: [MIXED[0]], events_last_id: 1}), {
        local: {wolfkey: 'k1', wolfseen: '1'},
        serve: withPrefs(server, request => {
            Object.assign(server.bell, request.body.bell || {});
            return respond(200, JSON.parse(JSON.stringify(server)));
        }),
    });
    assert(!page.shown('hdr-pip'));
    for (const kind of ['map', 'leave', 'vote']) {
        arrive(page, kind);
        assert(!page.shown('hdr-pip'), `${kind} is off: no dot`);
    }
    assert.strictEqual(page.el('hdr-bell').getAttribute('aria-label'), 'Alerts');
    assert.deepStrictEqual(page.json('currentEvents.map(e => e.id)'), [1, 2, 3, 4], 'the page still holds them');
    arrive(page, 'kick');
    assert(page.shown('hdr-pip'), 'a kick is on: the dot');

    // Opening the list marks the newest of all as seen, listed or not.
    arrive(page, 'zone');
    page.run('openAlerts()');
    assert.strictEqual(page.local.getItem('wolfseen'), '7');
    assert.strictEqual(count(page.el('sheet-body').innerHTML, 'class="al new"'), 1, 'only the kick is listed as new');
    page.run('closeSheet()');
    assert(!page.shown('hdr-pip'));

    // Zones switched on afterwards: the one that came before is listed, but lights nothing.
    await page.run(`setAlertPref('bell', 'objective', true)`);
    assert(!page.shown('hdr-pip'));
    assert(listedKinds(page).includes('zone'));
    arrive(page, 'flag');
    assert(page.shown('hdr-pip'), 'the next one does');

    // Switching a group off takes its unread alert out of the dot at once.
    await page.run(`setAlertPref('bell', 'objective', false)`);
    assert(!page.shown('hdr-pip'));

    // The Alerts sheet is up when a choice changes: its list follows.
    page.run('openAlerts()');
    assert(!page.el('sheet-body').innerHTML.includes('Cold Water Gap'));
    await page.run(`setAlertPref('bell', 'map', true)`);
    assert(page.el('sheet-body').innerHTML.includes('Cold Water Gap'));
});

/** A signed-in page on the Notifications screen. `subscribed` = this phone has notifications on. */
async function notifyPage({subscribed = true, secure = true, prefs = DEFAULT_PREFS, firstJoins = 2, features = PUSH_FEATURES} = {}) {
    const server = JSON.parse(JSON.stringify(prefs));
    const posted = [];
    const env = secure
        ? fakePush({permission: subscribed ? 'granted' : 'default', subscribedWith: subscribed ? new Uint8Array(KEY_BYTES) : null})
        : null;
    const page = await signedInPage(stateWith({features}), {secure, push: env});
    page.prefsAnswer = null;                      // set to answer the next POST another way
    page.serve = request => {
        if (request.path === '/api/push') return respond(200, {public_key: PUBLIC_KEY, subscribed, first_joins: firstJoins});
        if (request.path === '/api/alerts/prefs' && request.method === 'GET') return respond(200, JSON.parse(JSON.stringify(server)));
        if (request.path === '/api/alerts/prefs') {
            posted.push(request.body);
            if (page.prefsAnswer) return page.prefsAnswer(request);
            for (const kind of ['bell', 'phone']) Object.assign(server[kind], request.body[kind] || {});
            return respond(200, JSON.parse(JSON.stringify(server)));
        }
        if (request.path === '/api/push/settings') return respond(200, {ok: true, first_joins: request.body.first_joins});
        return respond(200, {ok: true});
    };
    page.run(`go('notify')`);
    await settle();
    return {page, posted, server, env};
}
const switchHtml = (kind, group, isOn, title, text, greyed = false) => `<button class="tg${isOn ? ' on' : ''}" type="button" role="switch"`
    + ` aria-checked="${isOn}" aria-label="${title}, ${kind === 'bell' ? 'in the bell' : 'as a phone notification'}"`
    + ` data-pref="${kind}" data-group="${group}"${greyed ? ' disabled' : ''}>`
    + `<svg class="i"><use href="#${kind}"/></svg>${text}</button>`;

test('Notifications: a row for every kind of alert, with a Bell and a Phone switch', async () => {
    const {page} = await notifyPage();
    assert.strictEqual(page.requests.filter(r => r.path === '/api/alerts/prefs').length, 2, 'asked on arrival, and again when the screen opens');
    assert(page.shown('nt-prefs'));
    assert(!page.shown('nt-phone-hint'));
    const rows = page.el('nt-rows').innerHTML;
    assert.strictEqual(count(rows, 'class="nrow"'), 7);
    const titles = [
        ['join', 'Players joining', 'Phone: the first on an empty server'],
        ['leave', 'Players leaving', 'Gone for 2 minutes'],
        ['map', 'New map', 'Every map change'],
        ['vote', 'Map votes', 'A vote started or ended'],
        ['admin', 'Kicks, bans and warnings', 'From a phone or a mod&#39;s command'],
        ['server', 'Game server down', 'It stopped, and when it is back'],
        ['objective', 'Zones and flags', 'A zone, a flag or the hill taken'],
    ];
    let at = -1;
    for (const [group, title, text] of titles) {
        const row = `<div class="nrow"><div class="main"><b>${title}</b><small>${text}</small></div>`
            + switchHtml('bell', group, DEFAULT_PREFS.bell[group], title, 'Bell')
            + switchHtml('phone', group, DEFAULT_PREFS.phone[group], title, group === 'join' ? 'First 2' : 'Phone')
            + '</div>';
        assert(rows.indexOf(row) > at, `${title}: ${rows}`);
        at = rows.indexOf(row);
    }

    // The join row's Phone switch names the first-joins number; "Phone" when that is 0.
    await page.run('setFirstJoins(3)');
    assert(page.el('nt-rows').innerHTML.includes('First 3</button>'));
    await page.run('setFirstJoins(0)');
    assert(page.el('nt-rows').innerHTML.includes(switchHtml('phone', 'join', true, 'Players joining', 'Phone')));

    // WolfRAT offers fewer groups (or one this page does not know): only the ones both know.
    const fewer = await notifyPage({prefs: {...DEFAULT_PREFS, groups: ['join', 'server', 'radar']}});
    assert.strictEqual(count(fewer.page.el('nt-rows').innerHTML, 'class="nrow"'), 2);
    assert(!fewer.page.el('nt-rows').innerHTML.includes('radar'));
});

test('a switch tap changes it at once, sends that one value, and goes back if WolfRAT says no', async () => {
    const {page, posted} = await notifyPage();
    const before = page.requests.length;
    tap(page, '[data-pref]', {pref: 'bell', group: 'map'});
    assert.strictEqual(page.run(`alertPref('bell', 'map')`), true, 'at once, before WolfRAT answers');
    assert(page.el('nt-rows').innerHTML.includes(switchHtml('bell', 'map', true, 'New map', 'Bell')));
    await settle();
    assert.deepStrictEqual(posted, [{bell: {map: true}}], 'just that one value');
    const sent = page.requests[before];
    assert.deepStrictEqual([sent.path, sent.method, sent.headers.Authorization], ['/api/alerts/prefs', 'POST', 'Bearer k1']);
    assert.strictEqual(page.run(`alertPref('bell', 'map')`), true);
    assert(!page.el('operation-result').classList.contains('show'), 'the switch is the answer: no toast');

    tap(page, '[data-pref]', {pref: 'phone', group: 'server'});
    await settle();
    assert.deepStrictEqual(posted[1], {phone: {server: false}}, 'on by default, so the tap turns it off');

    // Two quick taps. The first answer was made before WolfRAT had the second tap: it must not undo it.
    const waiting = [];
    page.prefsAnswer = () => new Promise(resolve => waiting.push(resolve));
    tap(page, '[data-pref]', {pref: 'bell', group: 'leave'});
    tap(page, '[data-pref]', {pref: 'bell', group: 'vote'});
    const afterFirst = {
        groups: GROUPS,
        bell: {...DEFAULT_PREFS.bell, map: true, leave: true},
        phone: {...DEFAULT_PREFS.phone, server: false},
    };
    waiting[0](respond(200, afterFirst));
    await settle();
    assert.strictEqual(page.run(`alertPref('bell', 'vote')`), true, 'the second tap stays as tapped');
    waiting[1](respond(200, {...afterFirst, bell: {...afterFirst.bell, vote: true}}));
    await settle();
    page.prefsAnswer = null;
    assert.deepStrictEqual(posted.slice(2), [{bell: {leave: true}}, {bell: {vote: true}}]);
    assert.deepStrictEqual(page.json('alertPrefs.bell'), {...DEFAULT_PREFS.bell, map: true, leave: true, vote: true});
    assert.deepStrictEqual(page.json('alertPrefs.phone'), {...DEFAULT_PREFS.phone, server: false});

    // WolfRAT says no: the switch goes back and the toast says why.
    page.prefsAnswer = () => respond(400, {error: 'Unknown group'});
    tap(page, '[data-pref]', {pref: 'bell', group: 'objective'});
    assert.strictEqual(page.run(`alertPref('bell', 'objective')`), true);
    await settle();
    assert.deepStrictEqual(posted[4], {bell: {objective: true}});
    assert.strictEqual(page.run(`alertPref('bell', 'objective')`), false, 'back to off');
    assert(page.el('nt-rows').innerHTML.includes(switchHtml('bell', 'objective', false, 'Zones and flags', 'Bell')));
    assert.strictEqual(page.el('operation-result').dataset.outcome, 'error');
    assert.strictEqual(page.el('operation-result').textContent, `Error: Zones and flags${MID}Bell ${LONG_DASH} Unknown group`);
    assert.deepStrictEqual(page.json('alertPrefs.bell'), {...DEFAULT_PREFS.bell, map: true, leave: true, vote: true}, 'the others stay');

    // WolfRAT out of reach: back as well.
    page.prefsAnswer = () => {
        throw new Error('Failed to fetch');
    };
    tap(page, '[data-pref]', {pref: 'bell', group: 'join'});
    assert.strictEqual(page.run(`alertPref('bell', 'join')`), false);
    await settle();
    assert.strictEqual(page.run(`alertPref('bell', 'join')`), true);
    assert.match(page.el('operation-result').textContent, /Players joining.*Failed to fetch/);

    // Not a switch of this page: nothing is sent.
    const sentSoFar = posted.length;
    for (const [pref, group] of [['bell', 'radar'], ['sms', 'join'], ['bell', '__proto__'], ['phone', 'toString']]) {
        tap(page, '[data-pref]', {pref, group});
    }
    tap(page, '[data-pref]', {pref: 'bell', group: 'map'}, {disabled: true});
    await settle();
    assert.strictEqual(posted.length, sentSoFar);

    // A key that stopped working signs the phone out.
    page.prefsAnswer = () => respond(401, {error: 'Unauthorized'});
    tap(page, '[data-pref]', {pref: 'bell', group: 'map'});
    await settle();
    assert(page.shown('signin'));
    assert.strictEqual(page.run('alertPrefs'), null, 'the choices belonged to that key');
});

test('the Phone switches are off and greyed until this phone has notifications on', async () => {
    const {page, posted, env} = await notifyPage({subscribed: false});
    const rows = page.el('nt-rows').innerHTML;
    assert.strictEqual(count(rows, 'data-pref="phone"'), 7);
    assert.strictEqual(count(rows, ' disabled>'), 7, 'every Phone switch is greyed');
    assert(rows.includes(switchHtml('phone', 'join', false, 'Players joining', 'First 2', true)), 'shown off, though this phone\'s choice is on');
    assert(rows.includes(switchHtml('bell', 'join', true, 'Players joining', 'Bell')), 'the Bell switches work as ever');
    assert(page.shown('nt-phone-hint'));
    assert.strictEqual(page.el('nt-phone-hint').textContent, 'Turn on notifications above to use the Phone switches.');

    tap(page, '[data-pref]', {pref: 'phone', group: 'map'});
    await settle();
    assert.deepStrictEqual(posted, [], 'a Phone tap does nothing yet');
    tap(page, '[data-pref]', {pref: 'bell', group: 'map'});
    await settle();
    assert.deepStrictEqual(posted, [{bell: {map: true}}]);

    // Notifications turned on: the switches show this phone's choices and work.
    await page.run('turnOnNotifications()');
    assert.strictEqual(env.subscribeCalls.length, 1);
    const live = page.el('nt-rows').innerHTML;
    assert.strictEqual(count(live, ' disabled>'), 0);
    assert(live.includes(switchHtml('phone', 'join', true, 'Players joining', 'First 2')));
    assert(live.includes(switchHtml('phone', 'server', true, 'Game server down', 'Phone')));
    assert(!page.shown('nt-phone-hint'));
    tap(page, '[data-pref]', {pref: 'phone', group: 'map'});
    await settle();
    assert.deepStrictEqual(posted[1], {phone: {map: true}});

    // A plain http address cannot notify at all.
    const http = await notifyPage({secure: false, subscribed: false});
    assert.strictEqual(count(http.page.el('nt-rows').innerHTML, ' disabled>'), 7);
    assert(http.page.shown('nt-phone-hint'));
    assert.strictEqual(http.page.el('nt-phone-hint').textContent, 'Phone notifications need the secure address.');
    assert(http.page.shown('nt-insecure') && !http.page.shown('nt-phone'));

    // A WolfRAT that cannot send notifications: the bell choices are still there.
    const mute = await notifyPage({features: {...ALL_FEATURES, push: false}});
    assert(mute.page.shown('nt-prefs'));
    assert.strictEqual(count(mute.page.el('nt-rows').innerHTML, ' disabled>'), 7);
    assert.strictEqual(mute.page.el('nt-status').textContent, "This WolfRAT can't send notifications to phones.");
    assert(!mute.page.shown('nt-on') && !mute.page.shown('nt-joins-card'));
    assert(!mute.page.requests.some(r => r.path === '/api/push'), 'nothing to ask it about');
});

test('the choices that could not be loaded say so, and are asked for again when the screen opens', async () => {
    const page = await signedInPage(stateWith({features: PUSH_FEATURES}));
    assert.strictEqual(page.run('alertPrefsState'), 'failed');
    page.serve = () => respond(500, {error: 'busy'});
    page.run(`go('notify')`);
    await settle();
    assert(page.shown('nt-prefs'));
    assert.strictEqual(page.el('nt-rows').innerHTML, '<p class="empty">Couldn&#39;t load this phone&#39;s choices.</p>'.replace(/&#39;/g, "'"));
    assert(!page.shown('nt-phone-hint'));
    assert.strictEqual(await page.run(`setAlertPref('bell', 'map', true)`), null, 'no switches to tap yet');

    page.serve = withPrefs(prefsOf({map: true}));
    page.run(`go('more')`);
    page.run(`go('notify')`);
    await settle();
    assert.strictEqual(page.run('alertPrefsState'), 'ok');
    assert(page.el('nt-rows').innerHTML.includes(switchHtml('bell', 'map', true, 'New map', 'Bell')));

    // A later failure keeps the choices it has.
    page.serve = () => respond(500, {error: 'busy'});
    page.run(`go('more')`);
    page.run(`go('notify')`);
    await settle();
    assert.strictEqual(page.run('alertPrefsState'), 'ok');
    assert.strictEqual(page.run(`alertPref('bell', 'map')`), true);

    // Not the answer of the contract: not taken as choices.
    assert.strictEqual(page.run(`cleanAlertPrefs({bell: [], phone: {}})`), null);
    assert.strictEqual(page.run(`cleanAlertPrefs({bell: {map: true}})`), null);
    assert.deepStrictEqual(page.json(`cleanAlertPrefs({bell: {map: 'yes', join: false, radar: true}, phone: {server: true}})`),
        {groups: null, bell: {join: false}, phone: {server: true}}, 'only true / false for the groups this page knows');
});

// ----------------------------------------------------- service worker
function loadWorker() {
    const handlers = {};
    const worker = {handlers, shown: [], opened: [], windows: []};
    const scope = {
        location: {origin: 'https://wolfrat.example.com'},
        addEventListener: (type, handler) => { handlers[type] = handler; },
        registration: {
            showNotification: (title, options) => {
                worker.shown.push({title, options});
                return Promise.resolve();
            },
        },
        clients: {
            matchAll: async options => {
                worker.matchOptions = options;
                return worker.windows;
            },
            openWindow: async url => {
                worker.opened.push(url);
                return {url};
            },
            claim: async () => {},
        },
        skipWaiting: async () => {},
    };
    const context = vm.createContext({
        self: scope, URL, console,
        caches: {open: async () => ({put: async () => {}, addAll: async () => {}}), match: async () => null, keys: async () => []},
        fetch: async () => ({ok: false}),
        Response: {error: () => ({})},
    });
    vm.runInContext(WORKER_SOURCE, context, {filename: workerPath});
    worker.dispatch = async (type, event) => {
        const waits = [];
        handlers[type]({...event, waitUntil: p => waits.push(p), respondWith: p => waits.push(p)});
        await Promise.all(waits);
        return waits.length;
    };
    return worker;
}

const plain = value => JSON.parse(JSON.stringify(value));

test('sw.js: a push shows WolfRAT\'s notification with the payload fields', async () => {
    const worker = loadWorker();
    await worker.dispatch('push', {data: {json: () => ({
        title: '[UK] Mixed Maps LC', body: 'Kestrel joined (1 player on)', tag: 'join', url: '/#players',
    })}});
    assert.deepStrictEqual(plain(worker.shown[0]), {title: '[UK] Mixed Maps LC', options: {
        body: 'Kestrel joined (1 player on)',
        icon: '/static/icon-192.png',
        badge: '/static/badge-96.png',
        tag: 'join',
        renotify: true,
        data: {url: '/#players'},
    }});
    await worker.dispatch('push', {data: {json: () => { throw new Error('not JSON'); }, text: () => 'plain words'}});
    assert.deepStrictEqual(plain(worker.shown[1]), {title: 'WolfRAT', options: {
        body: 'plain words', icon: '/static/icon-192.png', badge: '/static/badge-96.png',
        tag: 'wolfrat', renotify: true, data: {url: '/'},
    }}, 'renotify always comes with a tag');
    await worker.dispatch('push', {data: null});
    assert.strictEqual(worker.shown[2].title, 'WolfRAT');
});

test('sw.js: a tap focuses an open page and moves it to the url, else opens one', async () => {
    const worker = loadWorker();
    const calls = [];
    const page = {
        url: 'https://wolfrat.example.com/#now',
        focused: false,
        async focus() { calls.push('focus'); return this; },
        async navigate(url) { calls.push(['navigate', url]); this.url = url; return this; },
    };
    worker.windows = [page];
    let closed = 0;
    const tap = url => ({notification: {data: {url}, close: () => { closed += 1; }}});
    await worker.dispatch('notificationclick', tap('/#players'));
    assert.strictEqual(closed, 1);
    assert.deepStrictEqual(calls, ['focus', ['navigate', 'https://wolfrat.example.com/#players']]);
    assert.deepStrictEqual(worker.opened, []);
    assert.strictEqual(worker.matchOptions.type, 'window');

    calls.length = 0;
    await worker.dispatch('notificationclick', tap('/#players'));
    assert.deepStrictEqual(calls, ['focus'], 'already there: just brought forward');

    worker.windows = [];
    await worker.dispatch('notificationclick', tap('/#more'));
    await worker.dispatch('notificationclick', tap('https://evil.example/steal'));
    await worker.dispatch('notificationclick', tap('javascript:alert(1)'));
    await worker.dispatch('notificationclick', {notification: {data: null, close() {}}});
    assert.deepStrictEqual(worker.opened, [
        'https://wolfrat.example.com/#more',
        'https://wolfrat.example.com/',
        'https://wolfrat.example.com/',
        'https://wolfrat.example.com/',
    ], 'only pages of this WolfRAT open');
});

test('sw.js: a notification carries its group as the tag; a newer one of that group alerts again', async () => {
    const worker = loadWorker();
    const notice = body => ({data: {json: () => ({title: '[UK] Mixed Maps LC', body, tag: 'server', url: '/#now'})}});
    await worker.dispatch('push', notice('Game server is down'));
    await worker.dispatch('push', notice('Game server is back (down 37 min)'));
    await worker.dispatch('push', {data: {json: () => ({title: 'srv', body: 'Rebels took zone C (3 of 5)', tag: 'objective', url: '/#now'})}});
    assert.deepStrictEqual(worker.shown.map(n => [n.options.tag, n.options.renotify, n.options.body]), [
        ['server', true, 'Game server is down'],
        ['server', true, 'Game server is back (down 37 min)'],
        ['objective', true, 'Rebels took zone C (3 of 5)'],
    ], 'the same tag replaces the older notification, and renotify makes the phone alert for it');

    // A tap opens the notification's own screen.
    await worker.dispatch('notificationclick', {notification: {data: worker.shown[1].options.data, close() {}}});
    assert.deepStrictEqual(worker.opened, ['https://wolfrat.example.com/#now']);
});

test('sw.js still never answers /api or /ws, and keeps the app shell', async () => {
    const worker = loadWorker();
    for (const path of ['/api/status', '/api/events?after=3', '/ws']) {
        const answered = await worker.dispatch('fetch', {request: {method: 'GET', url: `https://wolfrat.example.com${path}`}});
        assert.strictEqual(answered, 0, `${path} goes straight to WolfRAT`);
    }
    assert.strictEqual(await worker.dispatch('fetch', {request: {method: 'POST', url: 'https://wolfrat.example.com/'}}), 0);
    assert.strictEqual(await worker.dispatch('fetch', {request: {method: 'GET', url: 'https://wolfrat.example.com/'}}), 1);
});

// ------------------------------------------------------------------- run
async function main() {
    let failed = 0;
    for (const [name, fn] of cases) {
        try {
            await fn();
            console.log(`ok   ${name}`);
        } catch (error) {
            failed += 1;
            console.error(`FAIL ${name}\n${error && error.stack ? error.stack : error}`);
        }
    }
    console.log(`${cases.length - failed}/${cases.length} passed`);
    if (failed) process.exitCode = 1;
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
