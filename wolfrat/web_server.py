"""
WolfRAT v2.4 — Embedded Web Server
Provides a mobile-friendly web UI for remote server administration.
All state reads from the existing ServerManager instance.
All commands go through the same protocol.py path as the desktop UI.
"""

import asyncio
import json
import os
import re
import secrets
import sys
import threading
import time
import logging

# Suppress aiohttp access logs (noisy GET /api/status every 5s)
logging.getLogger('aiohttp.access').setLevel(logging.WARNING)

try:
    from aiohttp import web
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

from wolfrat.protocol import wire_log


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


class WolfWebServer:
    """Embedded web server for WolfRAT mobile access."""

    def __init__(self, server_manager, host='0.0.0.0', port=8070):
        self._enabled = False
        self._running = False
        self._auth_lock = threading.Lock()
        self._web_username = "admin"
        self._web_token = None

        if not HAS_AIOHTTP:
            wire_log("WEB SERVER: aiohttp not installed, web server disabled")
            return

        self._enabled = True
        self._template_path = _get_template_path()
        wire_log(f"WEB SERVER: template path = {self._template_path} (exists={os.path.exists(self._template_path)})")
        self.sm = server_manager
        self.host = host
        self.port = port
        self._app = web.Application()
        self._ws_clients = set()
        self._loop = None
        self._thread = None
        self._runner = None
        self._last_broadcast = 0
        self._broadcast_interval = 1.0  # seconds between WebSocket pushes

        # Auth state — separate from JO server credentials
        self._login_ips = {}  # {ip: last_login_timestamp}
        self.on_login = None  # callback(ip, timestamp) for persistence

        # Routes (public)
        self._app.router.add_get('/', self._handle_index)
        self._app.router.add_post('/api/auth', self._handle_auth)
        self._app.router.add_get('/static/style.css', self._handle_css)
        self._app.router.add_get('/static/app.js', self._handle_js)

        # Routes (protected)
        self._app.router.add_get('/api/status', self._handle_status)
        self._app.router.add_get('/api/players', self._handle_players)
        self._app.router.add_get('/api/chat', self._handle_chat)
        self._app.router.add_get('/api/maps', self._handle_maps)
        self._app.router.add_get('/api/settings', self._handle_settings)
        self._app.router.add_get('/api/login-ips', self._handle_login_ips)
        self._app.router.add_post('/api/command', self._handle_command)
        self._app.router.add_post('/api/chat/send', self._handle_send_chat)
        self._app.router.add_post('/api/player/action', self._handle_player_action)
        self._app.router.add_post('/api/map/switch', self._handle_map_switch)
        self._app.router.add_get('/ws', self._handle_websocket)

    @property
    def is_running(self):
        return self._running

    def set_auth(self, username, token):
        """Set the web admin credentials (called from WebAdminTab)."""
        with self._auth_lock:
            self._web_username = username or "admin"
            self._web_token = token

    def start(self):
        """Start the web server in a background thread."""
        if not self._enabled or self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="WolfWeb")
        self._thread.start()

    def stop(self):
        """Stop the web server completely. Port is released, thread exits."""
        if not self._running:
            return  # Already stopped
        self._running = False
        if self._loop:
            # Schedule cleanup in the event loop
            async def _shutdown():
                # Close all WebSocket connections
                for ws in list(self._ws_clients):
                    try:
                        await ws.close()
                    except Exception:
                        pass
                self._ws_clients.clear()
                # Stop the runner (releases port)
                if self._runner:
                    await self._runner.cleanup()
                    self._runner = None
                self._loop.stop()

            future = asyncio.run_coroutine_threadsafe(_shutdown(), self._loop)
            try:
                future.result(timeout=3)
            except Exception:
                pass
        self._loop = None
        self._thread = None
        wire_log("WEB SERVER: stopped, port released")

    def restart(self):
        """Restart the web server (e.g. after port change)."""
        self.stop()
        time.sleep(1)
        self.start()

    def _run(self):
        """Run the aiohttp event loop in a background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._runner = web.AppRunner(self._app)
        self._loop.run_until_complete(self._runner.setup())
        site = web.TCPSite(self._runner, self.host, self.port)
        try:
            self._loop.run_until_complete(site.start())
            wire_log(f"WEB SERVER: listening on {self.host}:{self.port}")
            self._loop.run_forever()
        except OSError as e:
            wire_log(f"WEB SERVER ERROR: port {self.port} may be in use: {e}")
            self._running = False
        except Exception as e:
            wire_log(f"WEB SERVER ERROR: {e}")
            self._running = False
        finally:
            if self._runner:
                self._loop.run_until_complete(self._runner.cleanup())

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

    def _check_auth(self, request):
        """Check if request has valid auth token. Returns True if authorized."""
        with self._auth_lock:
            if not self._web_token:
                return False  # No token set = no access

        # Check Authorization header
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            token = auth[7:].strip()
            with self._auth_lock:
                if self._web_token and secrets.compare_digest(token, self._web_token):
                    return True
        # Check query param (for WebSocket)
        token = request.query.get('token', '')
        if token:
            with self._auth_lock:
                if self._web_token and secrets.compare_digest(token, self._web_token):
                    return True
        return False

    def broadcast_state(self):
        """Called by the desktop app when state changes. Pushes to all WebSocket clients.
        Throttled to avoid flooding — max once per _broadcast_interval seconds."""
        if not self._enabled or not self._running or not self._loop or not self._ws_clients:
            return
        now = time.time()
        if now - self._last_broadcast < self._broadcast_interval:
            return
        self._last_broadcast = now
        state = self._build_state()
        asyncio.run_coroutine_threadsafe(self._broadcast(json.dumps(state)), self._loop)

    def broadcast_chat(self):
        """Push chat update immediately (no throttle). Called on new chat messages."""
        if not self._enabled or not self._running or not self._loop or not self._ws_clients:
            return
        state = {'type': 'chat', 'chat': (self.sm.chat_messages or [])[-50:]}
        asyncio.run_coroutine_threadsafe(self._broadcast(json.dumps(state)), self._loop)

    async def _broadcast(self, message):
        """Send a message to all connected WebSocket clients."""
        dead = set()
        for ws in self._ws_clients:
            try:
                await ws.send_str(message)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead

    def _resolve_missions(self):
        """Resolve display names for mission list using available maps data."""
        import re
        missions_raw = self.sm.missions or []
        avail_raw = self.sm.available_maps_data or ''
        if not avail_raw:
            return missions_raw
        # Build filename -> display name lookup
        name_map = {}
        for line in avail_raw.split('\n'):
            line = line.strip()
            if not line:
                continue
            m = re.match(r'^\d+[.:]\s*', line)
            if m:
                line = line[m.end():]
            ext_match = re.search(r'(?i)\.(bms|npaj|npj)\b', line)
            if ext_match:
                filename = line[:ext_match.end()].strip()
                desc = line[ext_match.end():].strip()
                if desc.startswith('-'): desc = desc[1:].strip()
                if desc.startswith('('): desc = desc[1:].strip()
                if desc.endswith(')'): desc = desc[:-1].strip()
                name_map[filename.upper()] = desc if desc else filename
        # Replace filenames with display names
        missions_out = []
        for m in missions_raw:
            display = m
            ext_match = re.search(r'(?i)(\S+\.(?:bms|npj|npaj))', m)
            if ext_match:
                fname = ext_match.group(1)
                resolved = name_map.get(fname.upper())
                if resolved:
                    display = m.replace(fname, resolved)
            missions_out.append(display)
        return missions_out

    def _build_state(self):
        """Build a JSON-safe state snapshot from ServerManager."""
        return {
            'type': 'state',
            'timestamp': time.time(),
            'connected': self.sm.proto.connected if self.sm.proto else False,
            'players': self.sm.players or [],
            'chat': (self.sm.chat_messages or [])[-50:],
            'game_state': self.sm.game_state or {},
            'missions': self._resolve_missions(),
            'settings': self.sm.game_settings or {},
            'player_count': len(self.sm.players) if self.sm.players else 0,
            'game_mode': self.sm.game_state.get('mode', 'Unknown') if self.sm.game_state else 'Unknown',
        }

    def _find_map_index(self, filename):
        """Find a map's rotation index. MISSION SETNEXT expects the rotation index, not the available maps index."""
        wire_log(f"MAP LOOKUP: searching for '{filename}' in rotation")
        # Search rotation list first — this is what MISSION SETNEXT expects
        missions = self.sm.missions or []
        for line in missions:
            match = re.match(r'(\d+):\s*(\S+\.(?:bms|npj|npaj))', line, re.IGNORECASE)
            if match:
                idx = int(match.group(1))
                server_filename = match.group(2)
                if server_filename.lower() == filename.lower():
                    wire_log(f"MAP LOOKUP: found '{filename}' at rotation index {idx}")
                    return idx
        wire_log(f"MAP LOOKUP: '{filename}' not found in rotation")
        return -1

    # --- Auth ---

    async def _handle_auth(self, request):
        """POST /api/auth — Validate username + token."""
        try:
            body = await request.json()
            username = body.get('username', '').strip()
            token = body.get('token', '').strip()
            if not username or not token:
                return web.json_response({'error': 'Missing username or token'}, status=400)

            with self._auth_lock:
                valid_user = self._web_username
                valid_token = self._web_token

            if not valid_token:
                return web.json_response({'error': 'Web admin is not enabled'}, status=403)

            if (secrets.compare_digest(username, valid_user) and
                    secrets.compare_digest(token, valid_token)):
                # Track IP
                ip = request.remote or 'unknown'
                self._login_ips[ip] = time.time()
                wire_log(f"WEB AUTH: {username} logged in from {ip}")
                # Notify for persistence
                if self.on_login:
                    try:
                        self.on_login(ip, time.time())
                    except Exception:
                        pass
                # Return the access token itself — client uses it for all requests
                return web.json_response({'ok': True, 'token': token})

            wire_log(f"WEB AUTH FAILED: {username}")
            return web.json_response({'error': 'Invalid username or token'}, status=401)
        except Exception as e:
            return web.json_response({'error': str(e)}, status=500)

    # --- HTTP Handlers ---

    async def _handle_index(self, request):
        """Serve the mobile web UI."""
        template_path = os.path.join(self._template_path, 'index.html')
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                html = f.read()
            return web.Response(text=html, content_type='text/html')
        except FileNotFoundError:
            return web.Response(text='<h1>WolfRAT Web UI</h1><p>Template not found.</p>', content_type='text/html')

    async def _handle_css(self, request):
        """Serve the CSS stylesheet."""
        css_path = os.path.join(self._template_path, 'style.css')
        try:
            with open(css_path, 'r', encoding='utf-8') as f:
                css = f.read()
            return web.Response(text=css, content_type='text/css')
        except FileNotFoundError:
            return web.Response(text='', content_type='text/css')

    async def _handle_js(self, request):
        """Serve the JavaScript app."""
        js_path = os.path.join(self._template_path, 'app.js')
        try:
            with open(js_path, 'r', encoding='utf-8') as f:
                js = f.read()
            return web.Response(text=js, content_type='application/javascript')
        except FileNotFoundError:
            return web.Response(text='// app.js not found', content_type='application/javascript')

    async def _handle_status(self, request):
        """GET /api/status — Server status overview."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        return web.json_response(self._build_state())

    async def _handle_players(self, request):
        """GET /api/players — Current player list."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        return web.json_response({
            'players': self.sm.players or [],
            'count': len(self.sm.players) if self.sm.players else 0,
        })

    async def _handle_chat(self, request):
        """GET /api/chat — Recent chat messages."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        return web.json_response({
            'messages': (self.sm.chat_messages or [])[-100:],
        })

    async def _handle_maps(self, request):
        """GET /api/maps — Current mission rotation with resolved display names."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        return web.json_response({
            'missions': self._resolve_missions(),
            'available': self.sm.available_maps_data or '',
        })

    async def _handle_settings(self, request):
        """GET /api/settings — Current server settings."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        return web.json_response({
            'settings': self.sm.game_settings or {},
        })

    async def _handle_login_ips(self, request):
        """GET /api/login-ips — List of unique IPs with last login time."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        return web.json_response({
            'ips': self.get_login_ips(),
        })

    async def _handle_command(self, request):
        """POST /api/command — Send a raw command to the server."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        try:
            body = await request.json()
            cmd = body.get('command', '').strip()
            if not cmd:
                return web.json_response({'error': 'No command provided'}, status=400)
            if not self.sm.proto.connected:
                return web.json_response({'error': 'Not connected to server'}, status=503)
            wire_log(f"WEB CMD: {cmd}")
            self.sm.send(cmd)
            return web.json_response({'ok': True, 'command': cmd})
        except Exception as e:
            return web.json_response({'error': str(e)}, status=500)

    async def _handle_send_chat(self, request):
        """POST /api/chat/send — Send a chat message."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        try:
            body = await request.json()
            msg = body.get('message', '').strip()
            if not msg:
                return web.json_response({'error': 'No message provided'}, status=400)
            if not self.sm.proto.connected:
                return web.json_response({'error': 'Not connected to server'}, status=503)
            self.sm.send_chat(msg)
            return web.json_response({'ok': True})
        except Exception as e:
            return web.json_response({'error': str(e)}, status=500)

    async def _handle_player_action(self, request):
        """POST /api/player/action — Admin action on a player."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        try:
            body = await request.json()
            pid = body.get('pid', '').strip()
            action = body.get('action', '').strip().lower()
            if not pid or not action:
                return web.json_response({'error': 'Missing pid or action'}, status=400)
            if not self.sm.proto.connected:
                return web.json_response({'error': 'Not connected to server'}, status=503)

            actions = {
                'kick': lambda: self.sm.punt_player(pid),
                'ban': lambda: self.sm.ban_player(pid),
                'kill': lambda: self.sm.kill_player(pid),
                'swap': lambda: self.sm.swap_player(pid),
                'zero': lambda: self.sm.zero_player(pid),
            }
            if action not in actions:
                return web.json_response({'error': f'Unknown action: {action}'}, status=400)

            wire_log(f"WEB PLAYER ACTION: {action} on {pid}")
            actions[action]()
            return web.json_response({'ok': True, 'action': action, 'pid': pid})
        except Exception as e:
            return web.json_response({'error': str(e)}, status=500)

    async def _handle_map_switch(self, request):
        """POST /api/map/switch — Switch to a specific map by filename."""
        if not self._check_auth(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        try:
            body = await request.json()
            map_name = body.get('map', '').strip()
            if not map_name:
                return web.json_response({'error': 'No map name provided'}, status=400)
            if not self.sm.proto.connected:
                return web.json_response({'error': 'Not connected to server'}, status=503)

            # Find the server index for this map
            idx = self._find_map_index(map_name)
            if idx >= 0:
                wire_log(f"WEB MAP SWITCH: {map_name} -> server index {idx}")
                self.sm.send(f"MISSION SETNEXT {idx}")
                time.sleep(0.3)
                self.sm.send("GOTO GAMESTATE")
                return web.json_response({'ok': True, 'map': map_name, 'index': idx})
            else:
                # Fallback: try sending as mission name directly
                wire_log(f"WEB MAP SWITCH: index not found, trying direct command for {map_name}")
                self.sm.set_mission(map_name)
                return web.json_response({'ok': True, 'map': map_name, 'method': 'direct'})
        except Exception as e:
            return web.json_response({'error': str(e)}, status=500)

    async def _handle_websocket(self, request):
        """WebSocket endpoint for live state updates."""
        if not self._check_auth(request):
            return web.Response(status=401, text='Unauthorized')
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_clients.add(ws)
        wire_log(f"WEB: WebSocket connected ({len(self._ws_clients)} total)")

        # Send initial state
        try:
            await ws.send_str(json.dumps(self._build_state()))
        except Exception:
            pass

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        if data.get('type') == 'command' and self.sm.proto.connected:
                            self.sm.send(data.get('command', ''))
                        elif data.get('type') == 'chat' and self.sm.proto.connected:
                            self.sm.send_chat(data.get('message', ''))
                    except json.JSONDecodeError:
                        pass
                elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE):
                    break
        except Exception:
            pass
        finally:
            self._ws_clients.discard(ws)
            wire_log(f"WEB: WebSocket disconnected ({len(self._ws_clients)} total)")

        return ws
