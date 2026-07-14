# WolfRAT 2.1.1 — Instructions

## What Is This?

WolfRAT 2.1.1 is a remote admin tool for **Joint Operations: Typhoon Rising** dedicated servers. It connects to the server's admin port (default 4000) and lets you manage players, maps, settings, and chat from a Windows GUI.

It replaces the original WolfRAT v0.95 from 2005.

---

## Changelog

### 2.1.1 (June 2026)
- **FIXED: Chat duplicate filter blocking repeated commands.** The old filter used raw text matching, so `!switch` would only work once per session. Replaced with sequence overlap detection — commands now work every time they're typed.
- `protocol.py`: Changed from set-based dedup to chunk overlap detection.
- `app.py`: Tracks messages by unique ID instead of raw text.

### 2.1 (June 2026)
- Killing spree announcer (3/5/7/10 kill streaks)
- Recurring message timers (30/45/60 min intervals)
- Auto-reconnect on server drop (15s polling loop)
- Ghost connection timeout (60s)
- Custom WolfRAT icon

---

## Building the Executable

### Requirements
- Windows 10/11
- Python 3.14+ installed
- Internet connection (for pip)

### Build Steps
1. Open PowerShell
2. Navigate to the `source_code` folder:
   ```
   cd "C:\Users\YOUR_USERNAME\Desktop\WolfRAT2_Handoff\source_code"
   ```
3. Run the build script:
   ```
   .\build.bat
   ```
4. The executable will be at `dist\WolfRAT2.exe`

### First-Time Setup
If `pyinstaller` is not found, the build script uses `python -m PyInstaller` to avoid PATH issues. This should work out of the box.

If you get dependency errors, run:
```
pip install PyQt6 pyinstaller
```

---

## What's New in 2.1 (June 7, 2026)
- **Auto-Reconnect**: Added 60s timeout for ghost connections and a 15s auto-reconnect loop to keep the tool hooked to the server automatically.
- **Killing Spree Announcer**: Automatically tracks player streaks (3, 5, 7, 10 kills without dying) and broadcasts custom rampage messages to the server chat.
- **Recurring Timers**: Added 30, 45, and 60-minute interval options for recurring chat messages.
- **Custom Icon**: Added a sleek black-and-gold rat/wolf silhouette icon to the executable.

---

## Running WolfRAT 2.1

### From the Executable
Double-click `dist\WolfRAT2.exe`

### From Source
```
cd source_code
python main.py
```

---

## Server Connection

### Prerequisites
- JO dedicated server running (`jotacserver.exe`)
- Admin configured in `admin.cfg` (format: `username, password, access_level`)
- Server listening on port 4000 (configured in `game.cfg`)
- Server needs ~115 seconds to fully initialize after starting

### Connecting
1. Enter the server IP address
2. Port: 4000 (default)
3. Username: from your `admin.cfg`
4. Password: from your `admin.cfg`
5. Click **Connect**

### Status Bar
- **Green bar** = connected, pulsing
- **Red bar** = disconnected
- Shows current map and game mode
- Shows feedback when settings are changed

---

## Tabs

### 🖥 Server
- Connection settings (IP, port, username, password)
- Server status (name, game mode, player count)
- Save/load server profiles
- Connection log

### 👥 Players
- Player list (excludes host ID 0)
- Shows: Name, Team (Joint Ops / Rebels), Class, Kills, Deaths, Ping
- Admin actions: Warn, Punt (Kick), Ban, Kill, Swap Team, Zero Score
- Custom message field for warns/kicks
- Team balance display

### 🗺 Missions
- **Map Rotation** (left side): Maps currently on the server
  - Drag-and-drop to reorder
  - Right-click for context menu:
    - Switch to map
    - Play Once / Play Twice
    - Remove from rotation
  - Remove, Move Up, Move Down buttons
- **Available Maps** (right side): All maps on the server
  - Double-click to add to rotation
  - Search and filter by game mode
- **Set Next Map**: Skip to next map in rotation
- **Presets**: Save/load rotation presets

### ⚙ Settings
- **Game Settings**: Checkboxes for Friendly Fire, Auto Rebalance, Tracers, Fat Bullets, One Shot Kills, Tags, Friendly Tags, Allow Team Switching
- **Limits & Timers**: Sliders for Kill Limit, Time Limit, King of the Hill, Armoury Timer, Max Kills, Vote Kick Percent, Team Switch Interval
- **Time of Day**: Set hour (0100–2400) and game pass duration (5–120 min)
- **Passwords & Title**: Dropdown to select Server Password / Side A Password / Side B Password / Server Title, with Set and Clear buttons
- **Custom Command**: Send raw commands to the server
- All settings auto-apply on change (no Apply button needed)
- Feedback shown in status bar when settings change

### 💬 Chat Bot
- Live chat display
- Send messages to all players
- Recurring message system (auto-broadcasts messages on a timer)
- Welcome message for new players
- Auto team swap on chat trigger word

### 📢 Messages
- Send server-wide messages
- Configure recurring messages

---

## Chat Message Limit

JO server has a **69-character limit** on chat messages. WolfRAT enforces this in the GUI and protocol. Messages longer than 69 characters are truncated with "...".

---

## Map Rotation

### How It Works
- Maps are stored on the JO server in `game.cfg`
- WolfRAT reads the server's current rotation via `mission list`
- You can add/remove/reorder maps through the GUI
- Changes are sent to the server immediately via `mission add` / `mission remove` / `mission clear`

### Commands Used
- `mission list` — Get current rotation from server
- `mission available` — Get all available maps
- `mission add <filename>` — Add map to rotation
- `mission remove <filename>` — Remove map from rotation
- `mission clear` — Clear entire rotation
- `mission <filename>` — Switch to specific map immediately
- `cmd mission next` — Skip to next map in rotation

### Presets
- Rotation is auto-saved when you add/remove maps
- Load saved presets from the dropdown
- Presets are stored in `wolfrat_rotations.json` next to the executable

---

## Protocol

WolfRAT uses JO's encrypted admin protocol over TCP on port 4000.

### Packet Format
- 8-byte header: `[length(4)] [reversed(2)] [0x0D 0x0A]`
- Payload: ASCII command string, null-terminated

### Login Flow
1. Connect TCP to server port 4000
2. Server sends challenge (41 bytes)
3. Client sends encrypted auth (73 bytes)
4. Server responds with "OK - User: <username> successfully logged in."

### Encryption
Reverse-engineered from original WolfRAT.exe. See `02_ENCRYPTION_ALGORITHM.txt` for details. Confirmed working (65/65 byte match against captured traffic).

---

## Troubleshooting

### "Login failed"
- Check username/password in `admin.cfg`
- Server must be fully initialized (~115 seconds after start)
- Check `admin_log.txt` on the server

### No players showing
- Make sure you're connected (green bar at bottom)
- Click Refresh on the Server tab
- Check if the server has players connected

### Map switching doesn't work
- `mission <filename>` switches to a specific map
- `cmd mission next` skips to next in rotation
- Some commands may not work on all JO server versions

### Chat not showing
- WolfRAT polls for chat every 5 seconds
- Chat messages are fetched via `chat get`
- Check the connection log for errors

### Build fails
- Make sure Python 3.14+ is installed
- Run `pip install PyQt6 pyinstaller` manually
- Use `python -m PyInstaller` instead of `pyinstaller` if PATH issues

---

## File Locations

### On the Build Machine
- `source_code/main.py` — Entry point
- `source_code/wolfrat/app.py` — Main GUI application
- `source_code/wolfrat/protocol.py` — Server protocol and encryption
- `source_code/build.bat` — Build script
- `dist/WolfRAT2.exe` — Built executable

### Runtime Files (next to executable)
- `wolfrat_rotations.json` — Saved map rotation presets
- `wolfrat_servers.json` — Saved server profiles
- `wolfrat_wire.log` — Protocol debug log

### JO Server Files
- `C:\GAMES\JOTAC\Game\JO\jotacserver.exe` — Server executable
- `C:\GAMES\JOTAC\Game\JO\admin.cfg` — Admin credentials
- `C:\GAMES\JOTAC\Game\JO\game.cfg` — Server configuration
- `C:\GAMES\JOTAC\Game\JO\matchlogs\` — Match history

---

## Known Limitations

1. **Play count (x2)** — Display only. JO server doesn't have a `setrepeat` command. The actual repeat behavior is controlled by the server's rotation config.
2. **Command queue** — Responses may get misclassified if polling and UI commands overlap. This is a fundamental limitation of the synchronous design.
3. **No real-time chat push** — Chat is polled every 5 seconds, not streamed. There may be a delay.
4. **Armoury options** — Not implemented in the GUI. Use Custom Command for now.
5. **Minimum ping check** — Not implemented. Use Custom Command: `set minPing <value>`.
6. **Side passwords** — May not be supported by all JO server versions.

---

## Theme

WolfRAT 2.1 uses an OLED Black + Yellow theme:
- Background: pure black (#000000)
- Primary text: gold (#e8c840)
- Accents: dark yellow (#6a6a20, #8a7a20)
- Danger: red (#ff6040)
- Success: green (#50ff50)
- Buttons: 3D raised/depressed effect with border inversion on press

---

*WolfRAT 2.1 — Built for the Joint Operations community.*
*Original WolfRAT v0.95 (2005) by the Archon team.*
