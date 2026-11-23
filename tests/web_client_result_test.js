// Behaviour tests for the phone page's app.js, run by tests/test_web_client.py:
//     node tests/web_client_result_test.js wolfrat/web_templates/app.js
// Each case loads app.js into a fresh fake browser (a small fake DOM, storage,
// fetch and WebSocket) and drives it through its own functions.
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const sourcePath = process.argv[2];
if (!sourcePath) {
    throw new Error('usage: node web_client_result_test.js <app.js>');
}
const SOURCE = fs.readFileSync(sourcePath, 'utf8');
const UA = 'Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 '
    + '(KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36';

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
 * A fresh page: app.js loaded with the given storage and address hash.
 * `serve(request)` answers fetch() calls ({path, method, headers, body}).
 */
function makePage({local = {}, session = {}, hash = '', serve = null} = {}) {
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

    const page = {
        doc, events, requests, sockets, history, location,
        local: new FakeStorage(local),
        session: new FakeStorage(session),
        serve: serve || (() => respond(500, {error: 'no server in this test'})),
        el: id => doc.getElementById(id),
        run: code => vm.runInContext(code, context),
        shown: id => !doc.getElementById(id).classList.contains('hide'),
    };
    const context = vm.createContext({
        console,
        document: doc,
        localStorage: page.local,
        sessionStorage: page.session,
        location,
        history,
        navigator: {userAgent: UA},
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
        matchMedia: () => ({matches: false}),
        isSecureContext: false,
        addEventListener: () => {},
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

const ok = (extra = {}) => respond(200, {ok: true, accepted: true, replies: ['OK'], ...extra});
const refused = message => respond(200, {ok: false, accepted: false, replies: [message], error: message});

/** A page already signed in with device key "k1" and an open socket. */
async function signedInPage(state = stateWith(), options = {}) {
    const page = makePage({local: {wolfkey: 'k1'}, ...options});
    await page.run('booting');
    const socket = page.sockets[0];
    socket.readyState = 1;
    socket.onopen();
    socket.onmessage({data: JSON.stringify(state)});
    return page;
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
    assert.strictEqual(await page.run(`signInWithToken('admin', 'master-token')`), true);
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
    page.run(`updateUI({type: 'state', players: null, chat: null, missions: null, server: null, teams: null, vote: null, weather: null, lag_comp: null, features: null, next_map: null, settings: null})`);
    page.run(`updateUI({type: 'state', connected: false, players: [{id: 'x', name: ''}, null, {id: 3}], missions: [{filename: ''}, null], chat: [null, {text: null}], settings: {GameTime: '12/30'}})`);
    assert.strictEqual(page.el('hdr-sub').textContent, 'No link to the game server');
    assert.strictEqual(page.el('now-left').textContent, '12', 'time left falls back to settings.gametime');
    assert.match(page.el('plist').innerHTML, /No players/);
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
