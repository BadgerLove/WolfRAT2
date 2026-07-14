"""
WolfRAT 2.0 — Protocol Module (v3 - robust)
"""

import socket
import struct
import threading
import time
import os
import sys
import traceback

# Wire log - dead simple, no locks, no lazy init
def _wire_log_path():
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.dirname(sys.executable), 'wolfrat_wire.log')
    return os.path.join(os.getcwd(), 'wolfrat_wire.log')

# Write startup marker immediately on import
try:
    with open(_wire_log_path(), 'w', encoding='utf-8') as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] === protocol.py module loaded ===\n")
except Exception as e:
    print(f"WIRE LOG INIT ERROR: {e}")

def wire_log(msg):
    try:
        with open(_wire_log_path(), 'a', encoding='utf-8') as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception as e:
        print(f"WIRE LOG ERROR: {e}")



# ============== ENCRYPTION ==============
# Algorithm reverse-engineered from original JO:RAT tool
# Confirmed working against live JO server (65/65 byte match)

_JO_LCG_MULTIPLIER = 0x04B05731

def _jo_encrypt(challenge_payload, username, password):
    """Encrypt auth packet for JO server login.
    
    Args:
        challenge_payload: bytes from server challenge (after 8-byte header)
        username: str (e.g. "badger")
        password: str (e.g. "aaaaaa")
    Returns: 65-byte encrypted auth payload
    """
    # Buffer = username(32) + password(32) + null(1)
    buf = bytearray(65)
    usr = username.encode('ascii', errors='replace')
    pwd = password.encode('ascii', errors='replace')
    for i in range(min(len(usr), 32)):
        buf[i] = usr[i]
    for i in range(min(len(pwd), 32)):
        buf[32 + i] = pwd[i]
    
    # Key = challenge payload (strlen semantics - stops at first null)
    key = challenge_payload
    key_len = 0
    for b in key:
        if b == 0:
            break
        key_len += 1
    if key_len == 0:
        key_len = len(key)
    
    # Password hash: sum(key[i]^2 + i) + 0x32 + key_len
    h = 0
    for i in range(key_len):
        c = key[i]
        if c > 127:
            c -= 256
        h = (h + c * c + i) & 0xFFFFFFFF
    h = (h + 0x32 + key_len) & 0xFFFFFFFF
    
    # LCG initialization (3 steps)
    s1 = (h * _JO_LCG_MULTIPLIER + 1) & 0xFFFF
    s2 = (_JO_LCG_MULTIPLIER * s1 + 1) & 0xFFFF
    s3 = (_JO_LCG_MULTIPLIER * s2 + 1) & 0xFFFF
    lcg_state = s3
    
    # Cycling add: buf[i] += key[i % key_len]
    for i in range(65):
        buf[i] = (buf[i] + key[i % key_len]) & 0xFF
    
    # Conditional reverse (if s3 is odd)
    if (s3 & 1) != 0:
        buf.reverse()
    
    # Param pass: buf[i] += i + b1; b1 += b2
    b1 = s1 & 0xFF
    b2 = s2 & 0xFF
    for i in range(65):
        buf[i] = (buf[i] + (i & 0xFF) + b1) & 0xFF
        b1 = (b1 + b2) & 0xFF
    
    # LCG stream pass: state = mult*state+1; buf[i] += state & 0xFF
    state = lcg_state
    for i in range(65):
        state = (_JO_LCG_MULTIPLIER * state + 1) & 0xFFFF
        buf[i] = (buf[i] + (state & 0xFF)) & 0xFF
    
    return bytes(buf)

# ============== END ENCRYPTION ==============

class WolfProtocol:
    HEADER_SIZE = 8
    DEFAULT_PORT = 4000

    def __init__(self):
        self.sock = None
        self.connected = False
        self.host = ""
        self.port = self.DEFAULT_PORT
        self.username = ""
        self._recv_thread = None
        self._running = False
        self._on_packet = None
        self._on_connect = None
        self._on_disconnect = None
        self._on_log = None
        self._buffer = b""
        self._lock = threading.Lock()

    def set_callbacks(self, on_packet=None, on_connect=None, on_disconnect=None, on_log=None):
        self._on_packet = on_packet
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_log = on_log

    def _log(self, msg):
        if self._on_log:
            self._on_log(msg)

    def connect(self, host, port=DEFAULT_PORT, username="", password=""):
        if self.connected:
            self.disconnect()

        self.host = host
        self.port = port
        self.username = username
        self._buffer = b""

        wire_log(f"=== CONNECTING to {host}:{port} as {username} ===")

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            self.sock.settimeout(10)
            self.sock.connect((host, port))

            wire_log(f"TCP CONNECTED")

            # --- JO encrypted login ---
            self.sock.settimeout(5)
            challenge = self.sock.recv(1024)
            wire_log("CHALLENGE: {} bytes".format(len(challenge)))

            if len(challenge) >= 9:
                payload = challenge[8:]  # skip 8-byte header
                encrypted = _jo_encrypt(payload, username, password)
                auth_header = b'\x00\x00\x0d\x0a' + struct.pack('<I', 8 + 65)
                auth_packet = auth_header + encrypted
                self.sock.sendall(auth_packet)
                wire_log("AUTH SENT: {} bytes".format(len(auth_packet)))

                resp = self.sock.recv(1024)
                resp_text = resp[8:].rstrip(b'\x00').decode('ascii', errors='replace') if len(resp) > 8 else ''
                wire_log("LOGIN RESPONSE: {}".format(resp_text))

                if 'logged in' not in resp_text.lower():
                    self.sock.close()
                    return False, f"Login failed: {resp_text}"
            # --- end login ---

            self.sock.settimeout(None)
            self.connected = True
            self._running = True

            self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True, name="WolfRecv")
            self._recv_thread.start()

            if self._on_connect:
                self._on_connect()

            return True, f"Connected to {host}:{port}"

        except socket.timeout:
            wire_log(f"CONNECT TIMEOUT")
            return False, f"Connection to {host}:{port} timed out"
        except ConnectionRefusedError:
            wire_log(f"CONNECT REFUSED")
            return False, f"Connection refused by {host}:{port}"
        except OSError as e:
            wire_log(f"CONNECT ERROR: {e}")
            return False, f"Connection error: {e}"

    def disconnect(self):
        wire_log(f"DISCONNECT called")
        self._running = False
        self.connected = False
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def _do_disconnect(self, reason):
        wire_log(f"DO_DISCONNECT: {reason}")
        was_connected = self.connected
        self.connected = False
        self._running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        if was_connected and self._on_disconnect:
            self._on_disconnect(reason)

    def send_command(self, command):
        if not self.connected or not self.sock:
            wire_log(f"SEND FAILED: not connected")
            return False

        try:
            with self._lock:
                payload = command.encode('ascii', errors='replace')
                total_len = self.HEADER_SIZE + len(payload) + 1
                header = b'\x00\x00\x0d\x0a' + struct.pack('<I', total_len)
                packet = header + payload + b'\x00'

                wire_log(f"SEND: {command!r} -> {len(packet)} bytes: {packet[:40].hex()}")
                self.sock.sendall(packet)
                time.sleep(0.075)
                return True

        except Exception as e:
            wire_log(f"SEND ERROR: {e}")
            self._do_disconnect(f"Send error: {e}")
            return False

    def _recv_loop(self):
        wire_log(f"RECV THREAD STARTED")
        try:
            while self._running and self.connected and self.sock:
                try:
                    data = self.sock.recv(8192)
                    if not data:
                        wire_log(f"RECV: empty (server closed)")
                        self._do_disconnect("Server closed connection")
                        break

                    wire_log(f"RECV: {len(data)} bytes")
                    wire_log(f"  hex: {data[:80].hex()}")
                    wire_log(f"  ascii: {data[:200]}")

                    self._buffer += data
                    self._process_buffer()

                except ConnectionResetError as e:
                    wire_log(f"RECV RESET: {e}")
                    self._do_disconnect("Connection reset")
                    break
                except ConnectionAbortedError as e:
                    wire_log(f"RECV ABORTED: {e}")
                    self._do_disconnect("Connection aborted")
                    break
                except OSError as e:
                    wire_log(f"RECV OSERROR: {e}")
                    if self.connected:
                        self._do_disconnect(f"Socket error: {e}")
                    break
                except Exception as e:
                    wire_log(f"RECV EXCEPTION: {e}\n{traceback.format_exc()}")
                    self._do_disconnect(f"Recv error: {e}")
                    break
        except Exception as e:
            wire_log(f"RECV THREAD CRASH: {e}\n{traceback.format_exc()}")

        wire_log(f"RECV THREAD EXITED")

    def _process_buffer(self):
        iterations = 0
        while len(self._buffer) >= self.HEADER_SIZE:
            iterations += 1
            if iterations > 50:
                wire_log(f"PARSE: too many iterations, clearing buffer")
                self._buffer = b""
                break

            # Try to find valid packet header (CR+LF at bytes 6-7)
            found = False

            # First try: assume buffer starts with a header
            if len(self._buffer) >= 8:
                if self._buffer[2] == 0x0D and self._buffer[3] == 0x0A:
                    total_len = struct.unpack('<I', self._buffer[4:8])[0]
                    if 8 <= total_len <= 65536:
                        found = True

            # Second try: scan for CR+LF and check if it's at offset 6
            if not found:
                pos = 0
                while True:
                    crlf_pos = self._buffer.find(b'\x0d\x0a', pos)
                    if crlf_pos < 0:
                        break
                    candidate = crlf_pos - 6
                    if candidate >= 0 and candidate + 8 <= len(self._buffer):
                        total_len = struct.unpack('<I', self._buffer[candidate+4:candidate+8])[0]
                        if 8 <= total_len <= 65536:
                            if candidate > 0:
                                wire_log(f"PARSE: skipping {candidate} bytes: {self._buffer[:candidate].hex()}")
                            self._buffer = self._buffer[candidate:]
                            found = True
                            break
                    pos = crlf_pos + 2

            if not found:
                # No valid header found — skip the byte and try again
                wire_log(f"PARSE: no valid header, skipping byte 0x{self._buffer[0]:02X}")
                self._buffer = self._buffer[1:]
                continue

            # We have a header, get total length
            total_len = struct.unpack('<I', self._buffer[4:8])[0]

            if len(self._buffer) < total_len:
                wire_log(f"PARSE: incomplete packet ({len(self._buffer)}/{total_len})")
                break

            # Extract payload
            payload_bytes = self._buffer[self.HEADER_SIZE:total_len]
            self._buffer = self._buffer[total_len:]

            wire_log(f"PARSE: packet {total_len} bytes, payload {len(payload_bytes)} bytes")
            wire_log(f"  payload hex: {payload_bytes[:80].hex()}")
            wire_log(f"  payload ascii: {payload_bytes[:200]}")

            try:
                payload = payload_bytes.rstrip(b'\x00').decode('ascii', errors='replace').strip()
            except Exception:
                payload = repr(payload_bytes)

            if payload and self._on_packet:
                wire_log(f"  -> dispatching to handler")
                try:
                    self._on_packet(payload)
                except Exception as e:
                    wire_log(f"  HANDLER ERROR: {e}\n{traceback.format_exc()}")


class ServerManager:
    def __init__(self):
        self.proto = WolfProtocol()
        self.game_state = {}
        self.players = []
        self.missions = []
        self.chat_messages = []
        self.game_settings = {}
        self._poll_thread = None
        self._cmd_queue = []  # queue of expected response types
        self._polling = False
        self._on_players = None
        self._on_chat = None
        self._on_gamestate = None
        self._on_missions = None
        self._on_settings = None
        self._on_available_maps = None
        self._on_log = None
        self._on_disconnect_ui = None
        self.available_maps_data = ""
        self._last_recv_time = time.time()

    def set_callbacks(self, on_players=None, on_chat=None, on_gamestate=None,
                      on_missions=None, on_settings=None, on_available_maps=None, on_log=None, on_disconnect_ui=None, on_connect_done=None):
        self._on_players = on_players
        self._on_chat = on_chat
        self._on_gamestate = on_gamestate
        self._on_missions = on_missions
        self._on_settings = on_settings
        self._on_available_maps = on_available_maps
        self._on_log = on_log
        self._on_disconnect_ui = on_disconnect_ui
        self._on_connect_done = on_connect_done

    def connect(self, host, port=4000, username="", password=""):
        self.proto.set_callbacks(
            on_packet=self._handle_packet,
            on_connect=lambda: self._log("Socket connected"),
            on_disconnect=lambda msg: self._on_server_disconnect(msg),
            on_log=self._log
        )
        # Clear chat history on reconnect so dedup sets re-initialize
        self.chat_messages = []
        self._cmd_queue = []
        success, msg = self.proto.connect(host, port, username, password)
        if success:
            time.sleep(0.3)
            # Reset chat state BEFORE polling starts (not via signal, which fires too late)
            if hasattr(self, '_on_connect_done') and self._on_connect_done:
                self._on_connect_done()
            self.refresh_all()
            # Give the JO server time to finish sending all responses
            # before polling starts. Large responses (mission available = 8KB+)
            # can take multiple recv cycles. Rushing causes server crashes.
            time.sleep(3.0)
            self.start_polling(5.0)
        return success, msg

    def disconnect(self):
        self._polling = False
        self.proto.disconnect()

    def refresh_all(self):
        self.send("get gamestate")
        time.sleep(0.5)
        self.send("player list")
        time.sleep(0.5)
        self.send("mission list")
        time.sleep(0.5)
        self.send("mission available")
        time.sleep(1.0)  # mission available returns 8KB+, needs extra time
        self.send("get gamesettings")
        time.sleep(0.5)
        self.send("chat get")

    def send(self, command, quiet=False):
        if not quiet:
            self._log(f">> {command}")
        # Queue what we're expecting in response
        # quiet=True means fire-and-forget (recurring messages, polling) — don't queue
        if quiet:
            return self.proto.send_command(command)

        cmd_lower = command.lower()
        if 'player list' in cmd_lower:
            self._cmd_queue.append('players')
        elif 'chat get' in cmd_lower:
            self._cmd_queue.append('chat')
        elif 'get gamestate' in cmd_lower:
            self._cmd_queue.append('gamestate')
        elif 'mission list' in cmd_lower:
            self._cmd_queue.append('missions')
        elif 'mission available' in cmd_lower:
            self._cmd_queue.append('available')
        elif 'get gamesettings' in cmd_lower:
            self._cmd_queue.append('settings')
        # Commands that don't expect a useful response
        elif 'chat send' in cmd_lower or 'cmd ' in cmd_lower or 'set ' in cmd_lower:
            self._cmd_queue.append(None)
        elif 'mission add' in cmd_lower or 'mission clear' in cmd_lower or 'mission remove' in cmd_lower:
            self._cmd_queue.append(None)
        return self.proto.send_command(command)

    def warn_player(self, pid, msg): self.send(f"cmd warn {pid} {msg}")
    def punt_player(self, pid, msg=""): self.send(f"PLAYER PUNT {pid}")
    def ban_player(self, pid, msg=""): self.send(f"PLAYER BAN {pid}")
    def kill_player(self, pid): self.send(f"PLAYER KILL {pid}")
    def swap_player(self, pid): self.send(f"PLAYER SWAPTEAM {pid}")
    def zero_player(self, pid): self.send(f"PLAYER ZEROSCORE {pid}")
    def set_mission(self, name): self.send(f"mission {name}")
    def next_map(self): self.send("MISSION CYCLE")
    def set_setting(self, key, val):
        # Map lowercase UI key back to server's CamelCase
        server_key = getattr(self, '_key_case_map', {}).get(key.lower(), key)
        self.send(f"set {server_key} {val}")
    def toggle_autobalance(self, on): self.set_setting("autoBalanceOnRecycle", "1" if on else "0")
    def toggle_team_switch(self, on): self.set_setting("changeTeam", "1" if on else "0")
    def set_vote_percent(self, pct): self.set_setting("votePercent", str(pct))
    def set_team_switch_interval(self, sec): self.set_setting("changeTeamInterval", str(sec))
    def send_chat(self, msg, quiet=False):
        # JO server admin buffer is 122 bytes (PUSH 122 at 0x4BE1 in binary)
        # "chat send " prefix = 11 chars. 122 - 11 = 111 chars for message.
        # 69-char limit tested and confirmed in-game July 13 2026.
        if len(msg) > 69:
            msg = msg[:66] + "..."
            wire_log(f"CHAT: truncated to 69 chars")
        self.send(f"chat send {msg}", quiet=quiet)

    def mix_teams(self):
        """Randomly shuffle players to balance teams at round start.
        Only swaps the minimum needed. Announces + kills after swap.
        Returns list of actions taken.
        """
        import random
        if not self.players:
            return ["No players to mix"]

        # Separate into teams
        team_a = []  # team 1
        team_b = []  # team 2
        for p in self.players:
            try:
                t = int(p.get('team', '0'))
            except (ValueError, TypeError):
                t = 0
            if t == 1:
                team_a.append(p)
            elif t == 2:
                team_b.append(p)

        if len(team_a) + len(team_b) < 2:
            return ["Need at least 2 players to mix"]

        # Calculate how many need to move to balance
        total = len(team_a) + len(team_b)
        target = total // 2  # ideal team size

        if abs(len(team_a) - len(team_b)) <= 1:
            return ["Teams are already balanced (within 1 player)"]

        # Figure out which team is bigger and how many to move
        if len(team_a) > len(team_b):
            bigger, smaller = team_a, team_b
            bigger_team = 1
        else:
            bigger, smaller = team_b, team_a
            bigger_team = 2

        num_to_move = len(bigger) - target

        # Randomly pick players from the bigger team
        to_move = random.sample(bigger, min(num_to_move, len(bigger)))

        # Build name list for announcement
        names = [p.get('name', '?') for p in to_move]
        name_list = ', '.join(names)

        results = [f"Mixing {len(to_move)} players from Team {bigger_team}..."]

        # Announce the mix — one message with all names
        self.send_chat(f"Mixing teams: {name_list}")
        time.sleep(0.5)
        self.send_chat("Please wait...")
        time.sleep(1.0)

        for p in to_move:
            pid = p.get('id', '')
            name = p.get('name', '?')

            # Swap
            self.swap_player(pid)
            results.append(f"  Swapped {name}")
            time.sleep(0.5)

            # Kill so they respawn at correct base
            self.kill_player(pid)
            results.append(f"  Killed {name}")
            time.sleep(0.3)

        time.sleep(1.0)
        self.send_chat("Teams mixed! Good luck all.")
        return results

    def shuffle_teams(self):
        """Randomly shuffle ALL players across both teams."""
        import random
        players = list(self.players)
        if len(players) < 2:
            return ["No players to shuffle"]

        random.shuffle(players)
        mid = len(players) // 2
        new_team_a = players[:mid]   # Joint Ops
        new_team_b = players[mid:]   # Rebels

        results = [f"Shuffling {len(players)} players..."]

        # Figure out who actually needs to move
        to_move = []
        for p in new_team_a:
            if p.get('team') != '1':
                to_move.append((p, '1', 'Joint Ops'))
        for p in new_team_b:
            if p.get('team') != '2':
                to_move.append((p, '2', 'Rebels'))

        if not to_move:
            self.send_chat("Teams already shuffled — no changes needed!")
            return ["No changes needed"]

        names = [p.get('name', '?') for p, _, _ in to_move]
        self.send_chat(f"Mixing: {', '.join(names)}")
        time.sleep(1.0)

        for p, new_team, team_name in to_move:
            pid = p.get('id', '')
            name = p.get('name', '?')
            self.swap_player(pid)
            results.append(f"  {name} -> {team_name}")
            time.sleep(0.5)
            self.kill_player(pid)
            time.sleep(0.3)

        time.sleep(1.0)
        self.send_chat("Teams shuffled! Good luck all.")
        return results

    def swap_and_kill(self, pid, name=""):
        """Swap a player to the other team and kill them so they respawn at the right base."""
        display = name or str(pid)
        self.send_chat(f"{display} switched to the other team")
        time.sleep(0.3)
        self.swap_player(pid)
        time.sleep(0.5)
        self.kill_player(pid)

    def announce(self, msg):
        """Send a server-wide chat message. Splits long messages into chunks."""
        MAX_LEN = 80  # safe limit for in-game chat display
        if len(msg) <= MAX_LEN:
            self.send_chat(msg, quiet=True)
            return

        # Split on word boundaries
        words = msg.split()
        chunk = ""
        for word in words:
            if len(chunk) + len(word) + 1 > MAX_LEN:
                if chunk:
                    self.send_chat(chunk, quiet=True)
                chunk = word
            else:
                chunk = f"{chunk} {word}".strip()
        if chunk:
            self.send_chat(chunk, quiet=True)

    def get_team_stats(self):
        """Return team balance info."""
        team_a = [p for p in self.players if p.get('team') == '1']
        team_b = [p for p in self.players if p.get('team') == '2']
        try:
            score_a = sum(int(p.get('kills', '0')) for p in team_a)
            score_b = sum(int(p.get('kills', '0')) for p in team_b)
        except (ValueError, TypeError):
            score_a, score_b = 0, 0
        return {
            'team_a_count': len(team_a),
            'team_b_count': len(team_b),
            'team_a_score': score_a,
            'team_b_score': score_b,
            'difference': abs(len(team_a) - len(team_b)),
            'score_diff': abs(score_a - score_b),
        }

    def start_polling(self, interval=5.0):
        self._polling = True
        self._poll_thread = threading.Thread(target=self._poll_loop, args=(interval,), daemon=True)
        self._poll_thread.start()

    def stop_polling(self):
        self._polling = False

    def _poll_loop(self, interval):
        self._log(f"Polling every {interval}s")
        # NOTE: No first_cycle gamesettings fetch here.
        # refresh_all() already sent it. Duplicate rapid-fire
        # commands crash the JO server.
        stall_logged = False
        while self._polling and self.proto.connected:
            try:
                time.sleep(interval)
                if self._polling and self.proto.connected:
                    # If server hasn't responded in 30s, don't flood it
                    silence = time.time() - self._last_recv_time
                    if silence > 30:
                        if not stall_logged:
                            wire_log(f"POLL STALL: no response in {silence:.0f}s, pausing sends")
                            self._log(f"Server not responding ({silence:.0f}s), waiting...")
                            stall_logged = True
                        continue  # skip this cycle, wait for recv
                    if stall_logged:
                        wire_log(f"POLL RECOVERED: server responded after {silence:.0f}s")
                        self._log("Server responding again")
                        stall_logged = False
                    self.send("get gamestate", quiet=True)
                    time.sleep(0.3)
                    self.send("player list", quiet=True)
                    time.sleep(0.3)
                    self.send("chat get", quiet=True)
                    time.sleep(0.3)
                    self.send("mission list", quiet=True)
                    time.sleep(0.3)
                    self.send("get gamesettings", quiet=True)
            except Exception as e:
                wire_log(f"POLL ERROR: {e}")
                if not self.proto.connected:
                    break
                time.sleep(interval)  # retry after wait
        self._log("Polling stopped")

    def _handle_packet(self, payload):
        self._last_recv_time = time.time()
        self._log(f"<< {payload[:200]}")
        wire_log(f"HANDLE_PACKET: first 100 chars: {payload[:100]!r}")
        lower = payload.lower()

        # Content-based detection FIRST (queue can get misaligned)
        # Use strong content signals to identify each response type.

        # 1. Settings: has known settings keys (CHECK FIRST — settings contain "name",
        #    "team", "kills" as substrings of keys like ChangeTeam, MaxKills, ServerName
        #    which falsely match the player check)
        #    NOTE: key names must match what the JO server actually sends!
        if ('autobalance' in lower or 'votepercent' in lower or 'changeteam' in lower
                or 'friendlyfire' in lower or 'killlimit' in lower
                or 'tracers' in lower or 'fatbullets' in lower or 'oneshotkill' in lower
                or 'armorytimer' in lower or 'startdelay' in lower or 'kothlimit' in lower
                or 'puntvote' in lower or 'maxfriendlykills' in lower or 'maxscore' in lower):
            wire_log(f'CONTENT MATCH: settings')
            self._parse_settings(payload)
            return

        # 2. Player list: has NAME<TAB>#<TAB>TEAM header (actual tab-separated format)
        #    NOT just loose keywords — settings also contain 'name', 'team', 'kills'
        if ('\t' in payload and '#' in payload and 'name' in lower and 'team' in lower):
            wire_log(f'CONTENT MATCH: players')
            self._parse_players(payload)
            return

        # 3. Gamestate: has "Current State" or "State ="
        if 'current state' in lower or 'state =' in lower:
            wire_log(f'CONTENT MATCH: gamestate')
            self._parse_gamestate(payload)
            return

        # 4. Mission list (rotation): has <CURRENT MISSION> or (2x) markers
        #    MUST be before chat — mission lines like '0: DM-COD4Killhouse.npj' have colons
        if '<current mission>' in lower or '(2x)' in lower:
            wire_log(f'CONTENT MATCH: missions')
            self._parse_missions(payload)
            return

        # 5. Available maps: has numbered list with descriptions
        #    Only fires from manual Refresh button (not in polling loop)
        if ('.bms' in lower or '.npaj' in lower or '.npj' in lower):
            if ' (' in payload and ')' in payload:
                wire_log(f'CONTENT MATCH: available maps (description format)')
                self._parse_available_maps(payload)
                return
            import re
            if re.search(r'\d+\.\s+\S+\.(bms|npaj|npj)', payload, re.IGNORECASE):
                wire_log(f'CONTENT MATCH: available maps (numbered format)')
                self._parse_available_maps(payload)
                return

        # 6. Chat: has ':' (Name: message format) but no tabs (not player list)
        #    AFTER missions/available maps — those also have colons
        #    Simple check: payload has lines with 'word: text' pattern, no tabs
        if '\t' not in payload and ':' in payload:
            # Quick heuristic: at least one line has 'Name: message' format
            has_chat = False
            for line in payload.split('\n'):
                line = line.strip()
                if not line or '=' in line or line.startswith('#'):
                    continue
                if ': ' in line and not line[0].isdigit():
                    has_chat = True
                    break
            if has_chat:
                wire_log(f'CONTENT MATCH: chat')
                self._parse_chat(payload)
                return

        cmd_type = self._cmd_queue.pop(0) if self._cmd_queue else None
        wire_log(f'QUEUE FALLBACK: popped={cmd_type}, payload[:80]={payload[:80]!r}')

        if cmd_type == 'players':
            self._parse_players(payload)
        elif cmd_type == 'chat':
            self._parse_chat(payload)
        elif cmd_type == 'gamestate':
            self._parse_gamestate(payload)
        elif cmd_type == 'missions':
            self._parse_missions(payload)
        elif cmd_type == 'available':
            self._parse_available_maps(payload)
        elif cmd_type == 'settings':
            self._parse_settings(payload)

    def _parse_players(self, data):
        players = []
        lines = data.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Skip header line
            if line.startswith('NAME') or line.startswith('---'):
                continue
            # Server format: NAME<TAB>#<TAB>TEAM<TAB>Class<TAB>Kills<TAB>Deaths<TAB>PING
            parts = line.split('\t')
            parts = [p.strip() for p in parts]
            if len(parts) >= 3:
                name = parts[0].strip()
                pid = parts[1].strip()
                team = parts[2].strip()
                # Skip host (ID 0)
                if pid == '0':
                    continue
                cls = parts[3].strip() if len(parts) > 3 else ''
                kills = parts[4].strip() if len(parts) > 4 else '0'
                deaths = parts[5].strip() if len(parts) > 5 else '-'
                ping = parts[6].strip() if len(parts) > 6 else '-'
                # Map team numbers to names
                team_name = 'Rebels' if team == '2' else 'Joint Ops' if team == '1' else team
                if name and pid:
                    players.append({
                        'raw': line, 'id': pid, 'name': name,
                        'team': team, 'team_name': team_name, 'score': kills, 'class': cls,
                        'kills': kills, 'deaths': deaths, 'ping': ping
                    })
            elif len(parts) >= 2:
                pid = parts[0].strip()
                if pid == '0':
                    continue
                name = parts[1].strip()
                team = parts[2] if len(parts) > 2 else ''
                team_name = 'Rebels' if team == '2' else 'Joint Ops' if team == '1' else team
                players.append({'raw': line, 'id': pid, 'name': name,
                                'team': team, 'team_name': team_name,
                                'score': parts[3] if len(parts) > 3 else '',
                                'kills': parts[3] if len(parts) > 3 else '0',
                                'deaths': '-', 'ping': '-', 'class': ''})
        self.players = players
        if self._on_players:
            self._on_players(players)

    def _parse_missions(self, data):
        missions = [l.strip() for l in data.split('\n') if l.strip() and not l.startswith('#')]
        self.missions = missions
        if self._on_missions:
            self._on_missions(missions)

    def _parse_gamestate(self, data):
        self.game_state['raw'] = data
        lower = data.lower()
        if 'menu' in lower:
            self.game_state['mode'] = 'Menu'
        elif 'game' in lower or 'current state' in lower:
            self.game_state['mode'] = 'Game'
        # Extract "Current State = X" value
        for line in data.split('\n'):
            line = line.strip()
            if 'current state' in line.lower() and '=' in line:
                val = line.split('=', 1)[1].strip()
                self.game_state['mode'] = val
        if self._on_gamestate:
            self._on_gamestate(self.game_state)

    def _parse_settings(self, data):
        settings = {}
        self._key_case_map = {}  # lowercase -> original CamelCase for set commands
        for line in data.split('\n'):
            if '=' in line:
                k, _, v = line.partition('=')
                original = k.strip()
                lower = original.lower()
                settings[lower] = v.strip()
                self._key_case_map[lower] = original
        self.game_settings = settings
        wire_log(f"SETTINGS PARSED: {len(settings)} keys: {list(settings.keys())[:10]}")
        if self._on_settings:
            self._on_settings(settings)

    def _parse_chat(self, data):
        existing = {m['raw'] for m in self.chat_messages}
        for line in data.split('\n'):
            line = line.strip().rstrip('\r')
            if line and line not in existing:
                self.chat_messages.append({'time': time.strftime('%H:%M:%S'), 'text': line, 'raw': line})
        self.chat_messages = self.chat_messages[-500:]
        if self._on_chat:
            self._on_chat(self.chat_messages)

    def _parse_available_maps(self, data):
        self.available_maps_data = data
        if self._on_available_maps:
            self._on_available_maps(data)

    def _log(self, msg):
        if self._on_log:
            self._on_log(msg)

    def _on_server_disconnect(self, reason):
        """Handle server disconnect — log and notify UI."""
        self._log(f"Disconnected: {reason}")
        if hasattr(self, '_on_disconnect_ui') and self._on_disconnect_ui:
            self._on_disconnect_ui()
