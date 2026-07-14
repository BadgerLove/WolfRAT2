"""
B-Stats — Anonymous Usage Analytics
====================================
Drop this file into any FMJ tool. Call `bstats_start("wolfrat", "2.2")` on launch.
Sends a startup ping + periodic heartbeats. Fails silently. Zero dependencies.

Usage:
    import bstats
    bstats.bstats_start("wolfrat", "2.2")

That's it. Runs in a background daemon thread. Won't block or crash your app.
"""

import json
import platform
import uuid
import os
import threading
import time
import urllib.request
import urllib.error

BSTATS_URL = "http://fmj-squad.com/bstats/ping"
HEARTBEAT_INTERVAL = 30 * 60  # 30 minutes
TIMEOUT = 5  # seconds

_version = "0.0"
_tool = "unknown"
_os = "unknown"
_client_id = "unknown"
_running = False


def _get_os():
    """Get a clean OS string."""
    try:
        system = platform.system()
        if system == "Windows":
            ver = platform.version()
            # Windows 10 → "10.0.xxxx", Windows 11 → "10.0.22000+"
            build = int(ver.split(".")[-1]) if "." in ver else 0
            if build >= 22000:
                return "Windows 11"
            return "Windows 10"
        return system
    except Exception:
        return "unknown"


def _get_client_id():
    try:
        appdata = os.getenv("APPDATA") or os.path.expanduser("~")
        fmj_dir = os.path.join(appdata, "FMJSquad")
        os.makedirs(fmj_dir, exist_ok=True)
        cid_file = os.path.join(fmj_dir, "bstats_id.txt")
        if os.path.exists(cid_file):
            with open(cid_file, "r") as f:
                return f.read().strip()
        new_id = str(uuid.uuid4())
        with open(cid_file, "w") as f:
            f.write(new_id)
        return new_id
    except:
        return str(uuid.uuid4())

def _ping(ping_type="heartbeat"):
    """Send a single ping. Fails silently."""
    try:
        data = json.dumps({
            "tool": _tool,
            "version": _version,
            "os": _os,
            "client_id": _client_id,
            "type": ping_type
        }).encode("utf-8")
        req = urllib.request.Request(
            BSTATS_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=TIMEOUT)
    except Exception:
        pass  # Silent fail — never crash the host app


def _heartbeat_loop():
    """Background loop that sends heartbeats every 30 minutes."""
    while _running:
        time.sleep(HEARTBEAT_INTERVAL)
        if _running:
            _ping("heartbeat")


def bstats_start(tool, version):
    """
    Start B-Stats tracking. Call once on app launch.
    
    Args:
        tool: Tool name — "wolfrat" or "jomonitor"
        version: Version string — "2.2", "1.0.1", etc.
    """
    global _tool, _version, _os, _client_id, _running
    _tool = tool.lower()
    _version = version
    _os = _get_os()
    _client_id = _get_client_id()
    _running = True

    # Startup ping in background (non-blocking)
    t = threading.Thread(target=_ping, args=("startup",), daemon=True)
    t.start()

    # Heartbeat loop in background
    hb = threading.Thread(target=_heartbeat_loop, daemon=True)
    hb.start()


def bstats_stop():
    """Stop B-Stats tracking. Call on app shutdown (optional — daemon threads die anyway)."""
    global _running
    _running = False
