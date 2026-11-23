"""
WolfRAT Web Admin: the embedded web server behind the phone page.

All state reads from the existing ServerManager instance.
All commands go through the same protocol.py path as the desktop UI.

Signing in: every phone gets its own device key, either from the username and
access token (POST /api/auth) or from a code shown on the PC (POST /api/pair).
The access token itself is only taken by /api/auth; every other route wants a
device key. Keys are kept as SHA-256 hashes; signing a device out (or a new
access token) ends its key and closes its open connections.
"""

import asyncio
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sys
import threading
import time
import logging
from collections.abc import Mapping

from aiohttp import web
from aiohttp.web_exceptions import HTTPException

from wolfrat.admin_commands import chat_limit
from wolfrat.protocol import split_chat, wire_log

# Suppress aiohttp access logs (noisy GET /api/status every 5s)
logging.getLogger('aiohttp.access').setLevel(logging.WARNING)

_SECRET_SETTING_NAMES = frozenset({
    'serverpassword',
    'sideapassword',
    'sidebpassword',
    'gamepass',
    'bluepass',
    'redpass',
})


class AmbiguousMissionTarget(ValueError):
    """A filename names more than one authoritative queue entry."""


class StaleMissionTarget(ValueError):
    """A posted mission identity no longer matches the queue snapshot."""


class StalePlayerTarget(ValueError):
    """A posted player identity no longer matches the roster snapshot."""


class BadRequestBody(ValueError):
    """The request body is not the JSON object a route expects."""


# In front of every chat line sent from the phone page, so players can see an
# admin wrote it.  Older pages add it themselves; the server takes it off and
# puts it back once.
PHONE_PREFIX = "[ADMIN] "
# How long a typed phone command may wait for the WolfRAT window to take it.
PHONE_COMMAND_TIMEOUT = 10.0

# --- Devices (one key per phone) ---
DEVICE_KEY_BYTES = 32
MAX_DEVICES = 50                 # the least recently used one goes first
DEVICE_TOUCH_SECONDS = 60        # last_seen / last_ip are written at most this often
DEVICE_NAME_MAX = 40

# --- Pairing (a code shown on the PC) ---
PAIR_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PAIR_CODE_LENGTH = 6
PAIR_CODE_SECONDS = 5 * 60

# --- Requests and connections ---
CLIENT_MAX_SIZE = 64 * 1024      # no route needs a bigger body
MAX_WS_CLIENTS = 32
WS_HEARTBEAT = 30.0
WS_SEND_TIMEOUT = 5.0
# WebSocket close code when a key stops working (signed out, new token).
WS_CLOSE_SIGNED_OUT = 4001

SECURITY_HEADERS = {
    'X-Frame-Options': 'DENY',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self' ws: wss:; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
}

# Addresses that count as "this network": loopback, private (RFC 1918,
# fc00::/7), link-local and 100.64.0.0/10 (carrier-grade NAT / VPN overlays).
_LOCAL_NETWORKS = tuple(ipaddress.ip_network(network) for network in (
    '127.0.0.0/8',
    '10.0.0.0/8',
    '172.16.0.0/12',
    '192.168.0.0/16',
    '169.254.0.0/16',
    '100.64.0.0/10',
    '::1/128',
    'fc00::/7',
    'fe80::/10',
))

NETWORK_REFUSAL = (
    "This WolfRAT only takes phones on its own network. To use it from the "
    "internet, tick 'Allow phones from the internet' on the Web Admin tab in WolfRAT."
)
_NETWORK_REFUSAL_PAGE = (
    "<!doctype html><html><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
    "<title>WolfRAT</title></head><body>"
    "<h1>WolfRAT</h1>"
    "<p>This WolfRAT only takes phones on its own network.</p>"
    "<p>To use it from the internet, tick <b>Allow phones from the internet</b> "
    "on the Web Admin tab in WolfRAT.</p>"
    "</body></html>"
)

# The page's files: fixed names only, never a directory. (file, content type,
# text served when the file is missing; None = 404).
STATIC_FILES = {
    '/': ('index.html', 'text/html',
          '<h1>WolfRAT Web UI</h1><p>Template not found.</p>'),
    '/static/app.js': ('app.js', 'application/javascript', '// app.js not found'),
    '/static/style.css': ('style.css', 'text/css', ''),
    '/manifest.webmanifest': ('manifest.webmanifest', 'application/manifest+json', None),
    '/sw.js': ('sw.js', 'application/javascript', None),
    '/static/icon-192.png': ('icon-192.png', 'image/png', None),
    '/static/icon-512.png': ('icon-512.png', 'image/png', None),
    '/static/icon-maskable-512.png': ('icon-maskable-512.png', 'image/png', None),
}

# Where a checked request keeps the id of the device whose key it carried.
_DEVICE_ID = (
    web.RequestKey('wolf_device', str) if hasattr(web, 'RequestKey') else 'wolf_device'
)

_HOST_HEADER = re.compile(r'[A-Za-z0-9.\-]{1,253}(?::\d{1,5})?|\[[0-9A-Fa-f:.]{2,45}\](?::\d{1,5})?')


def _log_text(value, limit=40):
    """Something a visitor typed, safe for one wire-log line."""
    return repr(str(value)[:limit])


def _same_text(given, expected):
    """Constant-time comparison that also copes with non-ASCII text."""
    return hmac.compare_digest(
        str(given).encode('utf-8', 'replace'),
        str(expected).encode('utf-8', 'replace'),
    )


def _key_digest(key):
    return hashlib.sha256(str(key).encode('utf-8', 'replace')).hexdigest()


def is_local_address(address):
    """True for loopback, private, link-local and 100.64.0.0/10 addresses."""
    if not address:
        return False
    text = str(address).strip()
    if text.startswith('[') and text.endswith(']'):
        text = text[1:-1]
    text = text.split('%', 1)[0]          # IPv6 zone id
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in network for network in _LOCAL_NETWORKS)


def normalize_pair_code(code):
    """What the phone typed, as the PC shows it: capitals, no spaces or dashes."""
    return re.sub(r'[\s\-]+', '', str(code or '')).upper()


def device_name_from_agent(user_agent):
    """A readable default name for a device from its browser's User-Agent."""
    agent = str(user_agent or '')
    for marker, name in (
        ('iPhone', 'iPhone'),
        ('iPad', 'iPad'),
        ('Android', 'Android phone'),
        ('Windows', 'Windows PC'),
        ('Macintosh', 'Mac'),
        ('CrOS', 'Chromebook'),
        ('Linux', 'Linux PC'),
    ):
        if marker in agent:
            return name
    return 'Phone'


def clean_device_name(name):
    """One short printable line for the device list."""
    text = ''.join(
        character for character in str(name or '')
        if character.isprintable()
    )
    text = ' '.join(text.split())[:DEVICE_NAME_MAX].strip()
    return text or 'Phone'


def _get_template_path():
    """Get path to the web templates directory."""
    if getattr(sys, 'frozen', False):
        # PyInstaller --onefile extracts to sys._MEIPASS
        meipass = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        return os.path.join(meipass, 'wolfrat', 'web_templates')
    return os.path.join(os.path.dirname(__file__), 'web_templates')


def generate_token():
    """Generate a cryptographically secure access token."""
    return secrets.token_urlsafe(32)


async def _add_security_headers(request, response):
    """Every response: no framing, no sniffing, no referrer, the page's CSP."""
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    if request.path.startswith('/api/') or request.path == '/ws':
        response.headers['Cache-Control'] = 'no-store'
    else:
        response.headers['Cache-Control'] = 'no-cache'


@dataclass(frozen=True)
class WebServerStartResult:
    """The observable outcome of binding one web-server lifecycle."""

    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class _OpenSocket:
    """One open WebSocket: whose key opened it, from where, on which loop."""

    device_id: str
    ip: str
    loop: object


class WolfWebServer:
    """Embedded web server for WolfRAT mobile access."""

    def __init__(self, server_manager, host='0.0.0.0', port=8070):
        self.sm = server_manager
        self._running = False
        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._lifecycle_id = 0
        self._stop_requested = None
        self._start_future = None
        self._auth_lock = threading.Lock()
        self._web_username = "admin"
        self._web_token = None
        self.host = host
        self.port = port
        self._ws_clients = set()
        self._ws_lock = threading.Lock()
        self._ws_info = {}               # ws -> _OpenSocket
        self._loop = None
        self._thread = None
        self._runner = None
        self._last_broadcast = 0
        self._broadcast_interval = 1.0
        self._push_pending = False

        self._template_path = _get_template_path()
        wire_log(f"WEB SERVER: template path = {self._template_path} (exists={os.path.exists(self._template_path)})")

        # Auth state - separate from JO server credentials
        self._login_ips = {}  # {ip: last_login_timestamp}
        self.on_login = None  # callback(ip, timestamp), kept for older callers
        # callback(text) -> concurrent Future of {'ok', 'command', 'error'}:
        # runs a !command typed on the phone inside WolfRAT as Web Admin.
        # Set by the main window; None = typed commands are refused.
        self.on_command = None
        # Brute-force lockout: {ip: {'guesses': set(digest), 'first': ts, 'until': ts}}
        self._failed_logins = {}
        # Phones that may use the page: {device id: record}. Records hold the
        # SHA-256 of the key, never the key.
        self._devices = {}
        self._allow_internet = False
        self._pair = None                # (code, expires_at) while a code is shown
        self._internet_address = None    # host[:port] a signed-in phone used from outside
        # callback() after the device list (or the internet address) changed.
        # Called on the web thread or the caller's; the desktop queues it to Qt.
        self.on_devices_changed = None
        # callback(device_id, name) after a pairing code was used.
        self.on_paired = None

    def _create_application(self):
        """Build one aiohttp application for one event-loop lifecycle."""

        @web.middleware
        async def network_gate(request, handler):
            if not self.allow_internet and not is_local_address(request.remote):
                return self._network_refusal(request)
            return await handler(request)

        application = web.Application(
            middlewares=[network_gate],
            client_max_size=CLIENT_MAX_SIZE,
        )
        application.on_response_prepare.append(_add_security_headers)
        router = application.router

        # Public: the page's own files, signing in and pairing.
        for path, spec in STATIC_FILES.items():
            router.add_get(path, self._static_handler(*spec))
        router.add_post('/api/auth', self._handle_auth)
        router.add_post('/api/pair', self._handle_pair)

        # Everything else needs a device key.
        protect = self._protected
        router.add_get('/api/status', protect(self._handle_status))
        router.add_get('/api/players', protect(self._handle_players))
        router.add_get('/api/chat', protect(self._handle_chat))
        router.add_get('/api/maps', protect(self._handle_maps))
        router.add_get('/api/settings', protect(self._handle_settings))
        router.add_get('/api/login-ips', protect(self._handle_login_ips))
        router.add_get('/api/devices', protect(self._handle_devices))
        router.add_post('/api/devices/signout', protect(self._handle_device_signout))
        router.add_post('/api/logout', protect(self._handle_logout))
        router.add_post('/api/action', protect(self._handle_quick_action))
        # No raw-command route (/api/command): the page never used it.
        router.add_post('/api/chat/send', protect(self._handle_send_chat))
        router.add_post('/api/player/action', protect(self._handle_player_action))
        router.add_post('/api/map/switch', protect(self._handle_map_switch))
        router.add_get('/ws', protect(self._handle_websocket, query_key=True))
        return application

    @property
    def is_running(self):
        with self._state_lock:
            return self._running

    @property
    def application(self):
        """Return the aiohttp application for an external HTTP host."""
        return self._create_application()

    # --- Settings from the desktop ---

    @property
    def allow_internet(self):
        """False = only phones on this PC's own network get in."""
        with self._auth_lock:
            return self._allow_internet

    @allow_internet.setter
    def allow_internet(self, value):
        value = bool(value)
        with self._auth_lock:
            changed = value != self._allow_internet
            self._allow_internet = value
        if changed and not value:
            self._close_sockets(
                lambda info: not is_local_address(info.ip),
                "internet access turned off",
            )

    @property
    def internet_address(self):
        """host[:port] a signed-in phone last used from the internet, or None."""
        with self._auth_lock:
            return self._internet_address

    def load_internet_address(self, address):
        with self._auth_lock:
            text = str(address or '').strip()
            self._internet_address = text if _HOST_HEADER.fullmatch(text) else None

    def set_auth(self, username, token):
        """Set the web admin credentials (called from WebAdminTab).

        A new token (Regenerate Token) forgets every device and closes every
        open connection: the page has to sign in again.
        """
        with self._auth_lock:
            old_token = self._web_token
            self._web_username = username or "admin"
            self._web_token = token
            token_changed = bool(old_token) and old_token != token
            if token_changed:
                had_devices = bool(self._devices)
                self._devices = {}
                self._pair = None
        if token_changed:
            self._close_connections("token changed")
            if had_devices:
                self._devices_changed()

    def _current_token(self):
        with self._auth_lock:
            return self._web_token

    # --- Devices ---

    def create_device(self, name='', ip=None, now=None):
        """Give one phone its own key. Returns (key, device id); the key is
        shown to that phone once and only its SHA-256 is kept."""
        key = secrets.token_urlsafe(DEVICE_KEY_BYTES)
        stamp = int(time.time() if now is None else now)
        with self._auth_lock:
            device_id = secrets.token_hex(4)
            while device_id in self._devices:
                device_id = secrets.token_hex(4)
            self._devices[device_id] = {
                'id': device_id,
                'name': clean_device_name(name),
                'key_sha256': _key_digest(key),
                'created': stamp,
                'last_seen': stamp,
                'last_ip': ip or None,
            }
            dropped = []
            while len(self._devices) > MAX_DEVICES:
                oldest = min(
                    (record for record in self._devices.values()
                     if record['id'] != device_id),
                    key=lambda record: record.get('last_seen') or 0,
                )
                dropped.append(self._devices.pop(oldest['id'])['id'])
        if dropped:
            self._close_sockets(
                lambda info: info.device_id in dropped, "signed out"
            )
        self._devices_changed()
        return key, device_id

    def load_devices(self, records):
        """Devices saved in wolfrat_web.json (records without a usable hash are skipped)."""
        loaded = {}
        for record in records or ():
            if not isinstance(record, Mapping):
                continue
            device_id = str(record.get('id') or '')
            digest = str(record.get('key_sha256') or '').lower()
            if not re.fullmatch(r'[0-9a-f]{8}', device_id):
                continue
            if not re.fullmatch(r'[0-9a-f]{64}', digest):
                continue
            loaded[device_id] = {
                'id': device_id,
                'name': clean_device_name(record.get('name')),
                'key_sha256': digest,
                'created': self._int_or_none(record.get('created')),
                'last_seen': self._int_or_none(record.get('last_seen')),
                'last_ip': (str(record.get('last_ip'))[:64]
                            if record.get('last_ip') else None),
            }
        with self._auth_lock:
            self._devices = loaded

    @staticmethod
    def _int_or_none(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def devices_for_config(self):
        """Every device record as saved in wolfrat_web.json (hashes, no keys)."""
        with self._auth_lock:
            return [dict(record) for record in self._devices.values()]

    def list_devices(self, this_device=None):
        """Devices for the desktop list and /api/devices, most recently used first."""
        with self._auth_lock:
            records = [dict(record) for record in self._devices.values()]
        records.sort(key=lambda record: record.get('last_seen') or 0, reverse=True)
        return [
            {
                'id': record['id'],
                'name': record['name'],
                'created': record['created'],
                'last_seen': record['last_seen'],
                'last_ip': record['last_ip'],
                'this_device': record['id'] == this_device,
            }
            for record in records
        ]

    def sign_out_device(self, device_id, reason="signed out"):
        """End one device's key and close its connections. True if it existed."""
        with self._auth_lock:
            record = self._devices.pop(str(device_id or ''), None)
        if record is None:
            return False
        wire_log(f"WEB: device {record['id']} ({_log_text(record['name'])}) {reason}")
        self._close_sockets(lambda info: info.device_id == record['id'], reason)
        self._devices_changed()
        return True

    def _device_known(self, device_id):
        with self._auth_lock:
            return device_id in self._devices

    def _device_for_key(self, key):
        """The device record this key belongs to, or None."""
        if not key:
            return None
        digest = _key_digest(key)
        with self._auth_lock:
            if not self._web_token:
                return None          # Web Admin never set up: nobody gets in
            found = None
            for record in self._devices.values():
                if hmac.compare_digest(record['key_sha256'], digest):
                    found = dict(record)
            return found

    def _touch_device(self, device_id, ip, now=None):
        """Note a device's use; written at most once a minute."""
        now = time.time() if now is None else now
        with self._auth_lock:
            record = self._devices.get(device_id)
            if record is None:
                return
            if now - (record.get('last_seen') or 0) < DEVICE_TOUCH_SECONDS:
                return
            record['last_seen'] = int(now)
            record['last_ip'] = ip or record.get('last_ip')
        self._devices_changed()

    def _devices_changed(self):
        callback = self.on_devices_changed
        if callback is None:
            return
        try:
            callback()
        except Exception as error:
            wire_log(f"WEB: saving the device list failed: {error}")

    def _note_internet_address(self, request):
        """Remember the address a signed-in phone reached WolfRAT on from outside,
        so the desktop can offer it when pairing the next phone."""
        if is_local_address(request.remote):
            return
        host = str(request.headers.get('Host', '') or '').strip()
        if not _HOST_HEADER.fullmatch(host):
            return
        with self._auth_lock:
            if self._internet_address == host:
                return
            self._internet_address = host
        self._devices_changed()

    # --- Pairing ---

    def start_pairing(self, now=None):
        """A new one-use code for the desktop to show. Returns (code, expires_at)."""
        code = ''.join(secrets.choice(PAIR_ALPHABET) for _ in range(PAIR_CODE_LENGTH))
        expires_at = (time.time() if now is None else now) + PAIR_CODE_SECONDS
        with self._auth_lock:
            self._pair = (code, expires_at)
        wire_log("WEB PAIR: a pairing code is shown on the PC (5 minutes, one use)")
        return code, expires_at

    def cancel_pairing(self):
        with self._auth_lock:
            self._pair = None

    def pairing_code(self, now=None):
        """(code, expires_at) while a code can still be used, else None."""
        now = time.time() if now is None else now
        with self._auth_lock:
            if self._pair is not None and self._pair[1] <= now:
                self._pair = None
            return self._pair

    def _take_pair_code(self, code, now=None):
        """True (and the code is used up) when code is the one shown on the PC."""
        now = time.time() if now is None else now
        with self._auth_lock:
            if self._pair is None:
                return False
            expected, expires_at = self._pair
            if expires_at <= now:
                self._pair = None
                return False
            if not _same_text(code, expected):
                return False
            self._pair = None
            return True

    # --- Open connections ---

    def _add_socket(self, ws, device_id, ip, loop):
        with self._ws_lock:
            self._ws_info[ws] = _OpenSocket(device_id, ip or '', loop)
            self._ws_clients.add(ws)

    def _remove_socket(self, ws):
        with self._ws_lock:
            self._ws_info.pop(ws, None)
            self._ws_clients.discard(ws)

    def _open_sockets(self):
        with self._ws_lock:
            return list(self._ws_info.items())

    def _close_sockets(self, select, reason):
        """Close every open WebSocket that select(info) picks, from any thread."""
        targets = [ws_info for ws_info in self._open_sockets() if select(ws_info[1])]
        if not targets:
            return 0
        message = reason.encode('ascii', 'replace')[:120]
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        waiting = []
        for ws, info in targets:
            loop = info.loop
            if loop is None or loop.is_closed():
                continue
            closing = self._close_socket(ws, message)
            if loop is running:
                loop.create_task(closing)
                continue
            try:
                waiting.append(asyncio.run_coroutine_threadsafe(closing, loop))
            except RuntimeError:
                closing.close()
        for future in waiting:
            try:
                future.result(timeout=3)
            except Exception:
                pass
        wire_log(f"WEB: {reason} - {len(targets)} open connection(s) closed")
        return len(targets)

    @staticmethod
    async def _close_socket(ws, message):
        try:
            await ws.close(code=WS_CLOSE_SIGNED_OUT, message=message)
        except Exception:
            pass

    def _close_connections(self, reason):
        """Close every open WebSocket; the page has to sign in again."""
        return self._close_sockets(lambda _info: True, reason)

    # --- Lifecycle ---

    def start(self):
        """Start the web server in a background thread."""
        with self._lifecycle_lock:
            with self._state_lock:
                if self._thread is not None and self._thread.is_alive():
                    return self._start_future

                self._lifecycle_id += 1
                lifecycle_id = self._lifecycle_id
                stop_requested = threading.Event()
                start_future = Future()
                host = self.host
                port = self.port
                thread = threading.Thread(
                    target=self._run,
                    args=(
                        lifecycle_id,
                        stop_requested,
                        start_future,
                        host,
                        port,
                    ),
                    daemon=True,
                    name="WolfWeb",
                )
                self._running = False
                self._loop = None
                self._runner = None
                self._stop_requested = stop_requested
                self._start_future = start_future
                self._thread = thread
            thread.start()
            return start_future

    def stop(self):
        """Stop the web server completely. Port is released, thread exits."""
        with self._lifecycle_lock:
            with self._state_lock:
                thread = self._thread
                if thread is None:
                    self._running = False
                    return
                if thread is threading.current_thread():
                    raise RuntimeError("WolfWeb cannot join its own worker thread")
                was_running = self._running
                self._running = False
                loop = self._loop
                stop_requested = self._stop_requested
                start_future = self._start_future
                if stop_requested is not None:
                    stop_requested.set()
            if start_future is not None and not start_future.done():
                self._resolve_start(
                    start_future,
                    WebServerStartResult(
                        ok=False,
                        error="Web server startup was stopped",
                    ),
                )

            if (
                was_running
                and loop
                and not loop.is_closed()
                and loop.is_running()
            ):
                async def _shutdown():
                    for ws, _info in self._open_sockets():
                        try:
                            await ws.close()
                        except Exception:
                            pass
                    with self._ws_lock:
                        self._ws_clients.clear()
                        self._ws_info.clear()

                shutdown = _shutdown()
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        shutdown,
                        loop,
                    )
                except RuntimeError:
                    shutdown.close()
                else:
                    try:
                        future.result(timeout=3)
                    except Exception:
                        pass
                    try:
                        loop.call_soon_threadsafe(loop.stop)
                    except RuntimeError:
                        pass

            thread.join(timeout=3)
            if thread.is_alive():
                raise RuntimeError("WolfWeb worker did not stop")

            with self._state_lock:
                if self._thread is thread:
                    self._loop = None
                    self._runner = None
                    self._stop_requested = None
                    self._thread = None
            wire_log("WEB SERVER: stopped, port released")

    def restart(self):
        """Restart the web server (e.g. after port change)."""
        with self._lifecycle_lock:
            self.stop()
            return self.start()

    @staticmethod
    def _resolve_start(future, result):
        try:
            future.set_result(result)
        except InvalidStateError:
            pass

    def _run(
        self,
        lifecycle_id,
        stop_requested,
        start_future,
        host,
        port,
    ):
        """Run the aiohttp event loop in a background thread."""
        loop = asyncio.new_event_loop()
        with self._state_lock:
            if lifecycle_id != self._lifecycle_id:
                loop.close()
                return
            self._loop = loop
        asyncio.set_event_loop(loop)
        runner = None
        try:
            if stop_requested.is_set():
                return
            runner = web.AppRunner(self._create_application())
            with self._state_lock:
                if lifecycle_id != self._lifecycle_id:
                    return
                self._runner = runner
            loop.run_until_complete(runner.setup())
            if stop_requested.is_set():
                return
            site = web.TCPSite(runner, host, port)
            loop.run_until_complete(site.start())
            if stop_requested.is_set():
                return
            with self._state_lock:
                if (
                    lifecycle_id != self._lifecycle_id
                    or stop_requested.is_set()
                ):
                    return
                self._running = True
            wire_log(f"WEB SERVER: listening on {host}:{port}")
            self._resolve_start(
                start_future,
                WebServerStartResult(ok=True),
            )
            if not stop_requested.is_set():
                loop.run_forever()
        except OSError as e:
            error = f"Could not listen on {host}:{port}: {e}"
            wire_log(f"WEB SERVER ERROR: {error}")
            self._resolve_start(
                start_future,
                WebServerStartResult(ok=False, error=error),
            )
        except Exception as e:
            error = f"Web server startup failed: {e}"
            wire_log(f"WEB SERVER ERROR: {error}")
            self._resolve_start(
                start_future,
                WebServerStartResult(ok=False, error=error),
            )
        finally:
            if not start_future.done():
                self._resolve_start(
                    start_future,
                    WebServerStartResult(
                        ok=False,
                        error=(
                            "Web server startup was stopped"
                            if stop_requested.is_set()
                            else "Web server exited before becoming ready"
                        ),
                    ),
                )
            try:
                if runner is not None:
                    loop.run_until_complete(runner.cleanup())
            except Exception as error:
                wire_log(f"WEB SERVER CLEANUP ERROR: {error}")
            finally:
                with self._state_lock:
                    if lifecycle_id == self._lifecycle_id:
                        if self._runner is runner:
                            self._runner = None
                        if self._loop is loop:
                            self._loop = None
                        if self._thread is threading.current_thread():
                            self._thread = None
                        if self._stop_requested is stop_requested:
                            self._stop_requested = None
                        self._running = False
                with self._ws_lock:
                    for ws in [ws for ws, info in self._ws_info.items() if info.loop is loop]:
                        self._ws_info.pop(ws, None)
                        self._ws_clients.discard(ws)
                loop.close()

    def get_login_ips(self):
        """Return list of unique IPs with their last login time."""
        with self._auth_lock:
            return [{'ip': ip, 'last_login': ts} for ip, ts in sorted(
                self._login_ips.items(), key=lambda x: x[1], reverse=True)]

    def get_login_ips_dict(self):
        """Return raw dict of {ip: timestamp} for persistence."""
        with self._auth_lock:
            return dict(self._login_ips)

    def load_login_ips(self, ips_dict):
        """Load persisted login IPs from config."""
        with self._auth_lock:
            self._login_ips = {ip: float(ts) for ip, ts in ips_dict.items()}

    # --- Brute-force lockout ---
    # An address that presents LOCKOUT_MAX_FAILURES different wrong credentials
    # within LOCKOUT_WINDOW is locked for LOCKOUT_SECONDS: while locked, every
    # wrong credential gets 429 + Retry-After. A right one (token or device key)
    # always gets in, so nobody sharing the address can lock the admin out.
    # Distinct guesses are counted (by digest, never stored in clear) so a phone
    # re-sending one stale key cannot lock its owner out, while a guesser - who
    # never repeats a guess - is stopped.
    LOCKOUT_MAX_FAILURES = 5
    LOCKOUT_WINDOW = 15 * 60
    LOCKOUT_SECONDS = 15 * 60
    _LOCKOUT_MAX_TRACKED = 2048

    def _lockout_remaining(self, ip, now=None):
        """Seconds this address is still locked out for (0 = not locked)."""
        now = time.time() if now is None else now
        with self._auth_lock:
            record = self._failed_logins.get(ip)
            if not record:
                return 0
            return max(0, int(record['until'] - now + 0.999))

    def _record_failed_login(self, ip, guess, now=None):
        """Count one wrong credential. Returns True if this locked the address."""
        now = time.time() if now is None else now
        digest = hashlib.sha256(guess.encode('utf-8', 'replace')).digest()
        locked = False
        with self._auth_lock:
            if len(self._failed_logins) >= self._LOCKOUT_MAX_TRACKED:
                for stale in [
                    key for key, rec in self._failed_logins.items()
                    if rec['until'] <= now
                    and now - rec['first'] > self.LOCKOUT_WINDOW
                ]:
                    del self._failed_logins[stale]
            record = self._failed_logins.get(ip)
            if record is None or (
                record['until'] <= now
                and now - record['first'] > self.LOCKOUT_WINDOW
            ):
                if (record is None
                        and len(self._failed_logins) >= self._LOCKOUT_MAX_TRACKED):
                    return False
                record = {'guesses': set(), 'first': now, 'until': 0}
                self._failed_logins[ip] = record
            record['guesses'].add(digest)
            if (len(record['guesses']) >= self.LOCKOUT_MAX_FAILURES
                    and record['until'] <= now):
                record['until'] = now + self.LOCKOUT_SECONDS
                record['guesses'] = set()
                record['first'] = now
                locked = True
        if locked:
            wire_log(
                f"WEB AUTH LOCKOUT: {ip} refused for "
                f"{self.LOCKOUT_SECONDS // 60} minutes after "
                f"{self.LOCKOUT_MAX_FAILURES} failed logins"
            )
        return locked

    def _clear_failed_logins(self, ip):
        with self._auth_lock:
            self._failed_logins.pop(ip, None)

    # --- Checking a request's key ---

    @staticmethod
    def _presented_key(request, query_key=False):
        header = request.headers.get('Authorization', '')
        if header[:7].lower() == 'bearer ':
            key = header[7:].strip()
            if key:
                return key
        if query_key:
            return str(request.query.get('key', '') or '').strip()
        return ''

    def _authenticate(self, request, query_key=False):
        """(device record, None) for a valid key, else (None, the refusal)."""
        ip = request.remote or 'unknown'
        presented = self._presented_key(request, query_key)
        if not presented:
            return None, self._refusal(request, 401, 'Unauthorized')
        device = self._device_for_key(presented)
        if device is not None:
            self._touch_device(device['id'], ip)
            self._note_internet_address(request)
            return device, None
        remaining = self._lockout_remaining(ip)
        if remaining:
            return None, self._locked_out(request, remaining)
        if self._record_failed_login(ip, presented):
            return None, self._locked_out(request, self.LOCKOUT_SECONDS)
        return None, self._refusal(request, 401, 'Unauthorized')

    def _check_auth(self, request):
        """True if the request carries a valid device key."""
        return self._authenticate(request, query_key=request.path == '/ws')[1] is None

    def _protected(self, handler, query_key=False):
        """Route handler that runs only for a valid device key."""
        async def protected(request):
            device, refusal = self._authenticate(request, query_key)
            if refusal is not None:
                return refusal
            request[_DEVICE_ID] = device['id']
            return await handler(request)
        protected.__name__ = getattr(handler, '__name__', 'protected')
        protected.__doc__ = getattr(handler, '__doc__', None)
        return protected

    @staticmethod
    def _refusal(request, status, error):
        if request.path.startswith('/api/'):
            return web.json_response({'error': error}, status=status)
        return web.Response(status=status, text=error)

    @staticmethod
    def _lockout_text(remaining):
        minutes = max(1, (remaining + 59) // 60)
        return f'Too many failed logins. Try again in {minutes} min.'

    def _locked_out(self, request, remaining):
        headers = {'Retry-After': str(remaining)}
        if request.path.startswith('/api/'):
            return web.json_response(
                {'error': self._lockout_text(remaining), 'retry_after': remaining},
                status=429,
                headers=headers,
            )
        return web.Response(status=429, text=self._lockout_text(remaining), headers=headers)

    @classmethod
    def _locked_out_response(cls, remaining):
        return web.json_response(
            {'error': cls._lockout_text(remaining), 'retry_after': remaining},
            status=429,
            headers={'Retry-After': str(remaining)},
        )

    @staticmethod
    def _network_refusal(request):
        if request.path.startswith('/api/'):
            return web.json_response({'error': NETWORK_REFUSAL}, status=403)
        return web.Response(status=403, text=_NETWORK_REFUSAL_PAGE, content_type='text/html')

    # --- Pushing state to open pages ---

    def broadcast_state(self):
        """Push fresh state to every open page (any thread).

        At most one push a second; a push asked for inside that second goes
        out at its end, so the last change is never dropped.
        """
        loop = self._loop
        if not self.is_running or loop is None or not self._ws_clients:
            return
        try:
            loop.call_soon_threadsafe(self._schedule_state_push)
        except RuntimeError:
            pass

    def _schedule_state_push(self):
        """Web thread: one push now, or one at the end of this second."""
        if self._push_pending:
            return
        loop = asyncio.get_running_loop()
        wait = self._broadcast_interval - (time.time() - self._last_broadcast)
        self._push_pending = True
        loop.call_later(max(0.0, wait), self._push_state_now)

    def _push_state_now(self):
        self._push_pending = False
        self._last_broadcast = time.time()
        for _ws, info in self._open_sockets():
            self._touch_device(info.device_id, info.ip)
        try:
            message = json.dumps(self._build_state())
        except Exception as error:
            wire_log(f"WEB: building the page state failed: {error}")
            return
        asyncio.get_running_loop().create_task(self._broadcast(message))

    def broadcast_chat(self):
        """Push chat update immediately (no throttle). Called on new chat messages."""
        loop = self._loop
        if not self.is_running or loop is None or not self._ws_clients:
            return
        try:
            message = json.dumps({'type': 'chat', 'chat': self._chat_rows(50)})
        except Exception as error:
            wire_log(f"WEB: building the chat push failed: {error}")
            return
        coroutine = self._broadcast(message)
        try:
            asyncio.run_coroutine_threadsafe(coroutine, loop)
        except RuntimeError:
            coroutine.close()

    async def _broadcast(self, message):
        """Send a message to all connected WebSocket clients."""
        for ws, _info in self._open_sockets():
            try:
                await asyncio.wait_for(ws.send_str(message), timeout=WS_SEND_TIMEOUT)
            except Exception:
                self._remove_socket(ws)

    def _chat_rows(self, count):
        return self._json_safe((self.sm.chat_messages or [])[-count:])

    def _resolve_missions(self):
        """Render identity-bearing rows from the authoritative queue snapshot."""
        missions = tuple(self.sm.mission_entries)
        avail_raw = self.sm.available_maps_data or ''
        name_map = {}
        for line in avail_raw.split('\n'):
            line = line.strip()
            if not line:
                continue
            m = re.match(r'^\d+[.:]\s*', line)
            if m:
                line = line[m.end():]
            ext_match = re.search(r'(?i)\.(bms|npj|npz)\b', line)
            if ext_match:
                filename = line[:ext_match.end()].strip()
                desc = line[ext_match.end():].strip()
                if desc.startswith('-'):
                    desc = desc[1:].strip()
                if desc.startswith('('):
                    desc = desc[1:].strip()
                if desc.endswith(')'):
                    desc = desc[:-1].strip()
                name_map[filename.upper()] = desc if desc else filename
        return [
            {
                'queue_index': mission.queue_index,
                'filename': mission.filename,
                'display_name': (
                    name_map.get(mission.filename.upper())
                    or re.sub(
                        r'(?i)\.(?:bms|npj|npz)$', '', mission.filename
                    )
                ),
                'is_current': mission.is_current,
                'is_next': mission.is_next,
                'double_time': mission.double_time,
                'revision': mission.revision,
            }
            for mission in missions
        ]

    def _resolve_players(self):
        """Render players with the identity required for safe mutations."""
        legacy_by_id = {}
        for raw in self.sm.players or []:
            if not isinstance(raw, Mapping):
                continue
            try:
                legacy_by_id[int(raw.get('id'))] = dict(raw)
            except (TypeError, ValueError):
                continue

        players = []
        for player in self.sm.player_entries:
            if player.server_id == 0:
                continue
            rendered = legacy_by_id.get(player.server_id, {})
            rendered.update({
                'id': player.server_id,
                'name': player.name,
                'team': str(player.team),
                'team_name': {
                    1: 'Joint Ops',
                    2: 'Rebels',
                }.get(player.team, 'Unknown'),
                'class': player.player_class,
                'kills': str(
                    player.kills if player.kills is not None else 0
                ),
                'score': str(
                    player.kills if player.kills is not None else 0
                ),
                'deaths': str(
                    player.deaths if player.deaths is not None else '-'
                ),
                'ping': str(
                    player.ping if player.ping is not None else '-'
                ),
                'revision': player.revision,
            })
            players.append(rendered)
        return players

    def _build_state(self):
        """Build a JSON-safe state snapshot from ServerManager."""
        players = self._json_safe(self._resolve_players())
        chat = self._chat_rows(50)
        game_state = self._json_safe(self.sm.game_state or {})
        return {
            'type': 'state',
            'timestamp': time.time(),
            'connected': self.sm.is_connected,
            'players': players,
            'chat': chat,
            'game_state': game_state,
            'missions': self._resolve_missions(),
            'settings': self._safe_settings(),
            'player_count': len(players),
            'game_mode': self.sm.game_state.get('mode', 'Unknown') if self.sm.game_state else 'Unknown',
            # Longest phone chat line: the server's limit less "[ADMIN] ".
            'chat_max': max(1, chat_limit() - len(PHONE_PREFIX)),
        }

    @classmethod
    def _json_safe(cls, value):
        if isinstance(value, Enum):
            return value.value
        if is_dataclass(value) and not isinstance(value, type):
            return {
                field.name: cls._json_safe(getattr(value, field.name))
                for field in fields(value)
            }
        if isinstance(value, Mapping):
            return {
                str(key): cls._json_safe(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        return value

    def _safe_settings(self):
        settings = self.sm.game_settings or {}
        if hasattr(settings, 'items'):
            settings = dict(settings.items())
        else:
            settings = dict(settings)
        return self._json_safe({
            key: value
            for key, value in settings.items()
            if str(key).casefold() not in _SECRET_SETTING_NAMES
        })

    @staticmethod
    def _redact_command(command):
        tokens = command.split(maxsplit=2)
        if (
            len(tokens) == 3
            and tokens[0].casefold() == 'set'
            and tokens[1].casefold() in _SECRET_SETTING_NAMES
        ):
            return f'{tokens[0]} {tokens[1]} <redacted>'
        return command

    def _find_mission_entry(
        self, filename=None, *, queue_index=None, revision=None
    ):
        """Resolve the mission the admin tapped against the current queue.

        ``revision`` is accepted and ignored. The session bumps it on every
        command, so the queue gets a new one each 5 s poll and an exact match
        rejected nearly every tap made from a phone. Filename is the identity;
        the queue index only picks between duplicates of the same file.
        """
        missions = tuple(self.sm.mission_entries)
        target_name = str(filename or '').strip()
        if not target_name:
            raise ValueError("mission identity requires a filename")
        name_matches = [
            mission
            for mission in missions
            if mission.filename.casefold() == target_name.casefold()
        ]

        target_index = None
        if queue_index is not None and str(queue_index).strip() != '':
            try:
                target_index = int(queue_index)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "mission queue index must be an integer"
                ) from error
            if target_index < 0:
                raise ValueError("mission queue index cannot be negative")

        if target_index is not None:
            for mission in name_matches:
                if mission.queue_index == target_index:
                    return mission
        if len(name_matches) == 1:
            # The queue shifted under the phone; the file is still unambiguous.
            return name_matches[0]
        if not name_matches:
            raise StaleMissionTarget(
                f"stale mission identity: {target_name!r} is no longer "
                "in the queue"
            )
        raise AmbiguousMissionTarget(
            f"mission filename {target_name!r} is ambiguous; "
            "refresh and pick the queue entry again"
        )

    def _find_player_entry(self, player_id, name, revision=None):
        """Resolve the player the admin tapped against the current roster.

        Id + name is the guard against a reused slot. ``revision`` is accepted
        and ignored: it changes on every 5 s poll whether or not the player
        did, so matching it exactly failed most taps from the web panel.
        """
        target_name = str(name or '').strip()
        if not target_name:
            raise ValueError("player identity requires id and name")
        try:
            target_id = int(player_id)
        except (TypeError, ValueError) as error:
            raise ValueError("player id must be an integer") from error

        player = next(
            (
                item
                for item in self.sm.player_entries
                if item.server_id == target_id
            ),
            None,
        )
        if player is None:
            raise StalePlayerTarget(
                f"stale player identity: id {target_id} is no longer present"
            )
        if player.name != target_name:
            raise StalePlayerTarget(
                f"stale player identity: id {target_id} is now "
                f"{player.name!r}, not {target_name!r}"
            )
        return player

    @staticmethod
    def _result_payload(result, **context):
        replies = list(getattr(result, 'replies', ()) or ())
        accepted = bool(getattr(result, 'accepted', False))
        has_verification = hasattr(result, 'verified')
        verified = result.verified if has_verification else None
        payload = {
            'ok': (
                accepted and verified is True
                if has_verification
                else accepted
            ),
            'accepted': accepted,
            'replies': replies,
            **context,
        }
        if has_verification:
            payload['verified'] = verified
            payload['verification_error'] = result.verification_error
        if not accepted:
            payload['error'] = replies[-1] if replies else 'Retail command rejected'
        return payload

    async def _await_result(self, future):
        return await asyncio.wrap_future(future)

    @staticmethod
    async def _json_object(request):
        """The request body as a JSON object, or BadRequestBody (HTTP 400)."""
        try:
            body = await request.json()
        except ValueError as error:      # JSONDecodeError, bad UTF-8
            raise BadRequestBody('Request body must be valid JSON') from error
        if not isinstance(body, Mapping):
            raise BadRequestBody('Request body must be a JSON object')
        return body

    async def _phone_message(self, message):
        """One message typed on the phone page: a !command or a chat line.

        A !command runs inside WolfRAT as Web Admin (``on_command``); it is
        not posted to game chat.  Chat goes out as one "[ADMIN] " line, or is
        refused when it would not fit one.
        """
        if not isinstance(message, str):
            raise ValueError('Message must be text')
        text = " ".join(message.split())
        tag = PHONE_PREFIX.strip()
        if text == tag or text.startswith(tag + " "):   # older pages add it themselves
            text = text[len(tag):].strip()
        if not text:
            raise ValueError('No message provided')
        if text.startswith('!'):
            return await self._run_phone_command(text)
        line = PHONE_PREFIX + text
        if len(split_chat(line)) != 1:
            room = chat_limit() - len(PHONE_PREFIX)
            raise ValueError(
                f'Too long for one chat line ({room} characters at most)'
            )
        result = await self._await_result(self.sm.send_chat(line))
        return self._result_payload(result)

    async def _run_phone_command(self, text):
        command = text.split(None, 1)[0].lower()
        refused = {'ok': False, 'accepted': False, 'replies': [], 'command': command}
        runner = self.on_command
        if runner is None:
            return {**refused, 'error': 'Typed commands need the WolfRAT window'}
        wire_log(f"WEB COMMAND: {text[:120]}")
        try:
            outcome = await asyncio.wait_for(
                asyncio.wrap_future(runner(text)), timeout=PHONE_COMMAND_TIMEOUT
            )
        except asyncio.TimeoutError:
            return {**refused, 'error': 'WolfRAT is busy and has not answered (it may still run it)'}
        outcome = dict(outcome or {})
        command = outcome.get('command') or command
        if not outcome.get('ok'):
            return {**refused, 'command': command,
                    'error': outcome.get('error') or 'Not run'}
        return {'ok': True, 'accepted': True, 'replies': ['Run by WolfRAT'],
                'command': command}

    # --- Signing in ---

    def _device_name(self, request, given):
        if isinstance(given, str) and given.strip():
            return clean_device_name(given)
        return device_name_from_agent(request.headers.get('User-Agent', ''))

    async def _handle_auth(self, request):
        """POST /api/auth - username + access token in, a device key out."""
        try:
            body = await self._json_object(request)
            username = body.get('username', '')
            token = body.get('token', '')
            if not isinstance(username, str) or not isinstance(token, str):
                return web.json_response({'error': 'Missing username or token'}, status=400)
            username = username.strip()
            token = token.strip()
            if not username or not token:
                return web.json_response({'error': 'Missing username or token'}, status=400)

            with self._auth_lock:
                valid_user = self._web_username
                valid_token = self._web_token

            if not valid_token:
                return web.json_response({'error': 'Web admin is not enabled'}, status=403)

            ip = request.remote or 'unknown'
            if _same_text(username, valid_user) and _same_text(token, valid_token):
                # The right token always gets in, even while this address is
                # locked; it does not lift the lock for anyone else.
                if not self._lockout_remaining(ip):
                    self._clear_failed_logins(ip)
                key, device_id = self.create_device(
                    self._device_name(request, body.get('device_name')), ip
                )
                with self._auth_lock:
                    self._login_ips[ip] = time.time()
                wire_log(
                    f"WEB AUTH: {_log_text(username)} signed in from {ip} "
                    f"(device {device_id})"
                )
                if self.on_login:
                    try:
                        self.on_login(ip, time.time())
                    except Exception:
                        pass
                return web.json_response(
                    {'ok': True, 'key': key, 'device_id': device_id}
                )

            remaining = self._lockout_remaining(ip)
            if remaining:
                return self._locked_out_response(remaining)
            wire_log(f"WEB AUTH FAILED: {_log_text(username)} from {ip}")
            if self._record_failed_login(ip, username + chr(0) + token):
                return self._locked_out_response(self.LOCKOUT_SECONDS)
            return web.json_response({'error': 'Invalid username or token'}, status=401)
        except BadRequestBody as error:
            return web.json_response({'error': str(error)}, status=400)
        except HTTPException:
            raise
        except Exception as error:
            wire_log(f"WEB AUTH ERROR: {error}")
            return web.json_response({'error': 'Sign-in failed'}, status=500)

    async def _handle_pair(self, request):
        """POST /api/pair - the code shown on the PC in, a device key out."""
        try:
            body = await self._json_object(request)
            code = body.get('code', '')
            if not isinstance(code, str) or not normalize_pair_code(code):
                return web.json_response(
                    {'error': 'Type the code shown on the PC'}, status=400
                )
            code = normalize_pair_code(code)
            ip = request.remote or 'unknown'
            if self._take_pair_code(code):
                name = self._device_name(request, body.get('device_name'))
                key, device_id = self.create_device(name, ip)
                wire_log(f"WEB PAIR: device {device_id} paired from {ip}")
                callback = self.on_paired
                if callback is not None:
                    try:
                        callback(device_id, clean_device_name(name))
                    except Exception:
                        pass
                return web.json_response(
                    {'ok': True, 'key': key, 'device_id': device_id}
                )
            remaining = self._lockout_remaining(ip)
            if remaining:
                return self._locked_out_response(remaining)
            if (len(code) != PAIR_CODE_LENGTH
                    or any(character not in PAIR_ALPHABET for character in code)):
                return web.json_response(
                    {'error': f'The code is {PAIR_CODE_LENGTH} letters and numbers '
                              'from the PC screen'},
                    status=400,
                )
            wire_log(f"WEB PAIR FAILED: wrong or expired code from {ip}")
            if self._record_failed_login(ip, 'pair' + chr(0) + code):
                return self._locked_out_response(self.LOCKOUT_SECONDS)
            return web.json_response(
                {'error': 'Wrong or expired code. Press Pair a phone on the PC '
                          'for a new one.'},
                status=401,
            )
        except BadRequestBody as error:
            return web.json_response({'error': str(error)}, status=400)
        except HTTPException:
            raise
        except Exception as error:
            wire_log(f"WEB PAIR ERROR: {error}")
            return web.json_response({'error': 'Pairing failed'}, status=500)

    async def _handle_logout(self, request):
        """POST /api/logout - this device's key stops working."""
        self.sign_out_device(request.get(_DEVICE_ID), reason="signed out")
        return web.json_response({'ok': True})

    async def _handle_devices(self, request):
        """GET /api/devices - every signed-in device."""
        return web.json_response(
            {'devices': self.list_devices(this_device=request.get(_DEVICE_ID))}
        )

    async def _handle_device_signout(self, request):
        """POST /api/devices/signout {id} - end that device's key."""
        try:
            body = await self._json_object(request)
        except BadRequestBody as error:
            return web.json_response({'ok': False, 'error': str(error)}, status=400)
        device_id = body.get('id', '')
        if not isinstance(device_id, str) or not device_id.strip():
            return web.json_response({'ok': False, 'error': 'No device id'}, status=400)
        if not self.sign_out_device(device_id.strip(), reason="signed out from a phone"):
            return web.json_response({'ok': False, 'error': 'No such device'}, status=404)
        return web.json_response({'ok': True})

    # --- HTTP Handlers ---

    def _static_handler(self, filename, content_type, fallback):
        """Serve one fixed file from the templates folder."""
        async def serve(request):
            path = os.path.join(self._template_path, filename)
            try:
                with open(path, 'rb') as handle:
                    data = handle.read()
            except OSError:
                if fallback is None:
                    return web.Response(status=404, text='Not found')
                data = fallback.encode('utf-8')
            charset = None if content_type.startswith('image/') else 'utf-8'
            return web.Response(body=data, content_type=content_type, charset=charset)
        serve.__name__ = f"serve_{filename}"
        return serve

    async def _handle_status(self, request):
        """GET /api/status — Server status overview."""
        return web.json_response(self._build_state())

    async def _handle_players(self, request):
        """GET /api/players — Current player list."""
        players = self._json_safe(self._resolve_players())
        return web.json_response({
            'players': players,
            'count': len(players),
        })

    async def _handle_chat(self, request):
        """GET /api/chat — Recent chat messages."""
        return web.json_response({
            'messages': self._chat_rows(100),
        })

    async def _handle_maps(self, request):
        """GET /api/maps — Current mission rotation with resolved display names."""
        return web.json_response({
            'missions': self._json_safe(self._resolve_missions()),
            'available': self.sm.available_maps_data or '',
        })

    async def _handle_settings(self, request):
        """GET /api/settings — Current server settings."""
        return web.json_response({
            'settings': self._safe_settings(),
        })

    async def _handle_login_ips(self, request):
        """GET /api/login-ips — List of unique IPs with last login time."""
        return web.json_response({
            'ips': self.get_login_ips(),
        })

    async def _handle_quick_action(self, request):
        """POST /api/action — Execute a named, typed dashboard operation."""
        try:
            body = await self._json_object(request)
            action = str(body.get('action', '')).strip().lower()
            if not action:
                return web.json_response({'error': 'No action provided'}, status=400)
            if not self.sm.is_connected:
                return web.json_response(
                    {'error': 'Not connected to server'}, status=503
                )

            actions = {
                'next_map': self.sm.cycle_mission,
                'refresh_players': self.sm.refresh_players,
                'refresh_chat': self.sm.refresh_chat,
            }
            operation = actions.get(action)
            if operation is None:
                return web.json_response(
                    {'error': f'Unknown action: {action}'}, status=400
                )

            wire_log(f"WEB QUICK ACTION: {action}")
            result = await self._await_result(operation())
            return web.json_response(
                self._result_payload(result, action=action)
            )
        except ValueError as error:
            return web.json_response({'error': str(error)}, status=400)
        except HTTPException:
            raise
        except Exception as error:
            return web.json_response({'error': str(error)}, status=502)

    async def _handle_send_chat(self, request):
        """POST /api/chat/send — A chat line or a typed !command from the phone."""
        try:
            body = await self._json_object(request)
            msg = body.get('message', '')
            if not isinstance(msg, str) or not msg.strip():
                return web.json_response({'error': 'No message provided'}, status=400)
            if not self.sm.is_connected:
                return web.json_response({'error': 'Not connected to server'}, status=503)
            return web.json_response(await self._phone_message(msg))
        except ValueError as e:
            return web.json_response({'error': str(e)}, status=400)
        except HTTPException:
            raise
        except Exception as e:
            return web.json_response({'error': str(e)}, status=502)

    async def _handle_player_action(self, request):
        """POST /api/player/action — Admin action on a player."""
        try:
            body = await self._json_object(request)
            pid = str(body.get('pid', '')).strip()
            player_name = str(body.get('name', '')).strip()
            revision = body.get('revision')
            action = str(body.get('action', '')).strip().lower()
            if not pid or not action:
                return web.json_response({'error': 'Missing pid or action'}, status=400)
            if not self.sm.is_connected:
                return web.json_response({'error': 'Not connected to server'}, status=503)

            # The server parses this with (uint8)atol(), so anything unparseable
            # -- a player name, say -- silently becomes 0, which is the host.
            # KILL, SWAPTEAM and ZEROSCORE are not guarded against id 0 the way
            # PUNT is, so validate before we let it near them.
            try:
                pid_num = int(pid)
            except ValueError:
                return web.json_response({'error': f'Invalid player id: {pid!r}'}, status=400)
            if not 1 <= pid_num <= 250:
                return web.json_response(
                    {'error': f'Player id {pid_num} out of range (1-250)'}, status=400)

            if action not in {'kick', 'ban', 'kill', 'swap', 'zero'}:
                return web.json_response({'error': f'Unknown action: {action}'}, status=400)
            swaps_blocked = getattr(self.sm, 'swaps_blocked', None)
            if action == 'swap' and swaps_blocked and swaps_blocked():
                return web.json_response({'error': (
                    'No team swaps on co-op maps - the other team is the bots. '
                    'Use the Swap button in WolfRAT on the PC, or turn off '
                    "'Block team swaps on co-op maps' on the Chat Bot tab.")}, status=409)

            player = self._find_player_entry(
                pid_num, player_name, revision
            )
            actions = {
                'kick': lambda: self.sm.punt_player(player),
                'ban': lambda: self.sm.ban_player(player),
                'kill': lambda: self.sm.kill_player(player),
                # SWAPTEAM flips the player where they stand, alive among their
                # old team. Kill straight after, as !swap and !switch do.
                'swap': lambda: self.sm.swap_and_kill(player, player.name),
                'zero': lambda: self.sm.zero_player(player),
            }

            wire_log(
                f"WEB PLAYER ACTION: {action} on "
                f"{player.server_id}/{player.name}"
            )
            result = await self._await_result(actions[action]())
            return web.json_response(
                self._result_payload(
                    result,
                    action=action,
                    pid=player.server_id,
                    name=player.name,
                    revision=player.revision,
                )
            )
        except StalePlayerTarget as error:
            return web.json_response({'error': str(error)}, status=409)
        except ValueError as e:
            return web.json_response({'error': str(e)}, status=400)
        except HTTPException:
            raise
        except Exception as e:
            return web.json_response({'error': str(e)}, status=502)

    async def _handle_map_switch(self, request):
        """POST /api/map/switch — Switch to a specific map by filename."""
        try:
            body = await self._json_object(request)
            map_name = str(body.get('map', '')).strip()
            queue_index = body.get('index')
            revision = body.get('revision')
            if not map_name and queue_index is None:
                return web.json_response(
                    {'error': 'No mission identity provided'}, status=400
                )
            if not self.sm.is_connected:
                return web.json_response({'error': 'Not connected to server'}, status=503)

            mission = self._find_mission_entry(
                map_name,
                queue_index=queue_index,
                revision=revision,
            )
            wire_log(
                f"WEB MAP SWITCH: {mission.filename} at queue index "
                f"{mission.queue_index}"
            )
            result = await self._await_result(self.sm.switch_mission(mission))
            return web.json_response(self._result_payload(
                result,
                map=mission.filename,
                index=mission.queue_index,
                revision=mission.revision,
            ))
        except (AmbiguousMissionTarget, StaleMissionTarget) as error:
            return web.json_response({'error': str(error)}, status=409)
        except ValueError as e:
            return web.json_response({'error': str(e)}, status=400)
        except HTTPException:
            raise
        except Exception as e:
            return web.json_response({'error': str(e)}, status=502)

    async def _handle_websocket(self, request):
        """WebSocket endpoint for live state updates."""
        device_id = request.get(_DEVICE_ID)
        if len(self._ws_clients) >= MAX_WS_CLIENTS:
            return web.Response(status=503, text='Too many open connections')
        ip = request.remote or ''
        ws = web.WebSocketResponse(heartbeat=WS_HEARTBEAT)
        await ws.prepare(request)
        self._add_socket(ws, device_id, ip, asyncio.get_running_loop())
        wire_log(f"WEB: WebSocket connected ({len(self._ws_clients)} total)")

        # Send initial state
        try:
            await ws.send_str(json.dumps(self._build_state()))
        except Exception:
            pass

        try:
            async for msg in ws:
                # Its key may have been signed out (or the token changed)
                # since the connection opened.
                if not self._device_known(device_id):
                    await ws.close(code=WS_CLOSE_SIGNED_OUT, message=b'signed out')
                    break
                self._touch_device(device_id, ip)
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        if not isinstance(data, Mapping):
                            raise ValueError('Message must be a JSON object')
                        message_type = data.get('type')
                        # Only 'chat': the raw 'command' message is gone (see
                        # _create_application), typed !commands come as chat.
                        if message_type != 'chat':
                            await ws.send_str(json.dumps({
                                'type': 'error',
                                'error': f'Unknown message type: {message_type}',
                            }))
                            continue
                        if not self.sm.is_connected:
                            await ws.send_str(json.dumps({
                                'type': 'chat_result',
                                'ok': False,
                                'accepted': False,
                                'replies': [],
                                'error': 'Not connected to server',
                            }))
                            continue
                        try:
                            payload = await self._phone_message(data.get('message', ''))
                        except Exception as error:   # the page pairs it with its message
                            payload = {
                                'ok': False,
                                'accepted': False,
                                'replies': [],
                                'error': str(error),
                            }
                        await ws.send_str(json.dumps({'type': 'chat_result', **payload}))
                    except (json.JSONDecodeError, ValueError) as error:
                        await ws.send_str(json.dumps({
                            'type': 'error',
                            'error': str(error),
                        }))
                elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE):
                    break
        except Exception:
            pass
        finally:
            self._remove_socket(ws)
            wire_log(f"WEB: WebSocket disconnected ({len(self._ws_clients)} total)")

        return ws
