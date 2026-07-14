WolfRAT 2.0 — Joint Operations Server Admin Tool
==================================================

A modern replacement for the original WolfRAT v0.95 (2005).

Features:
  - Server connection manager with saved servers
  - Live player list with admin actions (Warn, Punt, Ban, Kill, Swap, Zero)
  - Mission/map browser with one-click map switching
  - Server settings manager (auto-balance, team switching, votes)
  - Chat bot with bad word filtering and auto-team-swap trigger
  - Modern dark theme UI
  - Packaged as a single Windows .exe

Building from source:
  1. Run: build.bat
  2. Output: dist\WolfRAT2.exe

Running from source:
  1. venv\Scripts\activate
  2. python main.py

Requirements:
  - Python 3.11+
  - PyQt6
  - pyinstaller (for building .exe)

Protocol:
  Connects to Joint Operations game servers on TCP port 40000.
  Uses the same plaintext protocol as the original WolfRAT v0.95.
  Username-only authentication (password not required by the game server).

Based on reverse engineering of WolfRAT v0.95 binary.
