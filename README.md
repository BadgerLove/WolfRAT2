# WolfRAT2

**Modern Remote Admin Tool for Joint Operations: Typhoon Rising**

A complete rewrite of the original WolfRAT v0.95 (2005) â€” rebuilt from scratch in Python 3 + PyQt6 with a dark theme UI, web dashboard, and features the original never had.

![Version](https://img.shields.io/badge/version-2.5.0-blue)
![Python](https://img.shields.io/badge/python-3.11--3.14-green)
![License](https://img.shields.io/badge/license-MIT-yellow)

## What Is This?

WolfRAT2 connects to a Joint Operations server's configured admin port
(commonly TCP 4000) and gives server admins a full GUI for managing players,
maps, chat, and server settings. No more typing commands into a console â€”
point, click, done.

"RAT" = **R**emote **A**dmin **T**ool. Not a backdoor. Just a better way to run your server.

## Features

### ðŸ–¥ï¸ Desktop GUI (12 Tabs)

| Tab | What It Does |
|-----|-------------|
| **Server** | Connection manager, saved servers, server status |
| **Console** | Raw command console with send/receive log |
| **Players** | Live player list with admin actions (Warn, Punt, Ban, Kill, Swap, Zero Score) |
| **Missions** | Map browser, one-click map switching, next map, mission presets |
| **Settings** | Server config â€” auto-balance, team switching, vote percent, respawn delay |
| **Chat Bot** | Live chat monitor, send chat, bad word filter, auto-team-swap trigger |
| **Messages** | Recurring messages, welcome messages, and persistent KD tracking |
| **Spree** | Kill streak tracker â€” auto-announces 3/5/7/10 kill streaks with gold heat gradient |
| **Mods** | TAC mod management and configuration |
| **Map Voting** | In-game map voting system |
| **Weapons** | Weapon loadout editor and configuration |
| **Web Admin** | Embedded dashboard listener, credentials, and access history |

### ðŸŒ Web Dashboard

Mobile-friendly web UI for remote administration from your phone or tablet. Runs on a configurable LAN port, token-protected. Same commands as the desktop UI.

### ðŸŽ¨ Dark Theme

Catppuccin Mocha-inspired dark theme with gold accents. Easy on the eyes during long sessions.

### ðŸ“¦ Single Executable

Packages into a single `.exe` via PyInstaller â€” no Python install needed on the target machine.

## Getting the Executable

Download the latest `WolfRAT2.exe` from [fmj-squad.com](https://fmj-squad.com/wolfra.html). Once installed, WolfRAT updates itself from the update button. Guides: [WolfRAT wiki](https://fmj-squad.com/wiki/).

## Building From Source

```bash
# Clone the repo
git clone https://github.com/BadgerLove/WolfRAT2.git
cd WolfRAT2

# Install the canonical runtime and build dependencies
python -m pip install --editable ".[dev]"

# Build
build.bat
```

Output: `dist\WolfRAT2.exe`

## Running From Source

```bash
python -m pip install --editable .
python main.py
```

For tests, linting, and executable builds, install the canonical development
extra:

```bash
python -m pip install --editable ".[dev]"
```

Python dependencies are declared only in `pyproject.toml`. Browser-client
validation uses the Node 24/npm 11 toolchain and canonical scripts declared in
`package.json`; it has no third-party npm packages to install.

Run the complete local validation:

```bash
python -m ruff check .
python -m pytest -q
npm run validate
python -m build
```

CI also builds a wheel and source distribution on Linux and Windows, installs
the wheel into a clean environment to verify its resources and entry point,
builds the committed `WolfRAT2.spec`, and runs the packaged executable through
its isolated smoke mode. The Windows artifact contains the executable, its
SHA-256 checksum, the MIT license, and the machine-readable smoke result.

Live retail conformance is a separate manual workflow for a dedicated JO
self-hosted runner. Before enabling it, create a GitHub environment named
`retail-conformance` with required reviewers, prevent self-review, restrict
deployment to the default branch, and define these environment secrets:

- `WOLFRAT_HOST`
- `WOLFRAT_PORT`
- `WOLFRAT_USERNAME`
- `WOLFRAT_PASSWORD`

The runner must carry the `self-hosted`, `windows`, `x64`, and `jotac-retail`
labels and be current enough to run the Node-based GitHub Actions runtime.
Credentials are exposed only to the conformance command, never dependency
installation or pull-request code.

## Requirements

- **Node.js 24 with npm 11** (only for browser-client validation)
- **Python 3.11â€“3.14** (for building/running from source)

Runtime and development dependencies are declared in `pyproject.toml`; Node
toolchain requirements and browser-client commands are declared in
`package.json`.

## How It Works

WolfRAT2 connects to the Joint Operations admin interface over TCP (port 4000 by
convention; the server reads `remote_admin_port` from `game.cfg`) using the same
protocol as the original WolfRAT v0.95 (2005).

Login is a challenge/response exchange: the server sends a framed 32-byte
challenge plus NUL, and the client returns an encrypted 65-byte response holding
the username and password fields from `admin.cfg`. Both are checked. Everything
after login is plaintext ASCII.

One mandatory session owns framing, authentication, request ordering, and the
authoritative snapshots. Every desktop and web feature uses typed semantic
operations through that session; background reads are coalesced and cannot race
interactive commands. Semantic mutations retain the workflow gate through their
confirmation readback, so a later mutation cannot act on an identity that the
first operation shifted. Mutations require their exact retail acknowledgement
and, where available, an independent state readback before WolfRAT reports them
as verified.

The connection is kept persistent. Teardown uses an abortive TCP close because
the retail server fails to retire orderly-FIN admin clients correctly; repeated
orderly reconnects can corrupt its retained client table. Accepted raw mutations
conservatively invalidate typed snapshots before any queued command can reuse an
old player, mission, or weapon identity.

The wire contract and command catalog were cross-checked against retail
`CAdminServer` behavior in IDA and the OpenNova implementation.

## History

The original **WolfRAT** was built in 2005 by WolfGaming using MFC70 (Visual C++ .NET). It served the JO community well but is long dead â€” won't run on modern Windows, source is gone.

**WolfRAT2** is a from-scratch rebuild. Same protocol, same purpose, modern everything.

## License

MIT â€” do what you want with it.

## Credits

- **WolfGaming** â€” original WolfRAT v0.95 (2005)
- **BadgerLove** â€” WolfRAT2 rewrite
- **NovaLogic** â€” Joint Operations: Typhoon Rising (2004)
