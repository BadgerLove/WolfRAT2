/* WolfRAT Web Admin: the phone page.
 *
 * Plain JavaScript: no framework, no CDN, no build step. The page runs under
 * a strict Content-Security-Policy, so it has no inline scripts and no inline
 * event handlers; every listener is added here with addEventListener.
 * Anything the server sends reaches the page through textContent or esc().
 *
 * Sign-in: each phone holds its own device key (localStorage "wolfkey"),
 * got by pairing (#pair=CODE in the address, or the code boxes) or with the
 * Web Admin username + token. Nothing else about the sign-in is stored.
 */
'use strict';

// ------------------------------------------------------------------ settings
const STORE_KEY = 'wolfkey';
const OLD_STORE_KEYS = ['wolfauth', 'wolftoken', 'wolfuser'];
const QUICK_STORE_KEY = 'wolfquick';
const PAIR_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789';
const PAIR_LENGTH = 6;
const DEFAULT_CHAT_MAX = 51;          // stock 59-character line less "[ADMIN] "
const PRIVATE_MAX = 118;              // one private line
const RECONNECT_MIN_MS = 2000;
const RECONNECT_MAX_MS = 15000;
const TOAST_MS = 7000;
const RING_LENGTH = 251.3;            // 2 x pi x 40: the time ring's circle
const FOG_MIN = 60;
const FOG_MAX = 400;
const WS_CONNECTING = 0;
const WS_OPEN = 1;
const ADMIN_TAG = '[ADMIN] ';
const DOT = ' \u00b7 ';
const DASH = '\u2014';
const ELLIPSIS = '\u2026';

const DEFAULT_QUICK = [
    'gg',
    'Map change in 2 min',
    'Keep it clean please',
    'Balancing teams',
    'Server restart in 5 min',
];
const REASONS = ['Team killing', 'Bad language', 'Spawn camping', 'Cheating'];
// [command, arguments, what it does]: the "!" picker and the Help page.
const COMMANDS = [
    ['!next', '', 'Skip to the next map'],
    ['!map', '<name>', 'Run a map from the rotation now'],
    ['!setnext', '<name>', 'Set the next map'],
    ['!list', '', 'Show the rotation in game chat'],
    ['!add', '<name>', 'Add a map to the rotation'],
    ['!remove', '<name>', 'Take a map out of the rotation'],
    ['!gametime', '<1-240>', 'Round time in minutes'],
    ['!time', '<hhmm>', 'Time of day'],
    ['!startgame', '', 'End the start delay now'],
    ['!startvote', '', 'Start the map vote now'],
    ['!mixteams', '', 'Shuffle the teams'],
    ['!balanceteams', '', 'Even up the teams'],
    ['!warn', '<player> [reason]', 'Warning in game chat'],
    ['!kick', '<player> [reason]', 'Kick a player'],
    ['!kill', '<player>', 'Kill a player'],
    ['!swap', '<player>', 'Move a player to the other team'],
    ['!ban', '<player> [reason]', "Ban on WolfRAT's own list"],
    ['!stats', '<player>', 'Career line for a player'],
    ['!storm', '[minutes]', 'Storm now'],
    ['!rain', '[minutes]', 'Rain now'],
    ['!snow', '[minutes]', 'Snow now'],
    ['!fog', '[minutes]', 'Fog now'],
    ['!clear', '[minutes]', "Dry sky, then the map's own"],
    ['!quake', '[1-40]', 'Earthquake for some seconds'],
    ['!lightning', '[far]', 'One flash of lightning'],
    ['!weather', 'on | off | status', 'Changing weather'],
    ['!sky', '<scene> | off | list', 'Sky scene'],
];
const TEAM_NAMES = {1: 'Joint Ops', 2: 'Rebels'};
const SCREENS = ['now', 'players', 'chat', 'maps', 'more', 'weather', 'lagcomp', 'help'];
const SUBPAGES = {weather: 'more', lagcomp: 'more', help: 'more'};
const BAND_CLASS = ['g', 'o', 'r'];
const ACTION_WORDS = {
    kick: 'Kick', ban: 'Ban', warn: 'Warn', kill: 'Kill',
    swap: 'Swap', zero: 'Zero score', message: 'Private message to',
};
const MODE_SHORT = {full: 'Full', manual: 'Manual', undecided: 'Waiting for an answer'};
const MODE_TEXT = {
    full: 'Full: WolfRAT does everything it is set up to do on the PC.',
    manual: "Manual: WolfRAT's automatic jobs are off (welcome lines, votes, balancing and the like), "
        + 'for when another WolfRAT looks after this server. What you press here still works.',
    undecided: 'WolfRAT is asking on the PC whether another WolfRAT looks after this server, '
        + 'and acts as Manual until it gets an answer. Pick one here to answer it.',
};
const TUNE_NAMES = {off: 'Off', watch: 'Watch', on: 'On'};
const TUNE_TEXT = {
    off: 'Off: every player uses the same Extra.',
    watch: "Watch: learns each player's best Extra and shows it, changes nothing.",
    on: 'On: each player gets their own learned Extra (within limits).',
};
// Weather preset id -> [sprite icon, colour class]. Unknown ids get a cloud.
const WEATHER_LOOK = {
    clear: ['sun', 'sun'], fog: ['fog', 'fog'], overcast: ['cloud', 'cloud'],
    rain: ['rain', 'rain'], drizzle: ['rain', 'rain'], storm: ['storm', 'storm'],
    snow: ['snow', 'snow'], blizzard: ['snow', 'snow'],
};

// --------------------------------------------------------------------- state
let deviceKey = readStore(STORE_KEY);
let ws = null;
let reconnectTimer = null;
let reconnectDelay = RECONNECT_MIN_MS;
let linkState = 'connecting';          // connecting | open | down | locked | blocked
let appShown = false;
let lastState = null;
let features = {};
let currentPlayers = [];
let currentMissions = [];
let currentChat = [];
let pendingChatTexts = [];
let operationResultTimer = null;
let currentScreen = '';
let subPushed = false;
let sheet = null;                      // {kind, actions}
let sheetHistory = false;
let ignorePop = 0;
let selectedPlayer = null;             // {pid, name} while the player sheet is up
let selectedReason = '';
let chatFilter = 'all';
let playerFilter = 'all';
let playerQuery = '';
let mapQuery = '';
let chatTarget = null;                 // null = everyone, else {pid, name}
let lastChatSeen = null;
let chatRenderKey = '';
let playersRenderKey = '';
let mapsRenderKey = '';
let weatherRenderKey = '';
let fogDragging = false;
let devices = null;
let typingTimer = null;
let busyActions = new Set();

// ------------------------------------------------------------------- helpers
function $(id) {
    return document.getElementById(id);
}

/** Text for innerHTML: escapes & < > " and ' (so it is safe in attributes too). */
function esc(value) {
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function setText(id, value) {
    const el = typeof id === 'string' ? $(id) : id;
    if (el) el.textContent = value === null || value === undefined ? '' : String(value);
}

function show(target, visible) {
    const el = typeof target === 'string' ? $(target) : target;
    if (el && el.classList) el.classList.toggle('hide', !visible);
}

function on(target, type, handler) {
    if (target && typeof target.addEventListener === 'function') {
        target.addEventListener(type, handler);
    }
}

function eachEl(selector, fn) {
    if (typeof document.querySelectorAll !== 'function') return;
    Array.prototype.forEach.call(document.querySelectorAll(selector) || [], fn);
}

function obj(value) {
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

function num(value) {
    if (value === null || value === undefined || value === '' || value === '-') return null;
    if (typeof value === 'boolean') return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
}

function plural(n, word) {
    return `${n} ${word}${n === 1 ? '' : 's'}`;
}

function fmtInt(n) {
    if (n === null || n === undefined) return DASH;
    try {
        return Number(n).toLocaleString('en-GB');
    } catch (error) {
        return String(n);
    }
}

function readStore(key, session) {
    try {
        const area = session ? sessionStorage : localStorage;
        return area.getItem(key);
    } catch (error) {
        return null;
    }
}

function writeStore(key, value) {
    try {
        localStorage.setItem(key, value);
    } catch (error) { /* private mode: the key lives for this visit only */ }
}

function dropStore(key) {
    try { localStorage.removeItem(key); } catch (error) { /* ignore */ }
    try { sessionStorage.removeItem(key); } catch (error) { /* ignore */ }
}

function settingValue(settings, key) {
    const want = key.toLowerCase();
    const found = Object.keys(obj(settings)).find(k => k.toLowerCase() === want);
    return found === undefined ? undefined : settings[found];
}

function stripExt(name) {
    return String(name || '').replace(/\.(bms|npj|npz)$/i, '');
}

function initials(name) {
    const clean = String(name || '').replace(/[^A-Za-z0-9]+/g, ' ').trim();
    if (!clean) return '?';
    let parts = clean.split(/\s+/);
    if (parts.length === 1) {
        const camel = parts[0].match(/[A-Z][a-z0-9]*/g);    // ValleyDog -> V, D
        if (camel && camel.length > 1) parts = camel;
    }
    return parts.slice(0, 2).map(part => part[0]).join('').toUpperCase();
}

function minutesShort(min) {
    const m = Math.max(0, Math.round(min));
    if (m < 60) return `${m}m`;
    return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

function minutesLong(min) {
    const m = Math.max(0, Math.round(min));
    if (m < 60) return `${m} min`;
    return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`;
}

function clock(seconds) {
    const s = Math.max(0, Math.round(seconds));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function toDate(value) {
    if (value === null || value === undefined || value === '') return null;
    const n = num(value);
    if (n !== null) return new Date(n > 1e12 ? n : n * 1000);
    const d = new Date(String(value));
    return Number.isNaN(d.getTime()) ? null : d;
}

function agoText(date) {
    const seconds = (Date.now() - date.getTime()) / 1000;
    if (seconds < 90) return 'now';
    if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
    return `${Math.round(seconds / 86400)} d ago`;
}

function dayText(date) {
    try {
        return date.toLocaleDateString('en-GB', {day: 'numeric', month: 'short'});
    } catch (error) {
        return date.toISOString().slice(0, 10);
    }
}

/** Phone keyboards type curly quotes and dashes; game chat takes plain ASCII. */
function tidyText(text) {
    return String(text || '')
        .replace(/[\u2018\u2019\u201a\u2032]/g, "'")
        .replace(/[\u201c\u201d\u201e\u2033]/g, '"')
        .replace(/[\u2013\u2014\u2212]/g, '-')
        .replace(/\u2026/g, '...')
        .replace(/\u00a0/g, ' ');
}

function isDone(result) {
    return !!result && (result.outcome === 'accepted' || result.outcome === 'verified');
}

function touchScreen() {
    try {
        return typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches;
    } catch (error) {
        return false;
    }
}

// ---------------------------------------------------------- result toasts
function operationResultSurface() {
    let surface = $('operation-result');
    if (surface) return surface;
    surface = document.createElement('div');
    surface.id = 'operation-result';
    surface.className = 'toast';
    surface.setAttribute('role', 'status');
    surface.setAttribute('aria-live', 'polite');
    document.body.appendChild(surface);
    return surface;
}

function showToast(message, tone) {
    const surface = operationResultSurface();
    surface.dataset.outcome = tone;
    surface.textContent = message;
    surface.className = `toast show t-${tone}`;
    clearTimeout(operationResultTimer);
    // Good news goes quickly; a refusal or an error stays up longer.
    const quiet = tone === 'accepted' || tone === 'verified' || tone === 'info';
    operationResultTimer = setTimeout(hideToast, quiet ? TOAST_MS - 2500 : TOAST_MS + 1000);
}

/** A new screen: drop a good-news toast left over from the last one. */
function hideQuietToast() {
    const surface = $('operation-result');
    if (!surface || !surface.dataset) return;
    const tone = surface.dataset.outcome;
    if (tone === 'accepted' || tone === 'verified' || tone === 'info') hideToast();
}

function hideToast() {
    const surface = $('operation-result');
    if (surface) surface.className = 'toast';
}

function outcomeOf(result) {
    if (result.accepted === true) {
        // The server took the command. A read-back that did not confirm it is
        // its own outcome: the server did not reject anything.
        if (result.verified === true) return 'verified';
        if (result.verified === undefined && result.ok !== false) return 'accepted';
        return 'unverified';
    }
    if (result.accepted === false || result.ok === false) return 'rejected';
    if (result.ok === true) return 'accepted';
    return 'error';
}

function renderOperationResult(data, context = {}) {
    const result = data && typeof data === 'object' ? data : {};
    const outcome = outcomeOf(result);
    const labels = {
        accepted: 'Accepted',
        rejected: 'Rejected',
        unverified: 'Unverified',
        error: 'Error',
        verified: 'Verified',
    };
    const subject = context.label
        || result.command
        || (result.action ? `${result.action} player ${result.pid || ''}`.trim() : '')
        || (result.type === 'chat_result' ? 'Server chat' : 'Server operation');
    const replies = Array.isArray(result.replies) ? result.replies.filter(Boolean) : [];
    const unverified = result.accepted === true && result.ok === false && result.verified !== true;
    const detail = result.verification_error
        || result.error
        || (unverified ? 'sent, but the read-back did not confirm it' : '')
        || replies[replies.length - 1]
        || subject;
    const message = String(detail) === String(subject)
        ? `${labels[outcome]}: ${subject}`
        : `${labels[outcome]}: ${subject} ${DASH} ${detail}`;
    showToast(message, outcome);
    return {...result, outcome};
}

// ------------------------------------------------------------------ the API
function retryAfterOf(response, data) {
    let header = null;
    try {
        header = response.headers && typeof response.headers.get === 'function'
            ? response.headers.get('Retry-After') : null;
    } catch (error) { /* ignore */ }
    const fromHeader = num(header);
    if (fromHeader !== null) return Math.max(0, fromHeader);
    const fromBody = num(data && data.retry_after);
    return fromBody === null ? null : Math.max(0, fromBody);
}

/** fetch() with the device key. Never throws: {ok, status, data, retryAfter}. */
async function api(path, options = {}) {
    const headers = {};
    const hasBody = options.body !== undefined;
    if (hasBody) headers['Content-Type'] = 'application/json';
    const key = options.key || deviceKey;
    if (options.auth !== false && key) headers.Authorization = 'Bearer ' + key;
    let response;
    try {
        response = await fetch(path, {
            method: options.method || 'GET',
            headers,
            body: hasBody ? JSON.stringify(options.body) : undefined,
            cache: 'no-store',
            credentials: 'same-origin',
        });
    } catch (error) {
        return {
            ok: false, status: 0, retryAfter: null,
            data: {error: error && error.message ? error.message : 'Connection error'},
        };
    }
    let data;
    try {
        data = await response.json();
    } catch (error) {
        data = {error: `Invalid server response (HTTP ${response.status})`};
    }
    if (!data || typeof data !== 'object' || Array.isArray(data)) data = {};
    return {
        ok: !!response.ok,
        status: Number(response.status) || 0,
        data,
        retryAfter: retryAfterOf(response, data),
    };
}

function lockedText(seconds) {
    if (seconds === null || seconds === undefined) {
        return 'Locked after too many wrong tries. Try again later.';
    }
    const minutes = Math.max(1, Math.ceil(seconds / 60));
    return `Locked after too many wrong tries. Try again in ${minutes} min.`;
}

const SIGNED_OUT_TEXT = 'This phone was signed out of WolfRAT. Pair it again to carry on.';

async function submitHttpOperation(path, body, context = {}) {
    const res = await api(path, {method: 'POST', body});
    if (res.status === 401) {
        signedOut(SIGNED_OUT_TEXT);
        return {ok: false, accepted: false, error: 'Signed out', outcome: 'error'};
    }
    if (res.status === 429) {
        return renderOperationResult({error: lockedText(res.retryAfter)}, context);
    }
    let data = res.data;
    if (res.status === 0) {
        data = {error: data.error || 'Connection error'};
    } else if (!res.ok) {
        data = {
            ...data,
            ok: undefined,
            accepted: undefined,
            verified: undefined,
            error: data.error || `HTTP ${res.status}`,
        };
    }
    return renderOperationResult(data, context);
}

// ------------------------------------------------------------- signing in
function deviceName() {
    const ua = String((typeof navigator !== 'undefined' && navigator.userAgent) || '');
    let device;
    const android = ua.match(/Android[^;)]*;\s*([^;)]+?)(?:\s+Build\/[^;)]*)?\)/);
    if (android && android[1] && !/^(K|wv|Mobile)$/i.test(android[1].trim())) device = android[1].trim();
    else if (/iPhone/.test(ua)) device = 'iPhone';
    else if (/iPad/.test(ua)) device = 'iPad';
    else if (/Android/.test(ua)) device = 'Android phone';
    else if (/Windows/.test(ua)) device = 'Windows PC';
    else if (/Macintosh/.test(ua)) device = 'Mac';
    else if (/Linux/.test(ua)) device = 'Linux PC';
    else device = 'Phone';
    let browser = '';
    if (/EdgA?\//.test(ua)) browser = 'Edge';
    else if (/SamsungBrowser/.test(ua)) browser = 'Samsung Internet';
    else if (/Firefox|FxiOS/.test(ua)) browser = 'Firefox';
    else if (/OPR\//.test(ua)) browser = 'Opera';
    else if (/Chrome|CriOS/.test(ua)) browser = 'Chrome';
    else if (/Safari/.test(ua)) browser = 'Safari';
    return (browser ? `${device}${DOT}${browser}` : device).slice(0, 48);
}

function normalizeCode(raw) {
    let text = String(raw || '');
    const at = text.indexOf('#pair=');
    if (at >= 0) text = text.slice(at + 6);
    return text.toUpperCase().split('')
        .filter(ch => PAIR_ALPHABET.indexOf(ch) >= 0)
        .join('')
        .slice(0, PAIR_LENGTH);
}

function clearHash() {
    try {
        history.replaceState(null, '', location.pathname + location.search);
    } catch (error) {
        try { location.hash = ''; } catch (ignored) { /* ignore */ }
    }
}

/** A pairing link (#pair=CODE): take the code and wipe it from the address at once. */
function takePairCodeFromHash() {
    const match = String(location.hash || '').match(/^#pair=([^&]*)/i);
    if (!match) return null;
    clearHash();
    let code = match[1];
    try { code = decodeURIComponent(code); } catch (error) { /* keep it raw */ }
    return normalizeCode(code);
}

function signinMessage(message, tone = 'bad') {
    const el = $('signin-msg');
    if (!el) return;
    el.textContent = message || '';
    el.className = 'signin-msg' + (tone && tone !== 'bad' ? ` ${tone}` : '') + (message ? '' : ' hide');
}

function showSignin(message, tone) {
    appShown = false;
    show('shell', false);
    show('connecting', false);
    show('signin', true);
    document.body.classList.remove('on-chat', 'typing');
    signinMessage(message || '', tone);
    setText('signin-lock', location.protocol === 'https:'
        ? `Secure connection${DOT}each phone has its own key`
        : `Each phone has its own key${DOT}sign it out any time`);
}

function showTokenForm(visible) {
    show('token-form', visible);
    show('pair-form', !visible);
    signinMessage('');
    const field = visible ? $('login-user') : $('pair-code');
    if (field && typeof field.focus === 'function') {
        try { field.focus(); } catch (error) { /* ignore */ }
    }
}

function showConnecting(message) {
    if (appShown) return;                 // the app stays up; the link bar says what is going on
    show('signin', false);
    show('shell', false);
    show('connecting', true);
    setText('connecting-msg', message || `Connecting${ELLIPSIS}`);
}

function showApp() {
    show('signin', false);
    show('connecting', false);
    show('shell', true);
    if (!appShown) {
        appShown = true;
        routeFromHash(true);
    }
    setLinkBar('');
}

function setBusy(id, busy, label) {
    const el = $(id);
    if (!el) return;
    el.disabled = !!busy;
    el.classList.toggle('busy', !!busy);
    if (label) el.textContent = label;
}

function renderPairBoxes() {
    const input = $('pair-code');
    const value = input ? String(input.value || '') : '';
    const focused = !!input && document.activeElement === input;
    let index = 0;
    eachEl('#pair-boxes b', box => {
        box.textContent = value[index] || '';
        box.classList.toggle('f', focused && index === Math.min(value.length, PAIR_LENGTH - 1));
        index += 1;
    });
}

function onPairInput() {
    const input = $('pair-code');
    if (!input) return;
    const clean = normalizeCode(input.value);
    if (input.value !== clean) input.value = clean;
    renderPairBoxes();
    signinMessage('');
}

/** A sign-in answer (pair or token): keep the device key, or say what went wrong. */
function acceptSignin(res, how) {
    const data = (res && res.data) || {};
    const key = res && res.ok && typeof data.key === 'string' ? data.key.trim() : '';
    if (key) {
        const previous = deviceKey;
        deviceKey = key;
        writeStore(STORE_KEY, key);
        OLD_STORE_KEYS.forEach(dropStore);
        if (previous && previous !== key) {
            // Re-paired: the old key of this phone is not needed any more.
            api('/api/logout', {method: 'POST', key: previous, body: {}});
        }
        const pass = $('login-pass');
        if (pass) pass.value = '';
        const code = $('pair-code');
        if (code) code.value = '';
        renderPairBoxes();
        signinMessage('');
        showConnecting();
        connectWS();
        return true;
    }
    const status = res ? res.status : 0;
    if (status === 429) {
        signinMessage(lockedText(res.retryAfter));
    } else if (status === 401 || status === 400) {
        signinMessage(how === 'pair'
            ? 'That code is wrong or has run out. Press Pair a phone on the PC for a new one.'
            : 'Wrong username or token.');
    } else if (status === 0) {
        signinMessage("Can't reach WolfRAT. Is it running, and is this phone on the same network?");
    } else if (res && res.ok) {
        signinMessage('This WolfRAT sent no key for this phone. Update WolfRAT and try again.');
    } else {
        signinMessage(data.error || `Sign-in failed (HTTP ${status}).`);
    }
    return false;
}

async function pairWithCode(raw) {
    const input = $('pair-code');
    const code = normalizeCode(raw === undefined ? (input ? input.value : '') : raw);
    if (code.length !== PAIR_LENGTH) {
        signinMessage(`Type all ${PAIR_LENGTH} characters of the code from the PC.`);
        return false;
    }
    if (input) input.value = code;
    renderPairBoxes();
    setBusy('pair-go', true, `Pairing${ELLIPSIS}`);
    signinMessage(`Pairing this phone${ELLIPSIS}`, 'info');
    const res = await api('/api/pair', {
        method: 'POST',
        auth: false,
        body: {code, device_name: deviceName()},
    });
    setBusy('pair-go', false, 'Pair this phone');
    return acceptSignin(res, 'pair');
}

async function signInWithToken(username, token) {
    const userField = $('login-user');
    const passField = $('login-pass');
    const user = String(username === undefined ? (userField ? userField.value : '') : username).trim();
    const secret = String(token === undefined ? (passField ? passField.value : '') : token).trim();
    if (!user || !secret) {
        signinMessage('Type the username and the access token from the Web Admin tab.');
        return false;
    }
    setBusy('token-go', true, `Signing in${ELLIPSIS}`);
    const res = await api('/api/auth', {
        method: 'POST',
        auth: false,
        body: {username: user, token: secret, device_name: deviceName()},
    });
    setBusy('token-go', false, 'Sign in');
    return acceptSignin(res, 'token');
}

function oldLogin() {
    const user = readStore('wolfuser');
    const token = readStore('wolftoken') || readStore('wolfauth') || readStore('wolfauth', true);
    return {user: user ? String(user).trim() : '', token: token ? String(token).trim() : ''};
}

function hasOldLogin() {
    return !!oldLogin().token;
}

/**
 * An older page saved the Web Admin token itself. Swap it once for a device
 * key; the old entries are deleted when that works or when the token is wrong.
 */
async function migrateOldLogin() {
    const old = oldLogin();
    if (!old.user || !old.token) {
        OLD_STORE_KEYS.forEach(dropStore);
        showSignin();
        return false;
    }
    showConnecting(`Updating this phone's sign-in${ELLIPSIS}`);
    const res = await api('/api/auth', {
        method: 'POST',
        auth: false,
        body: {username: old.user, token: old.token, device_name: deviceName()},
    });
    const key = res.ok && typeof res.data.key === 'string' ? res.data.key.trim() : '';
    if (key) return acceptSignin(res, 'token');
    if (res.status === 401 || res.status === 400) {
        OLD_STORE_KEYS.forEach(dropStore);
        showSignin('Your saved sign-in no longer works. Pair this phone again.');
        return false;
    }
    if (res.status === 429) {
        showSignin(lockedText(res.retryAfter));
        return false;
    }
    if (res.status === 0 || res.status >= 500) {
        showSignin(`Can't reach WolfRAT just now. Trying again${ELLIPSIS}`, 'info');
        setTimeout(() => {
            if (!deviceKey && !appShown && hasOldLogin()) migrateOldLogin();
        }, 10000);
        return false;
    }
    showSignin(res.data.error || `Sign-in failed (HTTP ${res.status}).`);
    return false;
}

function closeSocket() {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
    const socket = ws;
    ws = null;
    pendingChatTexts = [];
    if (socket) {
        socket.onopen = null;
        socket.onmessage = null;
        socket.onclose = null;
        socket.onerror = null;
        try { socket.close(); } catch (error) { /* ignore */ }
    }
}

/** The key no longer works (or the admin logged out): back to the sign-in screen. */
function signedOut(message, tone) {
    deviceKey = null;
    dropStore(STORE_KEY);
    closeSocket();
    closeSheet(true);
    lastState = null;
    devices = null;
    linkState = 'connecting';
    showSignin(message, tone);
}

async function logout() {
    const key = deviceKey;
    let res = null;
    if (key) res = await api('/api/logout', {method: 'POST', key, body: {}});
    const ok = !key || (res && (res.ok || res.status === 401));
    signedOut(ok
        ? 'Signed out. Pair again to come back.'
        : "Signed out on this phone. WolfRAT didn't answer, so remove this phone under Devices on another phone.",
    ok ? 'ok' : 'bad');
    return {ok: true, outcome: 'accepted'};
}

function askLogout() {
    openConfirm({
        title: 'Log out this phone?',
        text: 'It forgets its key. To come back, pair it again with a new code from the PC.',
        confirm: 'Log out',
        tone: 'danger',
        run: () => logout(),
    });
}

// --------------------------------------------------------------- the link
function setLinkBar(text, tone) {
    const bar = $('linkbar');
    if (!bar) return;
    bar.textContent = text || '';
    bar.className = 'linkbar' + (tone ? ` ${tone}` : '') + (text ? '' : ' hide');
}

function setLinkState(state, text) {
    linkState = state;
    if (state === 'open') {
        setLinkBar('');
    } else if (appShown) {
        const tone = state === 'locked' || state === 'blocked' ? 'bad' : '';
        setLinkBar(text || `Reconnecting to WolfRAT${ELLIPSIS}`, tone);
    } else if (text) {
        setText('connecting-msg', text);
    }
    renderHeader();
}

/** Ask the API why the socket failed (a browser never says). */
async function checkKey() {
    if (!deviceKey) return 'signedout';
    const res = await api('/api/status');
    if (!deviceKey) return 'signedout';
    if (res.status === 401) {
        signedOut(SIGNED_OUT_TEXT);
        return 'signedout';
    }
    if (res.status === 429) {
        setLinkState('locked', lockedText(res.retryAfter));
        return 'locked';
    }
    if (res.status === 403) {
        setLinkState('blocked', res.data.error || 'WolfRAT refused this phone.');
        return 'blocked';
    }
    if (res.ok) {
        updateUI(res.data);
        return 'ok';
    }
    if (!appShown) setText('connecting-msg', `Can't reach WolfRAT yet. Trying again${ELLIPSIS}`);
    return 'down';
}

function scheduleReconnect(checkNow) {
    if (!deviceKey || reconnectTimer) return;
    const wait = reconnectDelay;
    reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
    if (checkNow) checkKey();
    reconnectTimer = setTimeout(async () => {
        reconnectTimer = null;
        if (!deviceKey) return;
        const check = await checkKey();
        if (check === 'signedout') return;
        if (check === 'locked' || check === 'blocked') {
            scheduleReconnect(false);
            return;
        }
        connectWS();
    }, wait);
}

function connectWS() {
    if (!deviceKey) return;
    if (ws && (ws.readyState === WS_CONNECTING || ws.readyState === WS_OPEN)) return;
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    let socket;
    try {
        socket = new WebSocket(`${proto}//${location.host}/ws?key=${encodeURIComponent(deviceKey)}`);
    } catch (error) {
        scheduleReconnect(false);
        return;
    }
    let opened = false;
    ws = socket;
    socket.onopen = () => {
        if (ws !== socket) return;
        opened = true;
        reconnectDelay = RECONNECT_MIN_MS;
        setLinkState('open');
        showApp();
    };
    socket.onmessage = event => {
        if (ws !== socket) return;
        let data;
        try {
            data = JSON.parse(event.data);
        } catch (error) {
            return;
        }
        if (data && typeof data === 'object') handleWebSocketMessage(data);
    };
    socket.onclose = event => {
        if (ws !== socket) return;
        ws = null;
        pendingChatTexts = [];
        setLinkState('down');
        // Signed out from the PC (4001), or never got in: ask why at once.
        scheduleReconnect(!opened || (event && event.code === 4001));
    };
    socket.onerror = () => {};
}

function onWake() {
    if (typeof document.visibilityState === 'string' && document.visibilityState !== 'visible') return;
    if (!deviceKey) return;
    if (ws && (ws.readyState === WS_CONNECTING || ws.readyState === WS_OPEN)) return;
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
    reconnectDelay = RECONNECT_MIN_MS;
    connectWS();
}

function handleWebSocketMessage(data) {
    const type = String(data.type || '');
    if (type === 'state') {
        updateUI(data);
    } else if (type === 'chat') {
        updateChat(Array.isArray(data.chat) ? data.chat : []);
    } else if (type === 'error' || type.endsWith('_result')) {
        const result = renderOperationResult(data);
        if (type === 'chat_result' && pendingChatTexts.length) {
            const submitted = pendingChatTexts.shift();
            const input = $('chat-input');
            if (isDone(result) && input && input.value.trim() === submitted) {
                input.value = '';
                updateCounter();
            }
        }
    }
}

// ----------------------------------------------------------------- screens
function go(name, options = {}) {
    const target = SCREENS.indexOf(name) >= 0 ? name : 'now';
    const url = `${location.pathname}${location.search}#${target}`;
    try {
        if (options.push) history.pushState({screen: target}, '', url);
        else history.replaceState({screen: target}, '', url);
    } catch (error) { /* ignore */ }
    if (options.push) subPushed = true;
    showScreen(target);
}

function goBack(parent) {
    if (subPushed) {
        subPushed = false;
        try {
            history.back();
            return;
        } catch (error) { /* fall through */ }
    }
    go(parent || 'now');
}

function routeFromHash(force) {
    const wanted = String(location.hash || '').replace(/^#/, '');
    showScreen(SCREENS.indexOf(wanted) >= 0 ? wanted : 'now', force);
}

function showScreen(name, force) {
    if (name === currentScreen && !force) return;
    const was = currentScreen;
    currentScreen = name;
    if (was) hideQuietToast();
    SCREENS.forEach(s => {
        const el = $(`s-${s}`);
        if (el) el.classList.toggle('on', s === name);
    });
    const navKey = SUBPAGES[name] || name;
    eachEl('nav.bottom button', button => button.classList.toggle('on', button.dataset.nav === navKey));
    document.body.classList.toggle('on-chat', name === 'chat');
    if (name !== 'chat') document.body.classList.remove('typing');
    if (name === 'chat') {
        noteUnread(true);
        chatRenderKey = '';
        renderChat();
        setTimeout(scrollChatToEnd, 0);
    } else {
        try { window.scrollTo(0, 0); } catch (error) { /* ignore */ }
    }
    if (name === 'more' && was !== 'more') loadDevices();
}

function scrollToId(id) {
    const el = $(id);
    if (!el || typeof el.scrollIntoView !== 'function') return;
    setTimeout(() => {
        try { el.scrollIntoView({behavior: 'smooth', block: 'start'}); } catch (error) { el.scrollIntoView(); }
    }, 40);
}

function onPopState() {
    if (ignorePop > 0) {
        // Our own history.back() after closing a sheet: keep the address on
        // the screen that is showing now.
        ignorePop -= 1;
        try { history.replaceState(null, '', `${location.pathname}${location.search}#${currentScreen || 'now'}`); } catch (error) { /* ignore */ }
        return;
    }
    if (sheet) {
        sheetHistory = false;
        closeSheet(true);
        return;
    }
    subPushed = false;
    if (appShown) routeFromHash();
}

// ------------------------------------------------------------ bottom sheet
function openSheet(kind, html, actions) {
    const el = $('sheet');
    const body = $('sheet-body');
    if (!el || !body) return;
    body.innerHTML = html;
    sheet = {kind, actions: actions || {}};
    el.classList.add('on');
    el.setAttribute('aria-hidden', 'false');
    const scrim = $('scrim');
    if (scrim) scrim.classList.add('on');
    try { el.scrollTop = 0; } catch (error) { /* ignore */ }
    if (!sheetHistory) {
        // Android's back button closes the sheet instead of leaving the page.
        try {
            history.pushState({wolfSheet: true}, '', location.href);
            sheetHistory = true;
        } catch (error) { /* ignore */ }
    }
}

function closeSheet(fromHistory) {
    const wasOpen = !!sheet;
    sheet = null;
    selectedPlayer = null;
    const el = $('sheet');
    if (el) {
        el.classList.remove('on');
        el.setAttribute('aria-hidden', 'true');
    }
    const scrim = $('scrim');
    if (scrim) scrim.classList.remove('on');
    if (sheetHistory) {
        sheetHistory = false;
        if (!fromHistory) {
            ignorePop += 1;
            try {
                history.back();
            } catch (error) {
                ignorePop -= 1;
            }
        }
    }
    return wasOpen;
}

function sheetAct(act, el) {
    if (!sheet || !sheet.actions || typeof sheet.actions[act] !== 'function') return undefined;
    return sheet.actions[act](el || {dataset: {}});
}

/**
 * A confirm sheet. `run(choice)` does the work and returns a result; a done
 * result closes the sheet, anything else goes `back` (or closes it).
 */
function openConfirm(opts) {
    const choices = Array.isArray(opts.choices) ? opts.choices : [];
    let choice = choices.length ? choices[0].id : undefined;
    const chips = choices.length
        ? `<div class="ov">${esc(opts.choiceTitle || '')}</div><div id="cf-choices" class="reasons">${
            choices.map((c, i) => `<button class="fchip${i === 0 ? ' on' : ''}" type="button" data-act="pick" data-id="${esc(c.id)}">${esc(c.label)}</button>`).join('')
        }</div>`
        : '';
    const html = `<p class="sh-title">${esc(opts.title)}</p>`
        + (opts.text ? `<p class="sh-text">${esc(opts.text)}</p>` : '')
        + chips
        + '<div class="sh-btns">'
        + '<button class="btn" type="button" data-act="no">Cancel</button>'
        + `<button id="cf-yes" class="btn ${opts.tone === 'danger' ? 'bad' : 'acc'}" type="button" data-act="yes">${esc(opts.confirm || 'OK')}</button>`
        + '</div>';
    let busy = false;
    openSheet('confirm', html, {
        pick: el => {
            choice = el.dataset.id;
            eachEl('#cf-choices .fchip', chip => chip.classList.toggle('on', chip === el));
        },
        no: () => {
            if (busy) return;
            if (opts.back) opts.back();
            else closeSheet();
        },
        yes: async () => {
            if (busy) return undefined;
            busy = true;
            const mine = sheet;
            const button = $('cf-yes');
            if (button) {
                button.disabled = true;
                button.textContent = `Working${ELLIPSIS}`;
            }
            let result = null;
            try {
                result = await opts.run(choice);
            } finally {
                busy = false;
            }
            if (!deviceKey) return result;                // signed out meanwhile
            if (sheet !== mine && sheet !== null) return result;   // another sheet took over
            if (isDone(result)) closeSheet();
            else if (opts.back) opts.back();
            else closeSheet();
            return result;
        },
    });
}

// ------------------------------------------------------------------- state
function updateUI(state) {
    if (!state || typeof state !== 'object') return;
    lastState = state;
    features = obj(state.features);
    currentPlayers = Array.isArray(state.players) ? state.players : [];
    currentMissions = Array.isArray(state.missions) ? state.missions : [];
    applyFeatures();
    renderHeader();
    renderNow();
    renderPlayers(currentPlayers);
    if (Array.isArray(state.chat)) updateChat(state.chat);
    renderMaps(currentMissions);
    renderMore();
    renderWeather();
    renderLagComp();
    refreshOpenSheet();
    markTargetGone();
    updateComposerLimit();
}

function applyFeatures() {
    show('qa-set-next', !!features.set_next);
    show('qa-weather', !!features.weather);
    show('chat-to', !!features.private_message);
    show('mp-change', !!features.set_next);
    show('help-pair', features.pairing !== false);
    if (!features.private_message && chatTarget) setChatTarget(null);
}

function playerCount() {
    const count = num(lastState && lastState.player_count);
    return count !== null ? count : currentPlayers.filter(validPlayer).length;
}

function teamCode(value) {
    const t = String(value === null || value === undefined ? '' : value).trim();
    return t === '1' || t === '2' ? t : '0';
}

function teamClass(code) {
    return code === '1' ? 'jo' : code === '2' ? 'reb' : '';
}

function teamName(code) {
    const teams = Array.isArray(lastState && lastState.teams) ? lastState.teams : [];
    const entry = teams.find(t => t && String(t.team) === code);
    if (entry && entry.name) return String(entry.name);
    const player = currentPlayers.find(p => p && teamCode(p.team) === code && p.team_name);
    if (player) return String(player.team_name);
    return TEAM_NAMES[code] || 'Other';
}

function validPlayer(p) {
    return !!p && typeof p === 'object'
        && Number.isInteger(Number(p.id)) && p.id !== null && p.id !== ''
        && String(p.name === null || p.name === undefined ? '' : p.name) !== '';
}

function findPlayer(pid, name) {
    const id = Number(pid);
    return currentPlayers.find(p => validPlayer(p) && Number(p.id) === id && String(p.name) === String(name)) || null;
}

function teamOfName(name) {
    const player = currentPlayers.find(p => validPlayer(p) && String(p.name) === name);
    return player ? teamCode(player.team) : '0';
}

function pingBand(p) {
    const band = num(p && p.ping_band);
    if (band === 0 || band === 1 || band === 2) return band;
    const ping = num(p && p.ping);
    if (ping === null) return null;
    return ping < 80 ? 0 : ping < 150 ? 1 : 2;
}

function pingText(p) {
    const ping = num(p && p.ping);
    return ping === null ? DASH : `${ping} ms`;
}

function kdText(kills, deaths) {
    if (kills === null || deaths === null) return DASH;
    return (kills / Math.max(1, deaths)).toFixed(2);
}

function classText(p) {
    const value = String(p && p.class !== undefined && p.class !== null ? p.class : '').trim();
    return /^\d*$/.test(value) ? '' : value;
}

function validMission(m) {
    return !!m && typeof m === 'object'
        && m.queue_index !== null && m.queue_index !== '' && Number.isInteger(Number(m.queue_index))
        && String(m.filename || '') !== '';
}

function missionTitle(m) {
    return String((m && m.display_name) || stripExt(m && m.filename) || '');
}

function findMission(queueIndex, filename) {
    const index = Number(queueIndex);
    const file = String(filename || '').toLowerCase();
    const named = currentMissions.filter(m => validMission(m) && String(m.filename).toLowerCase() === file);
    return named.find(m => Number(m.queue_index) === index) || (named.length === 1 ? named[0] : null);
}

/** The next map: the server's next_map when it sends one, else the queue's flag. */
function isNextMission(m) {
    const next = lastState && lastState.next_map;
    if (next && typeof next === 'object' && next.filename) {
        if (String(m.filename).toLowerCase() !== String(next.filename).toLowerCase()) return false;
        const index = num(next.queue_index);
        return index === null || Number(m.queue_index) === index;
    }
    return !!m.is_next;
}

function currentMission() {
    return currentMissions.find(m => validMission(m) && m.is_current) || null;
}

function nextMapInfo() {
    const next = lastState && lastState.next_map;
    if (next && typeof next === 'object' && (next.filename || next.display_name)) {
        return {
            title: String(next.display_name || stripExt(next.filename)),
            filename: String(next.filename || ''),
            queue_index: next.queue_index,
        };
    }
    const queued = currentMissions.find(m => validMission(m) && m.is_next);
    if (queued) return {title: missionTitle(queued), filename: queued.filename, queue_index: queued.queue_index};
    return null;
}

function timeInfo(state) {
    const server = obj(state && state.server);
    let left = num(server.time_left_min);
    let limit = num(server.time_limit_min);
    if (left === null && limit === null) {
        const raw = settingValue(obj(state && state.settings), 'gametime');
        const match = String(raw === undefined || raw === null ? '' : raw).match(/^\s*(\d+)\s*\/\s*(\d+)\s*$/);
        if (match) {
            left = Number(match[1]);
            limit = Number(match[2]);
        }
    }
    return {left, limit};
}

function teamTotals() {
    const teams = Array.isArray(lastState && lastState.teams) ? lastState.teams : [];
    const result = ['1', '2'].map(code => {
        const entry = teams.find(t => t && String(t.team) === code);
        const players = currentPlayers.filter(p => validPlayer(p) && teamCode(p.team) === code);
        const kills = entry && num(entry.kills) !== null
            ? num(entry.kills)
            : players.reduce((sum, p) => sum + (num(p.kills) || 0), 0);
        const count = entry && num(entry.players) !== null ? num(entry.players) : players.length;
        return {code, name: teamName(code), kills, players: count};
    });
    return result;
}

// ------------------------------------------------------------- header
function renderHeader() {
    const state = lastState || {};
    const server = obj(state.server);
    setText('hdr-name', server.name || settingValue(obj(state.settings), 'servername') || 'WolfRAT');
    let sub;
    let tone;
    if (linkState === 'locked') {
        sub = 'Locked';
        tone = 'bad';
    } else if (linkState !== 'open') {
        sub = `Reconnecting${ELLIPSIS}`;
        tone = 'warn';
    } else if (!lastState) {
        sub = `Connecting${ELLIPSIS}`;
        tone = '';
    } else if (state.connected === false) {
        sub = 'No link to the game server';
        tone = 'bad';
    } else {
        const parts = ['Live'];
        const type = server.game_type || server.game_type_name;
        if (type) parts.push(type);
        parts.push(plural(playerCount(), 'player'));
        sub = parts.join(DOT);
        tone = 'ok';
    }
    setText('hdr-sub', sub);
    const dot = $('hdr-dot');
    if (dot) dot.className = 'live' + (tone ? ` ${tone}` : '');
    const mode = state.manual_mode;
    const tag = $('hdr-manual');
    if (tag) {
        tag.textContent = mode === 'undecided' ? 'MODE?' : 'MANUAL';
        show(tag, mode === 'manual' || mode === 'undecided');
    }
    const badge = $('nav-players');
    if (badge) {
        const count = lastState ? playerCount() : 0;
        badge.textContent = String(count);
        show(badge, count > 0);
    }
}

// ---------------------------------------------------------------- NOW
function renderNow() {
    const state = lastState || {};
    const server = obj(state.server);
    const current = currentMission();
    const valid = currentMissions.filter(validMission);
    setText('now-title', server.map_title || (current && missionTitle(current)) || stripExt(server.map_file) || DASH);
    const typeChip = $('now-type');
    if (typeChip) {
        const type = String(server.game_type || '');
        typeChip.textContent = type;
        typeChip.title = server.game_type_name ? String(server.game_type_name) : '';
        show(typeChip, !!type);
    }
    const parts = [];
    const file = server.map_file || (current && current.filename) || '';
    if (file) parts.push(file);
    if (current && valid.length) parts.push(`map ${valid.indexOf(current) + 1} of ${valid.length}`);
    setText('now-file', parts.join(DOT));

    const time = timeInfo(state);
    const ring = $('now-ring');
    const fg = $('now-ring-fg');
    let offset = RING_LENGTH;
    if (time.left === null) {
        setText('now-left', DASH);
        setText('now-limit', 'time left');
    } else if (time.limit === 0) {
        setText('now-left', DASH);
        setText('now-limit', 'no limit');
    } else {
        setText('now-left', String(Math.round(time.left)));
        setText('now-limit', time.limit ? `of ${Math.round(time.limit)} min` : 'min left');
        if (time.limit) offset = RING_LENGTH * (1 - Math.max(0, Math.min(1, time.left / time.limit)));
    }
    if (fg) fg.setAttribute('stroke-dashoffset', offset.toFixed(1));
    if (ring) ring.classList.toggle('low', time.left !== null && time.limit !== 0 && time.left <= 5);

    const next = nextMapInfo();
    setText('now-next-name', next ? next.title : 'Not set');

    renderTeams();
    renderServerList();
}

function renderTeams() {
    const el = $('now-teams');
    if (!el) return;
    const [a, b] = teamTotals();
    const total = a.kills + b.kills;
    const share = total > 0 ? (a.kills / total) * 100 : 50;
    const diff = a.players - b.players;
    const balance = Math.abs(diff) <= 1
        ? 'Teams even'
        : `${diff > 0 ? a.name : b.name} +${plural(Math.abs(diff), 'player')}`;
    el.innerHTML = `<div class="team jo"><div class="nm">${esc(a.name)}</div><div class="sc num">${esc(a.kills)}</div><small>kills${DOT}${esc(plural(a.players, 'player'))}</small></div>`
        + '<div class="vs">VS</div>'
        + `<div class="team reb"><div class="nm">${esc(b.name)}</div><div class="sc num">${esc(b.kills)}</div><small>kills${DOT}${esc(plural(b.players, 'player'))}</small></div>`
        + (total > 0
            ? `<div class="split"><i style="width:${share.toFixed(1)}%"></i></div>`
            : '<div class="split none"></div>')
        + `<div class="balance"><span>${esc(balance)}</span><span>${esc(plural(total, 'kill'))} in all</span></div>`;
}

function listRow(icon, title, sub, right, target) {
    const inner = `<span class="ic"><svg class="i sm"><use href="#${icon}"/></svg></span>`
        + `<div class="main"><b>${esc(title)}</b><small>${esc(sub)}</small></div>${right}`;
    if (!target) return `<div class="li">${inner}</div>`;
    const scroll = target.scroll ? ` data-scroll="${esc(target.scroll)}"` : '';
    return `<button class="li" type="button" data-go="${esc(target.go)}"${scroll}>${inner}</button>`;
}

function chip(text, tone) {
    return `<span class="chip${tone ? ` ${tone}` : ''}">${esc(text)}</span>`;
}

const CHEVRON = '<svg class="i sm faint"><use href="#chev"/></svg>';

function weatherLine(weather) {
    const w = obj(weather);
    const parts = [];
    if (w.summary) parts.push(String(w.summary));
    const fog = num(w.fog_m);
    if (fog !== null && !(w.summary && String(w.summary).indexOf(`${fog}`) >= 0)) parts.push(`fog ${fog} m`);
    if (w.dynamic) parts.push('changing');
    return parts.join(DOT) || DASH;
}

function lagLine(lc) {
    const l = obj(lc);
    if (l.summary) return String(l.summary);
    const tune = TUNE_NAMES[l.tune_mode];
    return (l.enabled ? 'On' : 'Off') + (tune ? `${DOT}auto-tune ${tune}` : '');
}

function weatherShort(weather) {
    const w = obj(weather);
    if (w.summary) return String(w.summary);
    const fog = num(w.fog_m);
    return fog !== null ? `Fog ${fog} m` : DASH;
}

function lagShort(lc) {
    const l = obj(lc);
    const tune = TUNE_NAMES[l.tune_mode];
    return (l.enabled ? 'On' : 'Off') + (tune ? `${DOT}${tune}` : '');
}

function renderServerList() {
    const el = $('now-server');
    if (!el) return;
    const state = lastState || {};
    const rows = [];
    const linked = state.connected !== false;
    rows.push(listRow('link', 'Game server link',
        linked ? 'WolfRAT is connected to the game server' : 'WolfRAT has no link to the game server',
        chip(linked ? 'OK' : 'DOWN', linked ? 'g' : 'r')));
    const mode = state.manual_mode;
    if (MODE_SHORT[mode]) {
        rows.push(listRow('toggle', 'WolfRAT mode', MODE_SHORT[mode],
            mode === 'full' ? chip('FULL', 'g') : chip(mode === 'manual' ? 'MANUAL' : 'ASK', 'o'),
            features.manual_switch ? {go: 'more', scroll: 'mode-card'} : null));
    }
    if (features.lag_comp && state.lag_comp && typeof state.lag_comp === 'object') {
        const lc = state.lag_comp;
        rows.push(listRow('target', 'Lag compensation', lagLine(lc),
            chip(lc.enabled ? 'ON' : 'OFF', lc.enabled ? 'y' : ''), {go: 'lagcomp'}));
    }
    if (state.vote && typeof state.vote === 'object') {
        const vote = state.vote;
        const votes = num(vote.votes) || 0;
        const needed = num(vote.needed);
        const secs = num(vote.seconds_left);
        const sub = vote.active
            ? `${vote.map || 'A map'}${DOT}${votes}${needed ? ` of ${needed}` : ''} votes`
            : 'No vote running';
        rows.push(listRow('vote', 'Map vote', sub,
            vote.active ? chip(`OPEN${secs !== null ? DOT + clock(secs) : ''}`, 'o') : chip('Waiting'),
            {go: 'maps'}));
    }
    if (state.weather && typeof state.weather === 'object') {
        rows.push(listRow('fog', 'Weather', weatherLine(state.weather),
            features.weather ? CHEVRON : '', features.weather ? {go: 'weather'} : null));
    }
    el.innerHTML = rows.join('');
}

function isAdminLine(m) {
    return chatKind(m) === 'wolfrat' && chatText(m).indexOf(ADMIN_TAG) === 0;
}

function renderEvents() {
    const el = $('now-events');
    if (!el) return;
    const lines = currentChat
        .filter(m => m && typeof m === 'object')
        .filter(m => (chatKind(m) === 'wolfrat' && !isAdminLine(m)) || chatKind(m) === 'console')
        .slice(-5)
        .reverse();
    el.innerHTML = lines.length
        ? lines.map(m => `<div class="ev"><time class="num">${esc(m.time)}</time><span class="d${chatKind(m) === 'console' ? ' con' : ''}"></span><span>${esc(chatText(m))}</span></div>`).join('')
        : '<p class="empty">Nothing yet</p>';
}

// ------------------------------------------------------------- PLAYERS
function playerMeta(p) {
    const parts = [];
    if (p.rank) parts.push(`<span class="rkt">${esc(p.rank)}</span>`);
    if (p.country) parts.push(esc(String(p.country).toUpperCase()));
    const on = num(p.time_on_min);
    if (on !== null) parts.push(esc(minutesShort(on)));
    if (!parts.length && classText(p)) parts.push(esc(classText(p)));
    if (!parts.length) parts.push(esc(teamName(teamCode(p.team))));
    return parts.join(DOT);
}

function playerRowHtml(p) {
    const code = teamCode(p.team);
    const kills = num(p.kills);
    const deaths = num(p.deaths);
    const band = pingBand(p);
    return `<div class="pl" role="button" tabindex="0" data-pid="${esc(Number(p.id))}" data-pname="${esc(p.name)}">`
        + `<div class="av ${teamClass(code)}">${esc(initials(p.name))}</div>`
        + `<div class="main"><div class="nm"><span>${esc(p.name)}</span></div><div class="meta">${playerMeta(p)}</div></div>`
        + `<div class="kd"><b class="num">${esc(kills === null ? DASH : kills)}/${esc(deaths === null ? DASH : deaths)}</b><small>K/D ${esc(kdText(kills, deaths))}</small></div>`
        + `<span class="ping${band === null ? '' : ` ${BAND_CLASS[band]}`} num">${esc(pingText(p))}</span>`
        + '</div>';
}

function renderPlayers(players) {
    if (Array.isArray(players)) currentPlayers = players;
    const all = currentPlayers.filter(validPlayer);
    const counts = {
        all: all.length,
        1: all.filter(p => teamCode(p.team) === '1').length,
        2: all.filter(p => teamCode(p.team) === '2').length,
        ping: all.filter(p => pingBand(p) === 2).length,
    };
    const chips = [
        ['all', `All ${counts.all}`],
        ['1', `${teamName('1')} ${counts[1]}`],
        ['2', `${teamName('2')} ${counts[2]}`],
    ];
    if (counts.ping || playerFilter === 'ping') chips.push(['ping', `High ping ${counts.ping}`]);
    const findOpen = !!$('pl-find') && !$('pl-find').classList.contains('hide');
    const chipEl = $('pl-chips');
    if (chipEl) {
        chipEl.innerHTML = chips.map(([key, label]) => `<button class="fchip${playerFilter === key ? ' on' : ''}" type="button" data-pf="${esc(key)}">${esc(label)}</button>`).join('')
            + `<button class="fchip${findOpen ? ' on' : ''}" type="button" data-pf="find"><svg class="i sm"><use href="#search"/></svg>Find</button>`;
    }

    const query = playerQuery.trim().toLowerCase();
    const shown = all.filter(p => {
        if (playerFilter === '1' || playerFilter === '2') {
            if (teamCode(p.team) !== playerFilter) return false;
        } else if (playerFilter === 'ping' && pingBand(p) !== 2) {
            return false;
        }
        return !query || String(p.name).toLowerCase().indexOf(query) >= 0;
    });
    const el = $('plist');
    if (!el) return;
    const key = JSON.stringify([shown, lastState && lastState.teams, playerFilter, query]);
    if (key === playersRenderKey) return;
    playersRenderKey = key;
    if (!shown.length) {
        el.innerHTML = `<p class="empty">${all.length ? 'No player matches' : 'No players on the server'}</p>`;
        return;
    }
    const html = [];
    ['1', '2', '0'].forEach(code => {
        const group = shown.filter(p => teamCode(p.team) === code);
        if (!group.length) return;
        const cls = teamClass(code);
        const kills = group.reduce((sum, p) => sum + (num(p.kills) || 0), 0);
        html.push(`<div class="thead"><span class="bar${cls ? ` ${cls}` : ''}"></span><b${cls ? ` class="${cls}"` : ''}>${esc(teamName(code))}</b><small>${esc(plural(group.length, 'player'))}${DOT}${esc(plural(kills, 'kill'))}</small></div>`);
        group.forEach(p => html.push(playerRowHtml(p)));
    });
    el.innerHTML = html.join('');
}

function setPlayerFilter(value) {
    if (value === 'find') {
        const box = $('pl-find');
        const opening = !!box && box.classList.contains('hide');
        show(box, opening);
        const input = $('pl-find-input');
        if (opening && input) {
            try { input.focus(); } catch (error) { /* ignore */ }
        } else {
            playerQuery = '';
            if (input) input.value = '';
        }
        playersRenderKey = '';
        renderPlayers();
        return;
    }
    playerFilter = value;
    playersRenderKey = '';
    renderPlayers();
}

// ------------------------------------------------------ the player sheet
function playerHeadHtml(p) {
    const code = teamCode(p.team);
    const band = pingBand(p);
    const sub = [teamName(code)];
    if (classText(p)) sub.push(classText(p));
    const on = num(p.time_on_min);
    if (on !== null) sub.push(`on for ${minutesLong(on)}`);
    if (p.country) sub.push(String(p.country).toUpperCase());
    return `<div class="sh-head"><div class="av ${teamClass(code)}">${esc(initials(p.name))}</div>`
        + `<div class="grow"><b>${esc(p.name)}</b>${p.rank ? ` <span class="rk">${esc(p.rank)}</span>` : ''}<small>${esc(sub.join(DOT))}</small></div>`
        + `<span class="ping${band === null ? '' : ` ${BAND_CLASS[band]}`} num">${esc(pingText(p))}</span></div>`;
}

function playerStatsHtml(p) {
    const kills = num(p.kills);
    const deaths = num(p.deaths);
    const xp = num(p.xp);
    const stats = [
        [kills === null ? DASH : kills, 'Kills'],
        [deaths === null ? DASH : deaths, 'Deaths'],
        [kdText(kills, deaths), 'K/D'],
    ];
    if (xp !== null) stats.push([fmtInt(xp), 'XP']);
    return stats.map(([value, label]) => `<div class="stat"><b class="num">${esc(value)}</b><small>${esc(label)}</small></div>`).join('');
}

function actButton(act, icon, label, tone) {
    return `<button class="act" type="button" data-act="${act}"><span class="ic${tone ? ` ${tone}` : ''}"><svg class="i"><use href="#${icon}"/></svg></span>${esc(label)}</button>`;
}

function playerSheetHtml(p) {
    const acts = [];
    if (features.private_message) acts.push(actButton('message', 'msg', 'Message', 'acc'));
    acts.push(actButton('swap', 'swap', 'Swap team', 'jo'));
    acts.push(actButton('kill', 'skull', 'Kill'));
    acts.push(actButton('zero', 'zero', 'Zero score'));
    const buttons = [];
    if (features.warn) buttons.push('<button class="dbtn warnb" type="button" data-act="warn"><svg class="i"><use href="#alert"/></svg>Warn</button>');
    buttons.push('<button class="dbtn kick" type="button" data-act="kick"><svg class="i"><use href="#kick"/></svg>Kick</button>');
    if (features.bans) buttons.push(`<button class="dbtn ban" type="button" data-act="ban"><svg class="i"><use href="#ban"/></svg>Ban${ELLIPSIS}</button>`);
    const reasons = REASONS.map(r => `<button class="fchip" type="button" data-act="reason" data-reason="${esc(r)}">${esc(r)}</button>`).join('')
        + `<button class="fchip" type="button" data-act="reason" data-reason="">Other${ELLIPSIS}</button>`;
    const heading = (features.bans ? 'Kick or ban' : 'Kick') + `${DOT}pick a reason`;
    const xp = num(p.xp) !== null;
    return `<div id="ps-head">${playerHeadHtml(p)}</div>`
        + `<div id="ps-stats" class="stats" style="grid-template-columns:repeat(${xp ? 4 : 3},minmax(0,1fr))">${playerStatsHtml(p)}</div>`
        + '<p id="ps-gone" class="sh-gone hide">This player has left the server.</p>'
        + `<div class="acts" style="grid-template-columns:repeat(${acts.length},minmax(0,1fr))">${acts.join('')}</div>`
        + '<div class="dzone">'
        + `<div class="ov">${esc(heading)}</div>`
        + `<div id="ps-reasons" class="reasons">${reasons}</div>`
        + '<input id="ps-reason" class="field hide" type="text" maxlength="60" placeholder="Type a reason" autocomplete="off" aria-label="Reason">'
        + `<div class="dbtns" style="--cols:${buttons.length}">${buttons.join('')}</div>`
        + (features.bans
            ? "<p class=\"note\">Ban goes on WolfRAT's own ban list (name + address), the same as the Bans tab on the PC, and you pick how long. The server's own ban is not used.</p>"
            : '')
        + '</div>';
}

function currentReason() {
    const input = $('ps-reason');
    if (input && input.classList && !input.classList.contains('hide')) {
        return tidyText(input.value).trim().slice(0, 60);
    }
    return selectedReason;
}

const PLAYER_SHEET_ACTIONS = {
    reason: el => {
        const value = el.dataset.reason || '';
        const already = el.classList.contains('on');
        eachEl('#ps-reasons .fchip', item => item.classList.remove('on'));
        const input = $('ps-reason');
        if (already) {
            selectedReason = '';
            show(input, false);
            return;
        }
        el.classList.add('on');
        selectedReason = value;
        show(input, !value);
        if (!value && input) {
            try { input.focus(); } catch (error) { /* ignore */ }
        }
    },
    message: () => {
        if (!selectedPlayer || !features.private_message) return;
        const target = {...selectedPlayer};
        closeSheet();
        setChatTarget(target);
        go('chat');
        focusChat();
    },
    swap: () => confirmPlayerAction('swap'),
    kill: () => confirmPlayerAction('kill'),
    zero: () => confirmPlayerAction('zero'),
    warn: () => {
        const reason = currentReason();
        if (!reason) {
            showToast('Pick or type a reason for the warning first.', 'info');
            return null;
        }
        return playerAction('warn', {reason});
    },
    kick: () => {
        const reason = currentReason();
        return playerAction('kick', reason ? {reason} : {});
    },
    ban: () => confirmBan(),
};

function openPlayerSheet(pid, name) {
    const player = findPlayer(pid, name);
    if (!player) {
        showToast(`${name || 'That player'} is no longer on the server.`, 'info');
        return false;
    }
    selectedReason = '';
    openSheet('player', playerSheetHtml(player), PLAYER_SHEET_ACTIONS);
    selectedPlayer = {pid: Number(player.id), name: String(player.name)};
    return true;
}

function reopenPlayerSheet(target) {
    if (!openPlayerSheet(target.pid, target.name)) closeSheet();
}

function refreshOpenSheet() {
    if (!sheet || sheet.kind !== 'player' || !selectedPlayer) return;
    const player = findPlayer(selectedPlayer.pid, selectedPlayer.name);
    if (player) {
        const head = $('ps-head');
        if (head) head.innerHTML = playerHeadHtml(player);
        const stats = $('ps-stats');
        if (stats) stats.innerHTML = playerStatsHtml(player);
    }
    show('ps-gone', !player);
}

function playerAction(action, extra = {}) {
    if (!selectedPlayer) return Promise.resolve(null);
    const target = {...selectedPlayer};
    const body = {pid: target.pid, name: target.name, action, ...extra};
    const word = ACTION_WORDS[action] || action;
    return submitHttpOperation('/api/player/action', body, {label: `${word} ${target.name}`}).then(result => {
        if (
            isDone(result)
            && selectedPlayer
            && selectedPlayer.pid === target.pid
            && selectedPlayer.name === target.name
        ) {
            closeSheet();
        }
        return result;
    });
}

function confirmPlayerAction(action) {
    const target = selectedPlayer ? {...selectedPlayer} : null;
    if (!target) return;
    const player = findPlayer(target.pid, target.name);
    const code = player ? teamCode(player.team) : '0';
    const other = code === '1' ? teamName('2') : code === '2' ? teamName('1') : 'the other team';
    const name = target.name;
    const texts = {
        swap: [`Swap ${name}?`, `Moves ${name} to ${other}. They are killed first, as !swap does.`, 'Swap team', 'acc'],
        kill: [`Kill ${name}?`, 'They respawn as normal.', 'Kill', 'danger'],
        zero: [`Zero ${name}'s score?`, "Kills and deaths go back to 0. This can't be undone.", 'Zero score', 'danger'],
    };
    const [title, text, confirm, tone] = texts[action];
    openConfirm({
        title, text, confirm, tone,
        run: () => {
            selectedPlayer = target;
            return playerAction(action);
        },
        back: () => reopenPlayerSheet(target),
    });
    selectedPlayer = target;
}

function confirmBan() {
    const target = selectedPlayer ? {...selectedPlayer} : null;
    if (!target || !features.bans) return;
    const reason = currentReason();
    const expiry = (Array.isArray(features.ban_expiry) ? features.ban_expiry : [])
        .filter(e => e && typeof e === 'object' && e.id !== undefined && e.id !== null)
        .map(e => ({id: String(e.id), label: String(e.label || e.id || 'Never')}));
    openConfirm({
        title: `Ban ${target.name}?`,
        text: (reason ? `Reason: ${reason}. ` : 'No reason given. ')
            + 'WolfRAT bans the name and the address, then removes the player.',
        confirm: 'Ban',
        tone: 'danger',
        choiceTitle: 'How long',
        choices: expiry,
        run: choice => {
            selectedPlayer = target;
            const extra = {};
            if (reason) extra.reason = reason;
            if (choice !== undefined) extra.expiry = choice;
            return playerAction('ban', extra);
        },
        back: () => reopenPlayerSheet(target),
    });
    selectedPlayer = target;
}

// ------------------------------------------------------------------ CHAT
function chatText(m) {
    const text = m.text !== undefined && m.text !== null ? m.text : m.raw;
    return String(text === undefined || text === null ? '' : text);
}

function chatKind(m) {
    if (m.kind === 'player' || m.kind === 'wolfrat' || m.kind === 'console') return m.kind;
    if (m.own) return 'wolfrat';
    return /^[^:]{1,40}: /.test(chatText(m)) ? 'player' : 'console';
}

function chatSender(m) {
    if (m.name) return String(m.name);
    const text = chatText(m);
    const at = text.indexOf(': ');
    return at > 0 ? text.slice(0, at) : '';
}

function chatBody(m, sender) {
    const text = chatText(m);
    if (sender && text.indexOf(`${sender}: `) === 0) return text.slice(sender.length + 2);
    if (!m.name) {
        const at = text.indexOf(': ');
        if (at > 0) return text.slice(at + 2);
    }
    return text;
}

function chatFilterMatch(m) {
    const kind = chatKind(m);
    if (chatFilter === 'players') return kind === 'player';
    if (chatFilter === 'wolfrat') return kind === 'wolfrat';
    if (chatFilter === 'server') return kind === 'console';
    if (chatFilter === 'commands') return kind === 'player' && chatBody(m, chatSender(m)).trim().indexOf('!') === 0;
    return true;
}

function chatLineHtml(m) {
    const kind = chatKind(m);
    const time = m.time ? `<time>${esc(m.time)}</time>` : '';
    if (kind === 'player') {
        const sender = chatSender(m);
        const body = chatBody(m, sender);
        const team = teamClass(teamOfName(sender));
        const command = body.trim().indexOf('!') === 0 ? ' cmd' : '';
        return `<div class="m${team ? ` ${team}` : ''}${command}"><div class="who">${esc(sender || 'Player')} ${time}</div><div class="body">${esc(body)}</div></div>`;
    }
    const text = chatText(m);
    if (kind === 'wolfrat' && text.indexOf(ADMIN_TAG) === 0) {
        return `<div class="m me"><div class="who">Web Admin ${time}</div><div class="body">${esc(text.slice(ADMIN_TAG.length))}</div></div>`;
    }
    if (kind === 'wolfrat') {
        return `<div class="sys"><img class="wi" src="/static/icon-192.png" alt=""><span>${esc(text)}</span>${time}</div>`;
    }
    return `<div class="sys con"><svg class="i"><use href="#info"/></svg><span>${esc(text)}</span>${time}</div>`;
}

function chatNearEnd() {
    try {
        const doc = document.documentElement;
        return window.innerHeight + window.scrollY >= doc.scrollHeight - 120;
    } catch (error) {
        return true;
    }
}

function scrollChatToEnd() {
    if (currentScreen !== 'chat') return;
    try {
        window.scrollTo(0, document.documentElement.scrollHeight);
    } catch (error) { /* ignore */ }
}

function renderChat(messages) {
    if (Array.isArray(messages)) currentChat = messages;
    const el = $('chat-messages');
    if (!el) return;
    const lines = currentChat.filter(m => m && typeof m === 'object');
    const shown = lines.filter(chatFilterMatch);
    const key = JSON.stringify([chatFilter, shown.map(m => [m.id, m.time, m.text, m.kind, m.own, m.name]), currentPlayers.map(p => p && [p.name, p.team])]);
    if (key === chatRenderKey) return;
    chatRenderKey = key;
    const stick = currentScreen === 'chat' && chatNearEnd();
    el.innerHTML = shown.length
        ? shown.map(chatLineHtml).join('')
        : `<p class="empty">${lines.length ? 'Nothing here with this filter' : 'No chat yet'}</p>`;
    if (stick) setTimeout(scrollChatToEnd, 0);
}

function chatKey(m) {
    if (!m) return '';
    return m.id !== undefined && m.id !== null ? `i${m.id}` : `t${m.time}|${m.text}`;
}

function noteUnread(seen) {
    const latest = currentChat.length ? chatKey(currentChat[currentChat.length - 1]) : '';
    if (seen || lastChatSeen === null || currentScreen === 'chat') lastChatSeen = latest;
    show('nav-chat', latest !== lastChatSeen);
}

function updateChat(messages) {
    currentChat = Array.isArray(messages) ? messages : [];
    renderChat(currentChat);
    renderEvents();
    noteUnread(false);
}

function setChatFilter(value) {
    chatFilter = value || 'all';
    eachEl('#chat-chips [data-cf]', button => button.classList.toggle('on', button.dataset.cf === chatFilter));
    chatRenderKey = '';
    renderChat();
}

function chatMax() {
    if (chatTarget) return PRIVATE_MAX;
    const max = num(lastState && lastState.chat_max);
    return max !== null && max > 0 ? max : DEFAULT_CHAT_MAX;
}

function updateCounter() {
    const input = $('chat-input');
    const count = $('chat-count');
    if (!input || !count) return;
    const max = chatMax();
    const length = String(input.value || '').length;
    count.textContent = `${length}/${max}`;
    count.className = 'cnt num' + (length >= max ? ' bad' : length >= max - 8 ? ' warn' : '');
}

function updateComposerLimit() {
    const input = $('chat-input');
    if (input) input.maxLength = chatMax();
    updateCounter();
}

function markTargetGone() {
    const to = $('chat-to');
    if (!to || !to.classList) return;
    to.classList.toggle('gone', !!chatTarget && !findPlayer(chatTarget.pid, chatTarget.name));
}

function setChatTarget(target) {
    chatTarget = target && target.name ? {pid: Number(target.pid), name: String(target.name)} : null;
    setText('chat-to-label', chatTarget ? chatTarget.name : 'All');
    const to = $('chat-to');
    if (to && to.classList) to.classList.toggle('priv', !!chatTarget);
    const input = $('chat-input');
    if (input) input.placeholder = chatTarget ? `Private to ${chatTarget.name}` : 'Message everyone';
    markTargetGone();
    updateComposerLimit();
}

function focusChat() {
    const input = $('chat-input');
    if (input && typeof input.focus === 'function') {
        try { input.focus(); } catch (error) { /* ignore */ }
    }
}

function clearSentText(message) {
    const input = $('chat-input');
    if (input && input.value.trim() === message) {
        input.value = '';
        updateCounter();
    }
}

function sendChatHttp(message) {
    const label = message.indexOf('!') === 0 ? message.split(/\s+/)[0] : 'Server chat';
    return submitHttpOperation('/api/chat/send', {message}, {label}).then(result => {
        if (isDone(result)) clearSentText(message);
        return result;
    });
}

function sendPrivate(target, message) {
    return submitHttpOperation(
        '/api/player/action',
        {pid: target.pid, name: target.name, action: 'message', message},
        {label: `Private to ${target.name}`},
    ).then(result => {
        if (isDone(result)) clearSentText(message);
        return result;
    });
}

/** Chat line, or a typed !command (WolfRAT runs those; they are not posted in chat). */
function sendChat() {
    const input = $('chat-input');
    if (!input) return Promise.resolve(null);
    const tidy = tidyText(input.value);
    if (tidy !== input.value) input.value = tidy;
    const message = tidy.trim();
    if (!message) return Promise.resolve(null);
    if (chatTarget) return sendPrivate(chatTarget, message);
    if (ws && ws.readyState === WS_OPEN) {
        pendingChatTexts.push(message);
        try {
            ws.send(JSON.stringify({type: 'chat', message}));
            return Promise.resolve(null);
        } catch (error) {
            pendingChatTexts.pop();
        }
    }
    return sendChatHttp(message);
}

/** A !command from a button: the same path as typing it. */
function sendCommand(command, label) {
    return submitHttpOperation('/api/chat/send', {message: command}, {label: label || command});
}

function quickReplies() {
    try {
        const saved = JSON.parse(readStore(QUICK_STORE_KEY) || 'null');
        if (Array.isArray(saved)) {
            const clean = saved.map(item => String(item).trim()).filter(Boolean).slice(0, 12);
            if (clean.length) return clean;
        }
    } catch (error) { /* fall back to the defaults */ }
    return DEFAULT_QUICK.slice();
}

function renderQuickReplies() {
    const el = $('quick');
    if (!el) return;
    el.innerHTML = quickReplies().map((text, i) => `<button class="fchip" type="button" data-quick="${i}">${esc(text)}</button>`).join('')
        + '<button class="fchip" type="button" data-quick="edit">+ Edit</button>';
}

function useQuickReply(index) {
    const text = quickReplies()[index];
    const input = $('chat-input');
    if (text === undefined || !input) return;
    input.value = text.slice(0, chatMax());
    updateCounter();
    focusChat();
}

function openQuickEditor() {
    openSheet('quick', '<p class="sh-title">Quick replies</p>'
        + '<p class="sh-text">One per line. A tap puts it in the message box; nothing is sent until you press Send.</p>'
        + `<textarea id="quick-edit" class="field" rows="7" maxlength="1200" spellcheck="false" aria-label="Quick replies">${esc(quickReplies().join('\n'))}</textarea>`
        + '<div class="sh-btns"><button class="btn" type="button" data-act="reset">Reset</button><button class="btn acc" type="button" data-act="save">Save</button></div>', {
        save: () => {
            const box = $('quick-edit');
            const lines = String(box ? box.value : '').split('\n')
                .map(line => tidyText(line).trim()).filter(Boolean).slice(0, 12);
            writeStore(QUICK_STORE_KEY, JSON.stringify(lines.length ? lines : DEFAULT_QUICK));
            renderQuickReplies();
            closeSheet();
        },
        reset: () => {
            dropStore(QUICK_STORE_KEY);
            renderQuickReplies();
            closeSheet();
        },
    });
}

function commandRowHtml(command, i, attr) {
    const [cmd, args, text] = command;
    return `<button class="pick" type="button" ${attr}="${i}"><div class="main"><b><code>${esc(cmd)}</code>${args ? ` <span class="faint">${esc(args)}</span>` : ''}</b><small>${esc(text)}</small></div></button>`;
}

function useCommand(command) {
    if (!command) return;
    setChatTarget(null);
    const input = $('chat-input');
    if (input) input.value = command[0] + (command[1] ? ' ' : '');
    updateCounter();
    if (currentScreen !== 'chat') go('chat');
    focusChat();
}

function openCommandPicker() {
    openSheet('commands', '<p class="sh-title">Commands</p>'
        + '<p class="sh-text">WolfRAT runs these as Web Admin; they are not posted in game chat.</p>'
        + `<div class="picklist">${COMMANDS.map((c, i) => commandRowHtml(c, i, 'data-act="cmd" data-i')).join('')}</div>`, {
        cmd: el => {
            const command = COMMANDS[Number(el.dataset.i)];
            closeSheet();
            useCommand(command);
        },
    });
}

function openTargetPicker() {
    if (!features.private_message) return;
    const rows = [`<button class="pick${chatTarget ? '' : ' on'}" type="button" data-act="all"><span class="av"><svg class="i sm"><use href="#mega"/></svg></span><div class="main"><b>Everyone</b><small>Game chat, with [ADMIN] in front</small></div></button>`];
    currentPlayers.filter(validPlayer).forEach(p => {
        const code = teamCode(p.team);
        const picked = chatTarget && chatTarget.pid === Number(p.id) && chatTarget.name === String(p.name);
        rows.push(`<button class="pick${picked ? ' on' : ''}" type="button" data-act="one" data-pid="${esc(Number(p.id))}" data-pname="${esc(p.name)}"><span class="av ${teamClass(code)}">${esc(initials(p.name))}</span><div class="main"><b>${esc(p.name)}</b><small>Private line${DOT}${esc(teamName(code))}</small></div></button>`);
    });
    openSheet('target', `<p class="sh-title">Send to</p><div class="picklist">${rows.join('')}</div>`, {
        all: () => {
            setChatTarget(null);
            closeSheet();
            focusChat();
        },
        one: el => {
            setChatTarget({pid: el.dataset.pid, name: el.dataset.pname});
            closeSheet();
            focusChat();
        },
    });
}

// ------------------------------------------------------------------ MAPS
function mapRowHtml(m, position) {
    const chips = [];
    if (m.is_current) chips.push(chip('NOW', 'y'));
    if (isNextMission(m)) chips.push(chip('NEXT'));
    if (m.double_time) chips.push(chip('x2'));
    return `<div class="li mp${m.is_current ? ' cur' : ''}" role="button" tabindex="0" data-queue-index="${esc(Number(m.queue_index))}" data-filename="${esc(m.filename)}">`
        + `<span class="idx num">${position}</span>`
        + `<div class="main"><b>${esc(missionTitle(m))}</b><small>${esc(m.filename)}</small></div>`
        + (chips.length ? `<span class="chips2">${chips.join('')}</span>` : '')
        + '<span class="kebab" aria-hidden="true"><svg class="i sm"><use href="#kebab"/></svg></span></div>';
}

function renderMaps(missions) {
    if (Array.isArray(missions)) currentMissions = missions;
    const valid = currentMissions.filter(validMission);
    setText('mp-count', `Rotation${DOT}${plural(valid.length, 'map')}`);
    renderMapCards();
    const el = $('map-list');
    if (!el) return;
    const query = mapQuery.trim().toLowerCase();
    const rows = valid
        .map((m, i) => [m, i + 1])
        .filter(([m]) => !query
            || missionTitle(m).toLowerCase().indexOf(query) >= 0
            || String(m.filename).toLowerCase().indexOf(query) >= 0);
    const key = JSON.stringify([rows, query]);
    if (key === mapsRenderKey) return;
    mapsRenderKey = key;
    el.innerHTML = rows.length
        ? rows.map(([m, position]) => mapRowHtml(m, position)).join('')
        : `<p class="empty">${valid.length ? 'No map matches' : 'No maps in the rotation'}</p>`;
}

function renderMapCards() {
    const state = lastState || {};
    const server = obj(state.server);
    const current = currentMission();
    const title = server.map_title || (current && missionTitle(current)) || stripExt(server.map_file) || DASH;
    setText('mp-now-name', title);
    setText('mp-now-thumb', String(server.game_type || '') || initials(title));
    const time = timeInfo(state);
    setText('mp-now-sub', time.left !== null && time.limit !== 0
        ? `${Math.round(time.left)} min left`
        : (server.map_file || (current && current.filename) || ''));
    const next = nextMapInfo();
    setText('mp-next-name', next ? next.title : 'Not set');
    setText('mp-next-thumb', next ? initials(next.title) : DASH);
    setText('mp-next-sub', next ? next.filename : (features.set_next ? 'Tap Change to pick one' : ''));

    const card = $('mp-vote');
    if (!card) return;
    const vote = state.vote;
    if (vote && typeof vote === 'object' && vote.active) {
        const votes = num(vote.votes) || 0;
        const needed = num(vote.needed);
        const secs = num(vote.seconds_left);
        const pct = needed ? Math.min(100, Math.round((votes / needed) * 100)) : 0;
        const more = needed ? Math.max(0, needed - votes) : null;
        card.innerHTML = `<div class="row"><span class="ov grow">Map vote</span>${chip(`OPEN${secs !== null ? DOT + clock(secs) : ''}`, 'o')}</div>`
            + `<div class="opt"><div class="row"><span class="grow">${esc(vote.map || 'A map')}</span><b class="num">${esc(votes)}${needed ? `/${esc(needed)}` : ''}</b></div><div class="bar"><i style="width:${pct}%"></i></div></div>`
            + (more !== null ? `<p class="note">${esc(more ? `${plural(more, 'more vote')} needed` : 'Enough votes')}</p>` : '');
        show(card, true);
    } else {
        show(card, false);
    }
}

function switchMap(queueIndex, filename, title) {
    return submitHttpOperation(
        '/api/map/switch',
        {index: queueIndex, map: filename},
        {label: `Run ${title || filename}`},
    );
}

function setNextMap(queueIndex, filename, title) {
    return submitHttpOperation(
        '/api/map/setnext',
        {filename, index: queueIndex},
        {label: `Next map: ${title || filename}`},
    );
}

function quickAction(action, label) {
    return submitHttpOperation('/api/action', {action}, {label});
}

function openMapSheet(queueIndex, filename) {
    const mission = findMission(queueIndex, filename);
    if (!mission) {
        showToast('That map is no longer in the rotation.', 'info');
        return false;
    }
    const valid = currentMissions.filter(validMission);
    const title = missionTitle(mission);
    const sub = [mission.filename, `map ${valid.indexOf(mission) + 1} of ${valid.length}`];
    if (mission.is_current) sub.push('playing now');
    if (isNextMission(mission)) sub.push('next');
    if (mission.double_time) sub.push('x2');
    const buttons = ['<button class="btn acc wide" type="button" data-act="run"><svg class="i"><use href="#play"/></svg>Run now</button>'];
    if (features.set_next && !isNextMission(mission)) {
        buttons.push('<button class="btn wide" type="button" data-act="next"><svg class="i"><use href="#listplus"/></svg>Set as next map</button>');
    }
    const index = Number(mission.queue_index);
    const file = String(mission.filename);
    openSheet('map', `<p class="sh-title">${esc(title)}</p><p class="sh-text">${esc(sub.join(DOT))}</p><div class="stack">${buttons.join('')}</div>`, {
        run: () => confirmRunNow(index, file, title),
        next: () => setNextMap(index, file, title).then(result => {
            if (isDone(result)) closeSheet();
            return result;
        }),
    });
    return true;
}

function confirmRunNow(index, file, title) {
    openConfirm({
        title: `Run ${title} now?`,
        text: `The round ends now and ${title} loads.`,
        confirm: 'Run now',
        tone: 'acc',
        run: () => switchMap(index, file, title),
        back: () => {
            if (!openMapSheet(index, file)) closeSheet();
        },
    });
}

function confirmNextMap() {
    const next = nextMapInfo();
    openConfirm({
        title: 'Skip to the next map?',
        text: next ? `The round ends now and ${next.title} loads.` : 'The round ends now and the next map loads.',
        confirm: 'Next map',
        tone: 'acc',
        run: () => quickAction('next_map', 'Next map'),
    });
}

function openNextPicker() {
    if (!features.set_next) return;
    const rows = currentMissions.filter(validMission).map((m, i) => {
        const notes = [m.filename];
        const next = isNextMission(m);
        if (m.is_current) notes.push('playing now');
        if (next) notes.push('next');
        return `<button class="pick${next ? ' on' : ''}" type="button" data-act="pick" data-qi="${esc(Number(m.queue_index))}" data-file="${esc(m.filename)}"><span class="idx num faint">${i + 1}</span><div class="main"><b>${esc(missionTitle(m))}</b><small>${esc(notes.join(DOT))}</small></div></button>`;
    });
    openSheet('nextpick', `<p class="sh-title">Pick the next map</p><div class="picklist">${rows.join('') || '<p class="empty">No maps in the rotation</p>'}</div>`, {
        pick: el => {
            const index = Number(el.dataset.qi);
            const file = el.dataset.file;
            const mission = findMission(index, file);
            return setNextMap(index, file, mission ? missionTitle(mission) : file).then(result => {
                if (isDone(result)) closeSheet();
                return result;
            });
        },
    });
}

function quickActionTap(kind) {
    if (kind === 'next_map') return confirmNextMap();
    if (kind === 'set_next') return openNextPicker();
    if (kind === 'message') {
        setChatTarget(null);
        go('chat');
        focusChat();
        return null;
    }
    if (kind === 'weather') return go('weather', {push: true});
    if (kind === 'mix') {
        return openConfirm({
            title: 'Mix the teams?',
            text: 'WolfRAT shuffles the players; many will change sides.',
            confirm: 'Mix teams',
            tone: 'acc',
            run: () => sendCommand('!mixteams', 'Mix teams'),
        });
    }
    if (kind === 'start') return sendCommand('!startgame', 'Start game');
    return null;
}

// ------------------------------------------------------------------ MORE
function renderMore() {
    const state = lastState || {};
    show('tile-weather', !!features.weather);
    setText('tile-weather-sub', state.weather ? weatherShort(state.weather) : DASH);
    show('tile-lagcomp', !!features.lag_comp);
    setText('tile-lagcomp-sub', state.lag_comp ? lagShort(state.lag_comp) : DASH);
    const mode = state.manual_mode;
    show('tile-mode', !!features.manual_switch);
    setText('tile-mode-sub', MODE_SHORT[mode] || DASH);
    show('mode-card', !!features.manual_switch);
    eachEl('#mode-seg button', button => button.classList.toggle('on', button.dataset.mode === mode));
    setText('mode-text', MODE_TEXT[mode] || '');
    setText('tile-devices-sub', devices ? plural(devices.length, 'phone') : 'Phones signed in');
}

async function loadDevices() {
    const list = $('dev-list');
    const res = await api('/api/devices');
    if (res.status === 401) {
        signedOut(SIGNED_OUT_TEXT);
        return;
    }
    if (!res.ok) {
        devices = null;
        if (list) {
            list.innerHTML = `<p class="empty">${res.status === 404 ? 'This WolfRAT has no device list.' : "Couldn't load the device list."}</p>`;
        }
        renderMore();
        return;
    }
    devices = (Array.isArray(res.data.devices) ? res.data.devices : []).filter(d => d && typeof d === 'object');
    renderDevices();
    renderMore();
}

function deviceRowHtml(d) {
    const name = String(d.name || 'Phone');
    const created = toDate(d.created);
    const seen = toDate(d.last_seen);
    const parts = [];
    if (created) parts.push(`Paired ${dayText(created)}`);
    parts.push(seen ? `last seen ${agoText(seen)}` : 'not seen yet');
    if (d.last_ip) parts.push(String(d.last_ip));
    const right = d.this_device
        ? chip('YOU', 'g')
        : `<button class="fchip sm" type="button" data-signout="${esc(d.id)}" data-name="${esc(name)}">Sign out</button>`;
    return `<div class="li"><span class="ic${d.this_device ? ' acc' : ''}"><svg class="i sm"><use href="#phone"/></svg></span>`
        + `<div class="main"><b>${esc(name)}${d.this_device ? ' (this phone)' : ''}</b><small>${esc(parts.join(DOT))}</small></div>${right}</div>`;
}

function renderDevices() {
    const list = $('dev-list');
    if (!list || !devices) return;
    if (!devices.length) {
        list.innerHTML = '<p class="empty">No phones signed in</p>';
        return;
    }
    const sorted = devices.slice().sort((a, b) => (b.this_device ? 1 : 0) - (a.this_device ? 1 : 0));
    list.innerHTML = sorted.map(deviceRowHtml).join('');
}

function askSignOutDevice(id, name) {
    openConfirm({
        title: `Sign out ${name || 'that phone'}?`,
        text: 'It loses its key at once and needs a new code from the PC to come back.',
        confirm: 'Sign out',
        tone: 'danger',
        run: async () => {
            const result = await submitHttpOperation('/api/devices/signout', {id}, {label: `Sign out ${name || 'phone'}`});
            if (deviceKey) loadDevices();
            return result;
        },
    });
}

function setMode(mode) {
    if (mode !== 'full' && mode !== 'manual') return null;
    return submitHttpOperation('/api/mode', {mode}, {label: mode === 'manual' ? 'Manual mode' : 'Full mode'});
}

// --------------------------------------------------------------- WEATHER
function clampFog(value) {
    return Math.max(FOG_MIN, Math.min(FOG_MAX, Math.round(value)));
}

function updateFogLabel(value) {
    const range = $('fog-range');
    const metres = value === null || value === undefined ? null : clampFog(value);
    setText('fog-val', metres === null ? DASH : `${metres} m`);
    const shown = metres === null ? (range ? Number(range.value) : FOG_MIN) : metres;
    if (range && range.style && typeof range.style.setProperty === 'function') {
        range.style.setProperty('--fill', `${(((shown - FOG_MIN) / (FOG_MAX - FOG_MIN)) * 100).toFixed(1)}%`);
    }
}

function presetHtml(preset, weather) {
    const id = String(preset.id);
    const look = WEATHER_LOOK[id.toLowerCase()] || ['cloud', 'cloud'];
    const label = String(preset.label || preset.id);
    const summary = weather && weather.summary ? String(weather.summary).toLowerCase() : '';
    const active = !!summary && summary.indexOf(label.toLowerCase()) >= 0;
    return `<button class="wxb${active ? ' on' : ''}" type="button" data-preset="${esc(id)}" data-label="${esc(label)}"><span class="em ${look[1]}"><svg class="i"><use href="#${look[0]}"/></svg></span>${esc(label)}</button>`;
}

function renderWeather() {
    const available = !!features.weather;
    show('wx-off', !available);
    show('wx-on', available);
    const weather = lastState && lastState.weather && typeof lastState.weather === 'object' ? lastState.weather : null;
    setText('wx-summary', weather && weather.summary ? weather.summary : 'No reading yet');
    const dynamic = $('wx-dyn');
    if (dynamic) {
        const changing = !!(weather && weather.dynamic);
        dynamic.textContent = changing ? 'Changing: on' : 'Changing: off';
        dynamic.className = 'chip' + (changing ? ' g' : '');
        show(dynamic, !!weather);
    }
    const presets = (Array.isArray(features.weather_presets) ? features.weather_presets : [])
        .filter(p => p && typeof p === 'object' && p.id !== undefined && p.id !== null && p.id !== '');
    const key = JSON.stringify([presets, weather && weather.summary]);
    const box = $('wx-presets');
    if (box && key !== weatherRenderKey) {
        weatherRenderKey = key;
        box.innerHTML = presets.length
            ? presets.map(p => presetHtml(p, weather)).join('')
            : '<p class="empty">No presets on this WolfRAT</p>';
    }
    if (!fogDragging) {
        const fog = weather ? num(weather.fog_m) : null;
        const range = $('fog-range');
        if (fog !== null && range) range.value = String(clampFog(fog));
        updateFogLabel(fog);
    }
}

function setWeatherPreset(id, label) {
    return submitHttpOperation('/api/weather', {preset: id}, {label: `Weather: ${label || id}`});
}

function setFog(metres) {
    const value = clampFog(Number(metres));
    updateFogLabel(value);
    return submitHttpOperation('/api/weather', {fog_m: value}, {label: `Fog ${value} m`});
}

// -------------------------------------------------------------- LAG COMP
function renderLagComp() {
    const available = !!features.lag_comp;
    const lc = lastState && lastState.lag_comp && typeof lastState.lag_comp === 'object' ? lastState.lag_comp : null;
    show('lc-on', available && !!lc);
    show('lc-off', !(available && lc));
    const off = $('lc-off');
    if (off && !(available && lc)) {
        off.innerHTML = `<p class="note">${available ? 'No reading from lag compensation yet.' : "Lag compensation isn't available on this WolfRAT."}</p>`;
    }
    if (!lc) return;
    const enabled = !!lc.enabled;
    const toggle = $('lc-switch');
    if (toggle) {
        toggle.className = 'switch' + (enabled ? '' : ' off');
        toggle.setAttribute('aria-checked', enabled ? 'true' : 'false');
        toggle.disabled = busyActions.has('lagcomp');
    }
    setText('lc-state', enabled ? 'On' : 'Off');
    setText('lc-summary', lc.summary || '');
    const mode = TUNE_NAMES[lc.tune_mode] ? lc.tune_mode : '';
    const busy = busyActions.has('lagcomp');
    eachEl('#lc-tune button', button => {
        button.classList.toggle('on', button.dataset.tune === mode);
        button.disabled = busy;
    });
    setText('lc-tune-note', TUNE_TEXT[mode] || '');
}

async function runOnce(name, work) {
    if (busyActions.has(name)) return null;
    busyActions.add(name);
    renderLagComp();
    try {
        return await work();
    } finally {
        busyActions.delete(name);
        renderLagComp();
    }
}

function setLagComp(enabled) {
    return runOnce('lagcomp', () => submitHttpOperation(
        '/api/lagcomp',
        {enabled: !!enabled},
        {label: enabled ? 'Lag compensation on' : 'Lag compensation off'},
    ));
}

function setTuneMode(mode) {
    if (!TUNE_NAMES[mode]) return null;
    return runOnce('lagcomp', () => submitHttpOperation(
        '/api/lagcomp',
        {tune_mode: mode},
        {label: `Auto-tune: ${TUNE_NAMES[mode]}`},
    ));
}

// -------------------------------------------------------------- HELP
function renderHelp() {
    const el = $('help-cmds');
    if (!el) return;
    el.innerHTML = COMMANDS.map((c, i) => commandRowHtml(c, i, 'data-usecmd')).join('');
}

// ---------------------------------------------------------- refresh button
async function refreshNow() {
    const button = $('hdr-refresh');
    if (!button || button.classList.contains('spin')) return;
    button.classList.add('spin');
    const results = await Promise.all([
        api('/api/action', {method: 'POST', body: {action: 'refresh_players'}}),
        api('/api/action', {method: 'POST', body: {action: 'refresh_chat'}}),
    ]);
    const status = await api('/api/status');
    button.classList.remove('spin');
    if ([...results, status].some(res => res.status === 401)) {
        signedOut(SIGNED_OUT_TEXT);
        return;
    }
    if (status.ok) updateUI(status.data);
    // Refresh has no read-back, so only a refusal is worth a toast.
    const failed = results.find(res => !res.ok || res.data.accepted === false);
    if (failed) {
        renderOperationResult(
            failed.ok ? failed.data : {error: failed.status === 429 ? lockedText(failed.retryAfter) : (failed.data.error || `HTTP ${failed.status}`)},
            {label: 'Refresh'},
        );
    }
}

// ------------------------------------------------------------ the events
function onShellClick(event) {
    const t = event.target;
    if (!t || typeof t.closest !== 'function') return;
    let el;
    if ((el = t.closest('[data-nav]'))) { go(el.dataset.nav); return; }
    if ((el = t.closest('[data-back]'))) { goBack(el.dataset.back); return; }
    if ((el = t.closest('[data-qa]'))) { quickActionTap(el.dataset.qa); return; }
    if ((el = t.closest('[data-go]'))) {
        if (el.dataset.filter) setChatFilter(el.dataset.filter);
        go(el.dataset.go, {push: !!SUBPAGES[el.dataset.go]});
        if (el.dataset.scroll) scrollToId(el.dataset.scroll);
        return;
    }
    if ((el = t.closest('[data-scroll]'))) { scrollToId(el.dataset.scroll); return; }
    if ((el = t.closest('.pl[data-pid]'))) { openPlayerSheet(el.dataset.pid, el.dataset.pname); return; }
    if ((el = t.closest('[data-pf]'))) { setPlayerFilter(el.dataset.pf); return; }
    if ((el = t.closest('.mp[data-filename]'))) { openMapSheet(el.dataset.queueIndex, el.dataset.filename); return; }
    if ((el = t.closest('[data-cf]'))) { setChatFilter(el.dataset.cf); return; }
    if ((el = t.closest('[data-quick]'))) {
        if (el.dataset.quick === 'edit') openQuickEditor();
        else useQuickReply(Number(el.dataset.quick));
        return;
    }
    if ((el = t.closest('[data-cmd]'))) { sendCommand(el.dataset.cmd); return; }
    if ((el = t.closest('[data-preset]'))) { setWeatherPreset(el.dataset.preset, el.dataset.label); return; }
    if ((el = t.closest('[data-fog]'))) {
        const range = $('fog-range');
        if (range) range.value = el.dataset.fog;
        setFog(Number(el.dataset.fog));
        return;
    }
    if ((el = t.closest('[data-mode]'))) { setMode(el.dataset.mode); return; }
    if ((el = t.closest('[data-tune]'))) { setTuneMode(el.dataset.tune); return; }
    if ((el = t.closest('[data-signout]'))) { askSignOutDevice(el.dataset.signout, el.dataset.name); return; }
    if ((el = t.closest('[data-logout]'))) { askLogout(); return; }
    if ((el = t.closest('[data-usecmd]'))) { useCommand(COMMANDS[Number(el.dataset.usecmd)]); }
}

function onSheetClick(event) {
    const t = event.target;
    if (!t || typeof t.closest !== 'function') return;
    const el = t.closest('[data-act]');
    if (el && !el.disabled) sheetAct(el.dataset.act, el);
}

function onRoleButtonKey(event) {
    const t = event.target;
    if (!t || (event.key !== 'Enter' && event.key !== ' ')) return;
    if (typeof t.getAttribute === 'function' && t.getAttribute('role') === 'button') {
        event.preventDefault();
        t.click();
    }
}

function bindEvents() {
    on($('shell'), 'click', onShellClick);
    on($('shell'), 'keydown', onRoleButtonKey);
    on($('sheet'), 'click', onSheetClick);
    on($('scrim'), 'click', () => closeSheet());
    on($('operation-result'), 'click', hideToast);
    on(document, 'keydown', event => {
        if (event.key === 'Escape' && sheet) closeSheet();
    });
    on(window, 'popstate', onPopState);
    on(document, 'visibilitychange', onWake);
    on(window, 'online', onWake);

    on($('hdr-refresh'), 'click', refreshNow);
    on($('hdr-manual'), 'click', () => {
        go('more');
        scrollToId('mode-card');
    });
    on($('now-next'), 'click', () => go('maps'));
    on($('mp-change'), 'click', openNextPicker);
    on($('pl-find-input'), 'input', event => {
        playerQuery = String(event.target.value || '');
        renderPlayers();
    });
    on($('mp-find'), 'input', event => {
        mapQuery = String(event.target.value || '');
        renderMaps();
    });

    const input = $('chat-input');
    on(input, 'input', updateCounter);
    on(input, 'keydown', event => {
        if (event.key === 'Enter' && !event.isComposing) {
            event.preventDefault();
            sendChat();
        }
    });
    on(input, 'focus', () => {
        clearTimeout(typingTimer);
        if (touchScreen()) document.body.classList.add('typing');
    });
    on(input, 'blur', () => {
        clearTimeout(typingTimer);
        // Late, so a tap on Send lands before the bar moves back.
        typingTimer = setTimeout(() => document.body.classList.remove('typing'), 200);
    });
    on($('chat-send'), 'mousedown', event => event.preventDefault());   // keep the keyboard up
    on($('chat-send'), 'click', () => sendChat());
    on($('chat-bang'), 'click', openCommandPicker);
    on($('chat-to'), 'click', openTargetPicker);

    on($('fog-range'), 'input', event => {
        fogDragging = true;
        updateFogLabel(Number(event.target.value));
    });
    on($('fog-range'), 'change', event => {
        fogDragging = false;
        setFog(Number(event.target.value));
    });
    on($('lc-switch'), 'click', () => {
        const lc = obj(lastState && lastState.lag_comp);
        setLagComp(!lc.enabled);
    });
    on($('dev-refresh'), 'click', loadDevices);

    on($('pair-code'), 'input', onPairInput);
    on($('pair-code'), 'focus', renderPairBoxes);
    on($('pair-code'), 'blur', renderPairBoxes);
    on($('pair-code'), 'keydown', event => {
        if (event.key === 'Enter') {
            event.preventDefault();
            pairWithCode();
        }
    });
    on($('pair-go'), 'click', () => pairWithCode());
    on($('show-token'), 'click', () => showTokenForm(true));
    on($('show-pair'), 'click', () => showTokenForm(false));
    on($('token-form'), 'submit', event => {
        event.preventDefault();
        signInWithToken();
    });
    on($('connecting-out'), 'click', () => logout());
}

function registerServiceWorker() {
    try {
        if (typeof navigator === 'undefined' || !navigator.serviceWorker) return;
        if (typeof window !== 'undefined' && window.isSecureContext === false) return;
        navigator.serviceWorker.register('/sw.js').catch(() => {});
    } catch (error) { /* not installable here (plain HTTP); the page works the same */ }
}

// ------------------------------------------------------------------- boot
function boot() {
    bindEvents();
    renderQuickReplies();
    renderHelp();
    setChatTarget(null);
    registerServiceWorker();
    const code = takePairCodeFromHash();
    if (code !== null) {
        showSignin();
        const input = $('pair-code');
        if (input) input.value = code;
        renderPairBoxes();
        if (code.length === PAIR_LENGTH) return pairWithCode(code);
        signinMessage('That pairing link is not complete. Type the code from the PC.');
        return Promise.resolve(false);
    }
    if (deviceKey) {
        showConnecting();
        connectWS();
        return Promise.resolve(true);
    }
    if (hasOldLogin()) return migrateOldLogin();
    showSignin();
    return Promise.resolve(false);
}

const booting = boot();
