"""The phone page's way into the WolfRAT window (everything here runs on the Qt thread).

Two jobs:

* **Actions.** ``WolfWebServer.on_desktop = bridge.submit``.  The web thread
  calls ``submit(action, payload)``, which only emits one queued signal and
  returns a ``concurrent.futures.Future``; the window's thread runs the action
  through the desktop's own code (the Mods tab's kick/ban/warn path with its
  log line and announcement, the Bans tab's list, the Weather tab's
  ``_start``, the Sky page's scene buttons, the lag comp panel's own box and
  list, Manual mode's switch, the Missions tab's saved lists) and settles the
  Future with the result dict the phone gets.
* **Snapshot.** What only the window knows (game type, what decides the
  match, the vote, weather, the sky, lag comp, career ranks, countries, time
  on the server, what this WolfRAT can do) is built here and published whole
  as ``WolfWebServer.desktop_state``; the web thread only ever reads that object.

The phone's "time left on this map" is for one round: after every poll
``check_time_restore`` puts the round length back once that round is over
(web_timeleft.py).

Result dicts: ``{"ok", "accepted", "replies", "error"?, "status"?}``;
"status" is the HTTP status the web server should use (400 bad input, 409
not possible right now).
"""

from __future__ import annotations

import time
from dataclasses import replace

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal

from wolfrat import coop_guard
from wolfrat import lag_comp
from wolfrat import manual_mode
from wolfrat import sky
from wolfrat import weather
from wolfrat import web_map_lists
from wolfrat import web_state
from wolfrat import web_timeleft
from wolfrat.ban_rules import KIND_IP
from wolfrat.map_timeline import MapClock
from wolfrat.protocol import wire_log

# "[ADMIN] " goes in front of a private line, as in front of phone chat, so the
# player can see an admin wrote it.  One helper line carries 118 characters
# (mycmd.LINE_LIMIT), colour codes included.
PHONE_PREFIX = "[ADMIN] "
PRIVATE_LINE_LIMIT = 118
# A player back within 10 minutes is on the same visit (private_welcome.REVISIT_SECONDS).
REVISIT_SECONDS = 600
# Lag comp's auto-tune names on the phone, as lag_comp.PT_MODE_NAMES says them.
TUNE_BY_NAME = {name: mode for mode, name in lag_comp.PT_MODE_NAMES.items()}
# How soon after a burst of signals (one poll) the snapshot is rebuilt.
PUBLISH_DELAY_MS = 150
# A round-time change of ours that the server has not answered after this long
# no longer holds up the next one.
TIME_BUSY_SECONDS = 30.0


def _settle(answer, value):
    """Give the phone its answer (once; the web side may have stopped waiting)."""
    if answer.done():
        return
    try:
        answer.set_result(value)
    except Exception:          # cancelled by the web side's timeout meanwhile
        pass


def _fail(message, status=None, **extra):
    result = {"ok": False, "accepted": False, "replies": [], "error": str(message), **extra}
    if status is not None:
        result["status"] = status
    return result


def _done(*replies, **extra):
    return {"ok": True, "accepted": True, "replies": [str(r) for r in replies if r], **extra}


def _now():
    return time.strftime("%H:%M:%S")


def _minutes(count):
    return f"{count} minute" if count == 1 else f"{count} minutes"


def _ban_lengths():
    from wolfrat.bans_tab import EXPIRY_CHOICES
    return [{"id": code, "label": label[:1].upper() + label[1:]}
            for label, code in EXPIRY_CHOICES]


def _weather_presets():
    """The desktop's "Weather now" buttons, emoji stripped, Clear first."""
    from wolfrat.weather_tab import _PRESET_BUTTONS
    return [{"id": "clear", "label": "Clear"}] + [
        {"id": key, "label": label.split(" ", 1)[-1]} for key, label in _PRESET_BUTTONS
    ]


def _held_name(sky) -> str:
    """'Storm', 'Fog 80 m', 'Rain with 200 m fog', 'Dry sky', else describe()."""
    if sky.dry:
        return "Dry sky"
    plain = replace(sky, fog_metres=None)
    for key, preset in weather.PRESETS.items():
        if key == "clear" or replace(preset, fog_metres=None) != plain:
            continue
        if key == "fog":
            return f"Fog {sky.fog_metres} m" if sky.fog_metres is not None else "Fog"
        if sky.fog_metres is not None and sky.fog_metres != preset.fog_metres:
            return f"{key.capitalize()} with {sky.fog_metres} m fog"
        return key.capitalize()
    text = sky.describe()
    return text[:1].upper() + text[1:]


class PhoneBridge(QObject):
    """Runs the phone's desktop actions and publishes the desktop snapshot."""

    # action, payload, Future: the only way in from the web thread.
    _request = pyqtSignal(str, object, object)

    def __init__(self, window, web_server, parent=None):
        super().__init__(parent)
        self._window = window
        self.ws = web_server
        self._clock = MapClock()           # its own: the Sky page's one changes on every call
        self._visit_start = {}             # name.casefold() -> when this visit began
        self._visit_seen = {}              # name.casefold() -> last time in the player list
        self._problems_logged = set()
        # The round length to put back after the phone set the time left.
        self._time = web_timeleft.TimeRestore(self._settings_file(web_timeleft.FILE_NAME))
        self._time_busy_until = 0.0        # a round-time change of ours is on its way
        self._monotonic = time.monotonic
        self._request.connect(self._run, Qt.ConnectionType.QueuedConnection)
        self._publish_timer = QTimer(self)
        self._publish_timer.setSingleShot(True)
        self._publish_timer.setInterval(PUBLISH_DELAY_MS)
        self._publish_timer.timeout.connect(self.publish)

    # --- wiring ------------------------------------------------------------

    def attach(self):
        self.ws.on_desktop = self.submit

    def submit(self, action, payload):
        """Web thread: queue one action for the window's thread."""
        from concurrent.futures import Future
        answer = Future()
        self._request.emit(str(action), dict(payload or {}), answer)
        return answer

    def _get(self, name):
        return getattr(self._window, name, None)

    def _settings_file(self, filename):
        """A file next to WolfRAT's other settings, or None (no settings folder)."""
        runtime = self._get("runtime")
        try:
            return runtime.path(filename) if runtime is not None else None
        except Exception:
            return None

    @property
    def _server(self):
        return self._window.server

    @staticmethod
    def _who(payload):
        device = " ".join(str(payload.get("device_name") or "").split())[:40]
        return f"Web Admin ({device})" if device else "Web Admin"

    def _log_once(self, key, text):
        if key not in self._problems_logged:
            self._problems_logged.add(key)
            wire_log(text)

    # --- actions -----------------------------------------------------------

    def _run(self, action, payload, answer):
        handlers = {
            "kick": self._kick,
            "ban": self._ban,
            "warn": self._warn,
            "message": self._message,
            "weather": self._weather,
            "lagcomp": self._lagcomp,
            "mode": self._mode,
            "sky": self._sky,
            "timeleft": self._timeleft,
            "maplist": self._maplist,
        }
        handler = handlers.get(action)
        try:
            result = (_fail(f"Unknown action: {action}", 400) if handler is None
                      else handler(payload, answer))
        except Exception as error:         # the phone gets the reason, WolfRAT carries on
            wire_log(f"[WEB] {action} from the phone failed: {error}")
            result = _fail(str(error))
        if result is not None:
            _settle(answer, result)
        self.schedule_publish()

    def _mod_action(self, answer, operation, *, context, announcement=None,
                    log_message, success_reply, failure_extra=None, event=None, after=None):
        """Through the Mods tab's own path (as !kick / !ban / !warn): the Mod
        activity line, then the announcement (and the page's alert, ``event``)
        once the server confirmed.  ``after()`` runs first, whichever way it went."""
        mods = self._get("mods_tab")
        if mods is None:
            return _fail("The WolfRAT window is still starting", 503)

        def succeeded(_result):
            if after is not None:
                after()
            _settle(answer, _done(success_reply))
            self.schedule_publish()

        def failed(message):
            if after is not None:
                after()
            _settle(answer, _fail(message, **(failure_extra or {})))
            self.schedule_publish()

        mods._submit_mod_action(
            operation,
            context=context,
            announcement=announcement,
            log_message=log_message,
            on_success=succeeded,
            on_failure=failed,
            event=event,
        )
        return None                        # settled by the callbacks

    def _kick(self, payload, answer):
        target_entry = payload["entry"]
        name = target_entry.name
        reason = payload.get("reason") or "Kicked by admin"
        who = self._who(payload)
        return self._mod_action(
            answer,
            lambda: self._server.punt_player(target_entry, reason),
            context=f"Kick {name}",
            announcement=f"{name} was kicked by an admin",
            log_message=f"[{_now()}] {who} kicked {name}: {reason}",
            success_reply=f"{name} was kicked",
            event=("kick", name, who),
        )

    def _ban(self, payload, answer):
        """WolfRAT's own ban list (name + IP), like the Bans tab's Ban button and
        !ban, then the kick.  JO's own PLAYER BAN (a CD-key id) is never used."""
        bans = self._get("bans_tab")
        if bans is None or self._get("mods_tab") is None:
            return _fail("This WolfRAT has no ban list", 409)
        lengths = {item["id"]: item["label"] for item in _ban_lengths()}
        expiry = str(payload.get("expiry") or "")
        if expiry not in lengths:
            return _fail("Pick a ban length from the list", 400)
        target_entry = payload["entry"]
        name = target_entry.name
        reason = payload.get("reason") or "Banned by admin"
        who = self._who(payload)
        added = bans.ban_now(name, reason=reason, added_by=who, kind="both", expiry=expiry)
        if added:
            # The next poll must not punt (and announce) them a second time.
            bans.enforcer.note_punted(name, bans.ip_of(name), bans._clock())
        what = ("name and IP" if any(entry.kind == KIND_IP for entry in added)
                else "name only, no IP known")
        until = "for ever" if not expiry else f"for {lengths[expiry].lower()}"
        reply = f"{name} banned ({what}, {until})"
        if manual_mode.quiet():
            reply += ". Manual mode: this WolfRAT won't keep them out by itself"
        return self._mod_action(
            answer,
            lambda: self._server.punt_player(target_entry, reason),
            context=f"Ban {name}",
            announcement=f"{name} was banned by an admin",
            log_message=f"[{_now()}] {who} banned {name}: {reason}",
            success_reply=reply,
            failure_extra={"replies": [f"{name} is on the ban list ({what}); "
                                       "the kick was not confirmed"]},
            event=("ban", name, who),
        )

    def _warn(self, payload, answer):
        target_entry = payload["entry"]
        name = target_entry.name
        reason = payload.get("reason") or "You have been warned"
        try:
            reason.encode("ascii")
        except UnicodeEncodeError:
            return _fail("Game chat only takes plain letters, numbers and punctuation", 400)
        who = self._who(payload)
        return self._mod_action(
            answer,
            lambda: self._server.warn_player(target_entry, reason),
            context=f"Warn {name}",
            log_message=f"[{_now()}] {who} warned {name}: {reason}",
            success_reply=f"Warning sent: WARNING to {name}: {reason}",
            event=("warn", name, who),
        )

    def _private_ready(self):
        send = getattr(self._server, "_private_line", None)
        ready = getattr(self._server, "_private_line_ready", None)
        try:
            return bool(send and ready and ready())
        except Exception:
            return False

    def _message(self, payload, answer):
        """One private line to one player, through the server's helper."""
        target_entry = payload["entry"]
        name = target_entry.name
        if not self._private_ready():
            return _fail("Private messages need WolfRAT on the server's PC with its "
                         "helper running", 409)
        text = " ".join(str(payload.get("message") or "").split())
        if not text:
            return _fail("Type a message", 400)
        line = PHONE_PREFIX + text
        room = PRIVATE_LINE_LIMIT - len(PHONE_PREFIX)
        if len(line) > PRIVATE_LINE_LIMIT:
            return _fail(f"Too long: {room} characters at most", 400)
        try:
            line.encode("latin-1")
        except UnicodeEncodeError:
            return _fail("It has letters the game can't show", 400)
        if not self._server._private_line(name, line):
            return _fail(f"Not sent: {name} is not on the server, or the helper is busy", 409)
        who = self._who(payload)
        wire_log(f"[WEB] private line to {name} from {who}: {text[:100]}")
        mods = self._get("mods_tab")
        if mods is not None and hasattr(mods, "mod_log"):
            mods.mod_log.addItem(f"[{_now()}] {who} sent {name} a private line: {text}")
        return _done(f"Sent to {name} only")

    def _weather(self, payload, answer):
        """Weather through the Weather tab's own code (as its buttons and !storm)."""
        tab = self._get("weather_tab")
        if tab is None:
            return _fail("Weather is not available", 409)
        preset = payload.get("preset")
        fog = payload.get("fog_m")
        minutes = payload.get("minutes")
        dynamic = payload.get("dynamic")
        lightning = payload.get("lightning")
        if preset is not None and (not isinstance(preset, str) or preset not in weather.PRESETS):
            return _fail("Unknown weather", 400)
        if fog is not None:
            if isinstance(fog, bool) or not isinstance(fog, (int, float)):
                return _fail("fog_m must be a number of metres", 400)
            fog = max(weather.CHAT_FOG_MIN, min(weather.CHAT_FOG_MAX, int(round(fog))))
        if preset == "clear" and fog is not None:
            return _fail("Clear has no fog distance", 400)
        if minutes is not None and (isinstance(minutes, bool) or not isinstance(minutes, int)
                                    or not 0 <= minutes <= weather.MAX_MINUTES):
            return _fail(f"minutes must be 0-{weather.MAX_MINUTES} (0 = until cleared)", 400)
        if dynamic is not None and not isinstance(dynamic, bool):
            return _fail("dynamic must be true or false", 400)
        if lightning is True:
            lightning = "near"
        if lightning is not None and lightning is not False and lightning not in ("near", "far"):
            return _fail('lightning must be "near" or "far"', 400)
        sky_change = preset is not None or fog is not None
        if not sky_change and dynamic is None and not lightning:
            return _fail("Pick a weather, a fog distance, changing weather or lightning", 400)
        if (sky_change or lightning) and tab._controller is None:
            return _fail(tab._last_problem or "Weather needs WolfRAT on the server's PC", 409)

        who = self._who(payload)
        replies = []
        if dynamic is not None:
            page = tab.dynamic_page
            if page.enabled_cb.isChecked() != dynamic:
                page.enabled_cb.setChecked(dynamic)     # the tab saves, logs and polls
                tab.log(f"{who} switched the changing weather {'on' if dynamic else 'off'}.")
            replies.append(f"Changing weather {'on' if dynamic else 'off'}.")
        if sky_change:
            if preset == "clear":                       # what the Clear button does
                tab._dynamic.end_front_now()
                tab._stop_timed_weather()
                ok = tab._start(weather.CLEAR, "clear", None, who)
                text = "Clearing: the map's own sky is coming back."
            else:
                name = preset or "fog"                  # fog_m alone = !fog 80m
                sky = weather.PRESETS[name]
                if fog is not None:
                    sky = replace(sky, fog_metres=fog)  # preset + fog_m = !storm 80m
                hold = (tab.minutes_spin.value() if minutes is None else minutes) or None
                ok = tab._start(sky, name, hold, who)   # _start polls by itself
                label = (f"Fog {fog} m" if name == "fog" and fog is not None
                         else name.capitalize() + (f" with {fog} m fog" if fog is not None else ""))
                text = f"{label} " + (f"for {hold} min." if hold else "until cleared.")
            if not ok:
                return _fail(tab._last_problem or "Weather needs WolfRAT on the server's PC",
                             409, replies=replies)
            replies.append(text)
        if lightning:
            if not tab._flash(who, far=lightning == "far"):
                return _fail("Lightning is not set up on this server (Weather tab: link "
                             "WolfRAT to the server)", 409, replies=replies)
            replies.append("Lightning " + ("in the distance." if lightning == "far" else "overhead."))
        return _done(*replies)

    def _lagcomp(self, payload, answer):
        """Lag comp through the panel's own box and auto-tune list, as a click."""
        panel = self._get("lag_comp_panel")
        if panel is None:
            return _fail("Lag compensation is not available", 409)
        enabled = payload.get("enabled")
        mode_name = payload.get("tune_mode")
        if enabled is not None and not isinstance(enabled, bool):
            return _fail("enabled must be true or false", 400)
        if mode_name is not None and mode_name not in TUNE_BY_NAME:
            return _fail("tune_mode must be off, watch or on", 400)
        if enabled is None and mode_name is None:
            return _fail("Nothing to change", 400)
        if panel._pid is None:
            # The desktop box is greyed out with no server; setChecked would
            # still save the wish, so refuse here.
            from wolfrat.lag_comp_panel import NO_SERVER
            reason = panel.server_lbl.text().lstrip("⚠").strip() or NO_SERVER
            return _fail(reason, 409)
        if enabled is not None and enabled != panel._wish_on and not panel.on_cb.isEnabled():
            return _fail(panel.state_lbl.text().strip()
                         or "Lag compensation cannot be switched on this server", 409)
        replies = []

        def set_mode():
            mode = TUNE_BY_NAME[mode_name]
            if mode == panel._settings.tune_mode:
                replies.append(f"Auto-tune per player is already {mode_name}.")
                return
            panel.tune_combo.setCurrentIndex(panel.tune_combo.findData(mode))
            panel._settings_timer.stop()
            panel._settings_settled()          # save, log, and write while on (as Use suggested)
            replies.append(f"Auto-tune per player: {mode_name}.")

        def set_enabled():
            if enabled == panel._wish_on:
                replies.append("Lag compensation is already " + ("on." if enabled else "off."))
                return
            panel.on_cb.setChecked(enabled)    # -> _on_clicked: saves, switches it now

        # On: the mode first, so the one poll that switches it on writes both.
        steps = []
        if enabled is not None:
            steps.append(set_enabled)
        if mode_name is not None:
            steps.append(set_mode)
        if enabled:
            steps.reverse()
        for step in steps:
            step()
        status = panel.state_lbl.text().strip()
        if status:
            replies.append(status)
        return _done(*replies)

    def _mode(self, payload, answer):
        """Manual / Full for this server, as the Server tab's switch."""
        mode = str(payload.get("mode") or "").strip().lower()
        if mode not in (manual_mode.FULL, manual_mode.MANUAL):
            return _fail('mode must be "full" or "manual"', 400)
        state = manual_mode.state
        if not state.key:
            return _fail("Connect WolfRAT to a server first", 409)
        if state.mode != mode:
            switch = getattr(self._window, "_manual_mode_switch", None)
            if switch is not None:
                switch(mode)                   # choose() + the same console line as the PC
            else:
                state.choose(mode)
            wire_log(f"[WEB] Manual mode -> {mode} by {self._who(payload)}")
        return _done(state.describe(), mode=state.mode)

    def _sky_page(self):
        tab = self._get("weather_tab")
        return getattr(tab, "sky_page", None) if tab is not None else None

    def _sky(self, payload, answer):
        """A sky scene through the Sky page's own code (as its scene buttons)."""
        page = self._sky_page()
        if page is None:
            return _fail("Sky colours are not available", 409)
        scene = payload.get("scene")
        minutes = payload.get("minutes")
        if not isinstance(scene, str) or (
                scene != "off" and all(item.key != scene for item in page.scenes())):
            return _fail("Unknown sky", 400)
        if scene != "off" and minutes is not None and (
                isinstance(minutes, bool) or not isinstance(minutes, int)
                or not 1 <= minutes <= sky.MAX_MINUTES):
            return _fail(f"minutes must be 1-{sky.MAX_MINUTES} "
                         "(leave it out for until the map changes)", 400)
        who = self._who(payload)
        if scene == "off":
            shown, text = page.phone_off(who)
        else:
            shown, text = page.phone_show(scene, minutes, who)
        return _done(text) if shown else _fail(text, 409)

    # --- saved map lists ---------------------------------------------------------

    def _maplist(self, payload, answer):
        """A saved map list onto the server, through the Missions tab's own
        feed (as its Load button: "add" = Add to the end, "replace" =
        Replace).  Answers as soon as it has started; the page follows the
        maps going on in the snapshot's map_lists."""
        missions = self._get("missions_tab")
        if missions is None or not hasattr(missions, "phone_load_list"):
            return _fail("Map lists need the WolfRAT window", 409)
        name = payload.get("name")
        mode = payload.get("mode")
        if not isinstance(name, str) or not name:
            return _fail("Pick a saved list", 400)
        if mode not in web_map_lists.MODES:
            return _fail('mode must be "add" or "replace"', 400)
        who = self._who(payload)
        started, text, status = missions.phone_load_list(name, mode == "replace", who)
        if not started:
            return _fail(text, status)
        how = "cleared the rotation and loaded" if mode == "replace" else "added"
        tail = "" if mode == "replace" else " to the end of the rotation"
        line = f"{who} {how} the map list '{name}'{tail}"
        wire_log(f"[WEB] {line}")
        mods = self._get("mods_tab")
        if mods is not None and hasattr(mods, "mod_log"):
            mods.mod_log.addItem(f"[{_now()}] {line}")
        return _done(text)

    def _map_lists_state(self):
        """The saved lists, the feed running now and how the last load from a
        phone went; None when this WolfRAT has no Missions tab for it."""
        missions = self._get("missions_tab")
        if missions is None or not hasattr(missions, "phone_lists"):
            return None
        return web_map_lists.block(
            missions.phone_lists(),
            getattr(missions, "_feed", None),
            getattr(missions, "_phone_last", None),
        )

    # --- time left on this map -------------------------------------------------

    def _time_reading(self):
        """(the running map's file, minutes left, round length) as the server
        says now; None for what it does not say."""
        sm = self._server
        current = web_state.current_entry(tuple(sm.mission_entries or ()))
        left, limit = web_state.parse_gametime((sm.game_settings or {}).get("gametime"))
        return (current.filename if current is not None else None), left, limit

    def _time_left_problem(self):
        """Why the phone cannot set the time left right now, or None."""
        if not self._server.is_connected:
            return "Not connected to the game server"
        bans = self._get("bans_tab")
        if coop_guard.is_coop(bans.game_type_now() if bans is not None else None):
            return "A co-op map has no round timer"
        map_file, _left, limit = self._time_reading()
        if limit is None:
            return "This map has no round timer"
        if not map_file:
            return "WolfRAT has not read the map list yet - try again in a moment"
        return None

    def _time_busy(self):
        return self._monotonic() < self._time_busy_until

    def _timeleft(self, payload, answer):
        """The time left on the running map, for this round only.  The server
        takes it as its round length, so the length found now is remembered
        and put back when the next round starts (check_time_restore)."""
        minutes = payload.get("minutes")
        if (isinstance(minutes, bool) or not isinstance(minutes, int)
                or not web_timeleft.MIN_MINUTES <= minutes <= web_timeleft.MAX_MINUTES):
            return _fail(f"minutes must be a whole number from {web_timeleft.MIN_MINUTES} "
                         f"to {web_timeleft.MAX_MINUTES}", 400)
        problem = self._time_left_problem()
        if problem:
            return _fail(problem, 409)
        if self._get("mods_tab") is None:
            return _fail("The WolfRAT window is still starting", 503)
        if self._time_busy():
            return _fail("WolfRAT is still changing the round time - try again in a moment", 409)
        server = manual_mode.state.key
        map_file, _left, limit = self._time_reading()
        who = self._who(payload)
        original = self._time.note_set(server, map_file, limit, minutes)
        reply = f"{_minutes(minutes)} left on this map."
        if original != minutes:
            reply += f" The next map goes back to {_minutes(original)}."

        def answered():
            # Our own change restarted the clock: the minutes left before it
            # must not look like a new round to check_time_restore.
            self._time_busy_until = 0.0
            self._time.clock_restarted(server)

        self._time_busy_until = self._monotonic() + TIME_BUSY_SECONDS
        try:
            return self._mod_action(
                answer,
                lambda: self._server.set_setting("gameTime", str(minutes)),
                context="Set the time left",
                announcement=f"{_minutes(minutes)} left on this map",
                log_message=f"[{_now()}] {who} set {_minutes(minutes)} left on this map",
                success_reply=reply,
                after=answered,
            )
        except Exception:
            answered()
            raise

    def check_time_restore(self):
        """After every poll: once the round the phone shortened is over, set the
        round length the server had before.  Quietly: no chat line, one line in
        the Mod activity list and the wire log.  This finishes what the admin
        pressed, so it runs in Manual mode too."""
        server = manual_mode.state.key
        entry = self._time.pending(server)
        if entry is None or self._time_busy() or not self._server.is_connected:
            return
        map_file, left, limit = self._time_reading()
        if limit is None:
            return
        original = self._time.reading(server, map_file, left, limit)
        mods = self._get("mods_tab")
        if original is None or mods is None:
            return
        was = entry.minutes

        def sent(_result):
            self._time_busy_until = 0.0
            self._time.sent(server)
            wire_log(f"[WEB] round length back to {original} min (the phone had set "
                     f"{was} min left for one map)")
            self.schedule_publish()

        def failed(message):
            self._time_busy_until = 0.0
            again = self._time.failed(server)
            wire_log(f"[WEB] round length not put back to {original} min: {message}"
                     + ("; trying again at the next poll" if again else "; given up"))
            if not again and hasattr(mods, "mod_log"):
                mods.mod_log.addItem(f"[{_now()}] WolfRAT could not put the round length back "
                                     f"to {_minutes(original)}: set it on the Settings tab")
            self.schedule_publish()

        self._time_busy_until = self._monotonic() + TIME_BUSY_SECONDS
        try:
            mods._submit_mod_action(
                lambda: self._server.set_setting("gameTime", str(original)),
                context="Put the round length back",
                log_message=f"[{_now()}] WolfRAT put the round length back to "
                            f"{_minutes(original)} (the phone's {_minutes(was)} was for one map)",
                on_success=sent,
                on_failure=failed,
            )
        except Exception:
            self._time_busy_until = 0.0
            raise

    # --- the snapshot --------------------------------------------------------

    def schedule_publish(self, *_args):
        """Rebuild the snapshot soon (once for a burst of signals)."""
        self._publish_timer.start()

    def publish_soon(self, *_args):
        """As schedule_publish, for a steady run of calls (a map list going on
        the server, one call per map): a call while a rebuild is already
        waiting does not push it back, so the page keeps getting progress."""
        if not self._publish_timer.isActive():
            self._publish_timer.start()

    def publish(self):
        self._part("time left", self.check_time_restore)
        try:
            snapshot = self.build_snapshot()
        except Exception as error:             # never break the window for the phone
            self._log_once("snapshot", f"[WEB] phone state failed: {error}")
            return
        self.ws.desktop_state = snapshot       # one attribute store: the web thread sees all or nothing
        self.ws.broadcast_state()

    def _part(self, key, builder, default=None):
        try:
            return builder()
        except Exception as error:
            self._log_once(key, f"[WEB] phone state: {key} failed: {error}")
            return default

    def build_snapshot(self):
        top = {}
        server = self._part("server", self._server_state)
        if server is not None:
            top["server"] = server
        next_map = self._part("next_map", self._next_map_state, default=False)
        if next_map is not False:
            top["next_map"] = next_map
        top["manual_mode"] = manual_mode.state.mode
        top["vote"] = self._part("vote", self._vote_state)
        weather_state, weather_ok, lightning_ok = self._part(
            "weather", self._weather_state, default=(None, False, False))
        top["weather"] = weather_state
        top["lag_comp"] = self._part("lag_comp", self._lag_comp_state)
        top["objective"] = self._part("objective", self._objective_state)
        sky_state = self._part("sky", self._sky_state)
        top["sky"] = sky_state
        top["time_restore"] = self._part("time_restore", self._time_restore_state)
        map_lists = self._part("map_lists", self._map_lists_state)
        top["map_lists"] = map_lists
        top["features"] = {
            "bans": self._get("bans_tab") is not None and self._get("mods_tab") is not None,
            "warn": self._get("mods_tab") is not None,
            "private_message": self._private_ready(),
            "weather": weather_ok,
            "weather_presets": self._part("presets", _weather_presets, default=[])
            if weather_ok else [],
            "weather_lightning": lightning_ok,
            "lag_comp": bool(self._part("lag_comp_feature", self._lag_comp_usable, default=False)),
            "manual_switch": bool(manual_mode.state.key),
            "ban_expiry": self._part("ban_expiry", _ban_lengths, default=[]),
            "sky": sky_state is not None,
            "sky_scenes": self._part("sky_scenes", self._sky_scenes, default=[])
            if sky_state is not None else [],
            "time_left": bool(self._part(
                "time_left", lambda: self._time_left_problem() is None, default=False)),
            "map_lists": map_lists is not None,
        }
        players = self._part("players", self._player_extras, default={})
        return {"top": top, "players": players}

    def _titles(self, *filenames):
        missions = self._get("missions_tab")
        titles = {}
        for filename in filenames:
            if filename and missions is not None:
                titles[filename.upper()] = missions._find_display_name(filename)
        return titles

    def _server_state(self):
        sm = self._server
        entries = tuple(sm.mission_entries or ())
        current = web_state.current_entry(entries)
        upcoming, _guessed = web_state.next_entry(entries)
        map_file = current.filename if current is not None else None
        bans = self._get("bans_tab")
        game_type = bans.game_type_now() if bans is not None else None
        left, limit = web_state.parse_gametime((sm.game_settings or {}).get("gametime"))
        if left is not None:
            if coop_guard.is_coop(game_type):
                left = limit = None                  # co-op: no timer, though it says 0/30
            elif map_file is not None:
                # A 0 counts only once this map's clock was seen running (MapClock).
                now = time.time()
                left = self._clock.minutes_left(map_name=map_file, remaining=left,
                                                read_at=now, now=now)
                if left is None:
                    limit = None
        titles = self._titles(map_file, upcoming.filename if upcoming else None)
        return web_state.server_block(sm, titles, game_type=game_type, time_left=(left, limit))

    def _next_map_state(self):
        sm = self._server
        upcoming, _guessed = web_state.next_entry(tuple(sm.mission_entries or ()))
        titles = self._titles(upcoming.filename if upcoming else None)
        return web_state.next_map_block(sm, titles)

    def _vote_state(self):
        """The player !vote, else !skip, else the end-of-match vote, else None."""
        mods = self._get("mods_tab")
        voting = self._get("map_voting_tab")

        def seconds(timer):
            left = timer.remainingTime()
            return None if left < 0 else (left + 999) // 1000

        if mods is not None and getattr(mods, "_vote_active", False):
            return {
                "kind": "map",
                "active": True,
                "map": mods._vote_map_name,
                "votes": len(mods._vote_voters),
                "needed": int(mods._vote_total * mods._vote_threshold / 100) + 1,
                "seconds_left": seconds(mods._vote_timer),
            }
        if mods is not None and getattr(mods, "_skip_active", False):
            return {
                "kind": "skip",
                "active": True,
                "map": None,
                "votes": len(mods._skip_voters),
                "needed": int(mods._skip_total * mods._skip_threshold / 100) + 1,
                "seconds_left": seconds(mods._skip_timer),
            }
        if voting is not None and getattr(voting, "_vote_stage", "") == "voting":
            counts, leader, total = voting._tally_votes()
            missions = self._get("missions_tab")
            names = [missions._find_display_name(filename) if missions is not None
                     else web_state.plain_map_name(filename)
                     for _entry, filename in voting._map_choices]
            left = voting.duration_spin.value() * 60 - (time.time() - voting._vote_start_time)
            return {
                "kind": "end_of_match",
                "active": True,
                "map": names[leader - 1] if total and 0 < leader <= len(names) else None,
                "votes": total,
                "needed": 0,
                "seconds_left": max(0, int(left)),
                "choices": [{"option": index + 1, "map": name, "votes": counts.get(index + 1, 0)}
                            for index, name in enumerate(names)],
            }
        return None

    def _weather_state(self):
        """(state["weather"], features["weather"], features["weather_lightning"])."""
        tab = self._get("weather_tab")
        if tab is None or tab._controller is None:     # remote, not found, or unusable
            return None, False, False
        page = tab.dynamic_page
        dynamic = page.enabled_cb.isChecked()
        held = tab._schedule.active
        if held is not None:
            left = tab._schedule.seconds_left()
            if left is None:
                tail = "until cleared"
            elif left < 60:
                tail = "under a minute left"
            else:
                tail = f"{-(-left // 60)} min left"
            summary = f"{_held_name(held)}, {tail}"
        elif dynamic:
            how = page.frequency_combo.currentText().lower()
            status = page.status_lbl.text().split("  -  ")[0].strip()
            summary = f"Changing weather: {how}" + (f" - {status}" if status else "")
        else:
            summary = "Map's own sky"
        reading = tab._last_reading
        state = {
            "summary": summary,
            "fog_m": reading.fog_metres if reading is not None else None,
            "dynamic": dynamic,
            # Added: the furthest this map lets anyone see (the fog slider's end).
            "map_fog_m": reading.map_fog_metres if reading is not None else None,
        }
        lightning = bool(tab.flash_btn.isEnabled())
        return state, True, lightning

    def _objective_state(self):
        """What decides the running match (web_state.objective_block), from the
        Bans tab's last poll; None when that says nothing."""
        bans = self._get("bans_tab")
        if bans is None or not self._server.is_connected:
            return None
        return web_state.objective_block(
            bans.game_type_now(), bans.zones_now(), bans.hill_now(), bans.team_caps_now(),
            bans.cap_goals_now(), bans.objectives_now(), bans.ai_left_now())

    def _sky_state(self):
        page = self._sky_page()
        return page.phone_state() if page is not None else None

    def _sky_scenes(self):
        page = self._sky_page()
        return page.phone_scenes() if page is not None else []

    def _time_restore_state(self):
        """{"limit_min": n} while the server is on a round length the phone set
        and WolfRAT will put n back at the next round; else None."""
        entry = self._time.pending(manual_mode.state.key)
        if entry is None or not self._server.is_connected:
            return None
        _map, _left, limit = self._time_reading()
        return {"limit_min": entry.original} if limit == entry.minutes else None

    def _lag_comp_state(self):
        panel = self._get("lag_comp_panel")
        if panel is None:
            return None
        state = panel._state
        return {
            "enabled": bool(panel._wish_on),
            "tune_mode": lag_comp.PT_MODE_NAMES.get(panel._settings.tune_mode, "off"),
            "summary": panel.state_lbl.text().strip() or None,
            # Added: True while WolfRAT's own lag comp really runs in the server.
            "running": bool(state is not None and state.on),
        }

    def _lag_comp_usable(self):
        panel = self._get("lag_comp_panel")
        return panel is not None and panel._pid is not None and panel.on_cb.isEnabled()

    def _player_extras(self):
        """{name.casefold(): {rank, xp, country, time_on_min}} for the players on now."""
        entries = tuple(self._server.player_entries or ())
        names = [player.name for player in entries if player.server_id != 0]
        careers = self._career_fields(names)
        minutes = self._time_on(names)
        out = {}
        for name in names:
            key = name.casefold()
            career = careers.get(key) or {}
            out[key] = {
                "rank": career.get("rank"),
                "xp": career.get("xp"),
                "country": self._country_of(name),
                "time_on_min": minutes.get(key),
            }
        return out

    def _career_fields(self, names):
        panel = self._get("career_panel")
        store = getattr(panel, "store", None) if panel is not None else None
        if store is None:
            return {}                          # no career database: rank and xp stay None
        from wolfrat import career
        ranks = panel.ranks()
        out = {}
        for name in names:
            try:
                found = store.peek(name)
            except Exception:
                found = None
            xp = int(found.xp) if found is not None else 0
            out[name.casefold()] = {"rank": career.rank_for(xp, ranks).name, "xp": xp}
        return out

    def _country_of(self, name):
        """The 2-letter code from a cached connection check; never looks one up."""
        bans = self._get("bans_tab")
        if bans is None:
            return None
        try:
            _ip, verdict = bans.connection_for(name)
        except Exception:
            return None
        if verdict is None or getattr(verdict, "error", ""):
            return None
        code = str(getattr(verdict, "country", "") or "").strip().upper()
        return code if len(code) == 2 and code.isalpha() else None

    def _time_on(self, names, now=None):
        """Minutes on the server this visit, counted from when this WolfRAT first
        saw them; back within 10 minutes = the same visit."""
        now = time.time() if now is None else now
        out = {}
        for name in names:
            key = name.strip().casefold()
            if not key:
                continue
            last = self._visit_seen.get(key)
            if last is None or now - last > REVISIT_SECONDS:
                self._visit_start[key] = now
            self._visit_seen[key] = now
            out[key] = max(0, int((now - self._visit_start[key]) // 60))
        for key in [k for k, seen in self._visit_seen.items() if now - seen > REVISIT_SECONDS]:
            self._visit_seen.pop(key, None)
            self._visit_start.pop(key, None)
        return out
