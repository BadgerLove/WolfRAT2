# WolfRAT 2.0 — Code Review (2026-05-21)

Dale asked for a slow, thorough review. No rushing. One issue at a time.

---

## Bug 1: Available Maps Not Showing in UI

**Symptom:** Click "Load Available Maps from Server" → wire log shows map names being received → but the Available Maps list widget stays empty.

**Root Cause Chain:**

1. The command queue (`_cmd_queue`) classifies responses by popping entries in order. If ANY response arrives out of order (e.g., a polling gamestate response arrives before the available maps response), the wrong parser gets called.

2. `_parse_available_maps()` just passes data to a callback. It does NOT store the data in `self.available_maps` (unlike `_parse_players`, `_parse_chat`, etc. which all store their data). If the signal isn't connected or fires before the UI is ready, data is gone forever.

3. The fallback keyword detection in `_handle_packet()` has NO handler for `'available'` maps. If the queue is empty or desynced, available maps responses fall through to keyword matching and get misclassified.

4. `update_available_maps()` parses lines looking for `"0. MAPNAME.bms (Description)"` format. If the JO server sends a slightly different format (e.g., no numbers, different spacing), nothing gets parsed.

**Fix (3 parts, do them one at a time):**

### Fix 1a: Store available maps in ServerManager
In `_parse_available_maps()`, store the data so it can be re-fetched:
```python
def _parse_available_maps(self, data):
    self.available_maps_data = data  # <-- ADD THIS
    if self._on_available_maps:
        self._on_available_maps(data)
```
Also add `self.available_maps_data = ""` in `__init__`.

### Fix 1b: Add fallback keyword detection
In `_handle_packet()`, add after the existing fallbacks:
```python
elif '.bms' in lower or 'available' in lower:
    self._parse_available_maps(payload)
```

### Fix 1c: Add debug logging to update_available_maps
In `MissionsTab.update_available_maps()`, add logging so we can see what data arrives:
```python
def update_available_maps(self, data: str):
    print(f"AVAILABLE MAPS DATA: {data[:500]}")  # <-- ADD THIS
    # ... rest of method
```

---

## Bug 2: Maps Not Rotating

**Symptom:** Saved rotation doesn't apply on connect. Or rotation list stays empty.

**Root Cause Chain:**

1. `auto_apply_rotation()` is called via `QTimer.singleShot(3000, ...)` after connect. It runs ON THE MAIN THREAD with `time.sleep()` calls. This freezes the GUI for several seconds.

2. `update_missions()` has a guard: `if self._rotation_maps: return` — it only populates the rotation list from server data if the list is EMPTY. But `auto_apply_rotation()` sets `_rotation_maps` before the server's `mission list` response arrives. So if the server response arrives AFTER auto-apply, it gets ignored.

3. The polling loop (`_poll_loop`) NEVER requests `mission list`. It only polls gamestate, players, and chat. So the rotation is only fetched once on connect — if it fails or is empty, it never retries.

**Fix (2 parts, do them one at a time):**

### Fix 2a: Add mission list to polling
In `_poll_loop()`, add mission list polling:
```python
def _poll_loop(self, interval):
    self._log(f"Polling every {interval}s")
    while self._polling and self.proto.connected:
        time.sleep(interval)
        if self._polling and self.proto.connected:
            self.send("get gamestate", quiet=True)
            time.sleep(0.3)
            self.send("player list", quiet=True)
            time.sleep(0.3)
            self.send("chat get", quiet=True)
            time.sleep(0.3)
            self.send("mission list", quiet=True)  # <-- ADD THIS
    self._log("Polling stopped")
```

### Fix 2b: Fix update_missions to always update current map
The current map marker should update even when rotation list is populated. Change the guard logic:
```python
def update_missions(self, missions: list):
    # Always update current map marker
    for m in missions:
        if '<CURRENT MISSION>' in m:
            name = m.split(' - ')[0].strip() if ' - ' in m else m.strip()
            if ':' in name[:5]:
                name = name.split(':', 1)[1].strip()
            self.current_label.setText(name)
            break

    # Only populate rotation list on first load
    if self._rotation_maps:
        return

    # ... rest of population logic stays the same
```

---

## Bug 3: Buttons Appear Unresponsive

**Symptom:** Clicking buttons (especially Connect, Refresh, admin actions) causes the UI to freeze/hang.

**Root Cause:**

All server operations run synchronously on the main GUI thread. When you click "Connect":
1. `server.connect()` does TCP handshake (up to 10s timeout)
2. Then sends challenge/response auth
3. Then calls `refresh_all()` which sends 5 commands with `time.sleep(0.2)` between each
4. Then starts polling

Total freeze time: up to ~15 seconds on connect. During this time, ALL buttons are unresponsive because Qt's event loop is blocked.

Similarly, admin actions (warn, punt, ban, kill) call `time.sleep()` between commands, freezing the UI for 0.3-0.5s each time.

**The fix for this is threading — but it's a bigger change. Do NOT attempt this in the same pass as Bug 1 and Bug 2.**

**Short-term mitigation:** At minimum, move `refresh_all()` and `auto_apply_rotation()` off the main thread.

---

## Bug 4: Duplicate Files

`source_code/app.py` and `source_code/wolfrat/app.py` are IDENTICAL (diff confirms).
Same for `source_code/protocol.py` and `source_code/wolfrat/protocol.py`.

The entry point `main.py` imports from `wolfrat.app`, so the `wolfrat/` versions are the ones that run.
The top-level copies are dead weight. Can be cleaned up later.

---

## Bug 5: _process_buffer Fallback is Dangerous

In `protocol.py`, the buffer parser has a fallback that tries to interpret the first 4 bytes as a big-endian length:
```python
test_len = struct.unpack('!I', self._buffer[0:4])[0]
if 8 <= test_len <= 65536:
    found = True
```

This is dangerous — it uses `!I` (big-endian) while the actual protocol uses `<I` (little-endian). If the first 4 bytes happen to look like a valid big-endian length, the parser will misinterpret the packet.

**Fix:** Remove this fallback entirely. If we can't find a valid header with CR+LF, skip the byte and move on.

---

## Priority Order

1. **Bug 1** (Available Maps) — Most visible, Dale's main complaint
2. **Bug 2** (Map Rotation) — Second complaint
3. **Bug 3** (Buttons) — Bigger change, do after 1+2 are proven working
4. **Bug 5** (Buffer parser) — Cleanup, low risk
5. **Bug 4** (Duplicates) — Cosmetic

---

## What NOT To Do

- Don't rewrite the whole file
- Don't add threading in the same pass as bug fixes
- Don't change the encryption (it works)
- Don't change the GUI layout or styling
- Don't "improve" things that work
- Fix one bug, test it, THEN move to the next
