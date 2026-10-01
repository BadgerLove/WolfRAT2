"""Compatibility facade over the typed retail admin session.

Runtime code talks to :class:`ServerManager`; only the explicit raw-console
entry point can submit untyped text.  Socket ownership, framing, authentication,
request ordering, reply correlation, and snapshots all live in
``RetailAdminSession``.
"""

from __future__ import annotations

from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass, replace
import logging
import random
import threading
from typing import Callable, Iterable, Mapping, Optional

from .admin_commands import (
    AdminCommands,
    AdminOperation,
    AvailableMission,
    CommandSpec,
    GameSettings,
    MAX_CHAT_LEN,
    MAX_CHAT_TOKENS,
    chat_limit,
    MissionEntry,
    PlayerEntry,
    SETTING_SCHEMA,
    WeaponEntry,
    WeaponMode,
)
from .coop_guard import REFUSAL as COOP_REFUSAL
from .admin_session import (
    CommandResult,
    RawResult,
    RequestPriority,
    RetailAdminSession,
)


CHAT_MAX_LEN = MAX_CHAT_LEN
_LOGGER = logging.getLogger("wolfrat.protocol")
_SECRET_SETTING_NAMES = frozenset(
    key.casefold()
    for key, kind in SETTING_SCHEMA.items()
    if kind == "secret"
)


class _MissionNotFoundError(ValueError):
    """A mission target is absent, rather than present but ambiguous."""


@dataclass(frozen=True)
class TeamWorkflow:
    """One planned team mutation and the messages describing that plan."""

    messages: tuple[str, ...]
    completion: Future


import os
import sys
import time as _time

# Wire log. Appends for ever (never wiped on start) - the admin deletes the
# file when they want a fresh one. It stays small because the 5 s poll
# replies are only written when they change (ServerManager._wire_changed)
# and a line repeated back to back is folded into one "repeated" note.
_wire_lock = threading.Lock()
_wire_day = None          # date of the last line written, for day headers
_wire_last = None         # last message written (without its time stamp)
_wire_repeats = 0         # how many times _wire_last repeated since
_wire_repeat_time = ""    # time of the latest repeat


def _wire_log_path():
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.dirname(sys.executable), 'wolfrat_wire.log')
    return os.path.join(os.getcwd(), 'wolfrat_wire.log')


def wire_log(message: str) -> None:
    """Append one line to wolfrat_wire.log."""
    global _wire_day, _wire_last, _wire_repeats, _wire_repeat_time
    now = _time.localtime()
    stamp = _time.strftime('%H:%M:%S', now)
    try:
        with _wire_lock:
            if message == _wire_last:
                _wire_repeats += 1
                _wire_repeat_time = stamp
                return
            lines = []
            if _wire_repeats:
                times = "once more" if _wire_repeats == 1 else f"{_wire_repeats} more times"
                lines.append(f"[{_wire_repeat_time}] (line above repeated {times})")
            day = _time.strftime('%Y-%m-%d', now)
            if day != _wire_day:
                lines.append(f"---------- {_time.strftime('%A %d %B %Y', now)} ----------")
                _wire_day = day
            lines.append(f"[{stamp}] {message}")
            _wire_last, _wire_repeats = message, 0
            with open(_wire_log_path(), 'a', encoding='utf-8') as f:
                f.write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"WIRE LOG ERROR: {e}")
    _LOGGER.debug("%s", message)


# Startup marker. A blank line first so each run stands out in the file.
try:
    with open(_wire_log_path(), 'a', encoding='utf-8') as _f:
        _f.write("\n")
except Exception as e:
    print(f"WIRE LOG INIT ERROR: {e}")
wire_log("=== protocol.py module loaded ===")


class _ProtocolView:
    """Read-only compatibility view for extensions checking connectivity."""

    __slots__ = ("_manager",)

    def __init__(self, manager: "ServerManager") -> None:
        object.__setattr__(self, "_manager", manager)

    @property
    def connected(self) -> bool:
        return self._manager.is_connected

    def __setattr__(self, name, value) -> None:
        raise AttributeError("protocol compatibility view is read-only")


class ServerManager:
    """Qt-friendly adapter over one mandatory :class:`RetailAdminSession`.

    Submitting a semantic mutation commits its complete mutation/readback
    workflow. Cancelling the returned Future only detaches that observer; it
    cannot safely retract bytes already sent or leave a multi-step workflow
    half applied.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[..., RetailAdminSession] = RetailAdminSession,
        verification_attempts: int = 5,
        verification_delay: float = 0.2,
    ) -> None:
        if verification_attempts < 1:
            raise ValueError("verification_attempts must be positive")
        if verification_delay < 0:
            raise ValueError("verification_delay cannot be negative")
        self._session_factory = session_factory
        self._verification_attempts = int(verification_attempts)
        self._verification_delay = float(verification_delay)
        self._session: Optional[RetailAdminSession] = None
        self._protocol_view = _ProtocolView(self)
        self._poll_timer: Optional[threading.Timer] = None
        self._poll_interval = 5.0
        self._polling = False
        self._lock = threading.RLock()
        self._mutation_tail = completed_future(None)
        self._closed = False
        self._connection_generation = 0
        self._last_poll_success: float = 0.0
        # Last quiet poll reply written to the wire log, per operation.
        self._wire_seen: dict = {}
        self._stale_watchdog_timer: Optional[threading.Timer] = None
        # Must survive several failed poll cycles. The socket timeout is 15s
        # and JO freezes 5-15s during map loads, so 20s tripped on ordinary
        # map changes and forced a needless reconnect.
        self._stale_threshold = 60.0  # seconds without poll response = dead

        self.players: list[dict[str, object]] = []
        self.missions: list[str] = []
        self.chat_messages: list[dict[str, object]] = []
        self.game_settings: dict[str, str] = {}
        self.game_state: dict[str, str] = {}
        self.available_maps_data = ""
        self.weapons: list[dict[str, object]] = []
        self._last_raw_chat_lines: tuple[str, ...] = ()
        self._chat_message_id = 0

        self._on_players = None
        self._on_chat = None
        self._on_raw_chat = None
        self._on_gamestate = None
        self._on_missions = None
        self._on_settings = None
        self._on_available_maps = None
        self._on_weapons = None
        self._on_log = None
        self._on_disconnect_ui = None
        self._on_connect_done = None
        # coop_guard.CoopGuard, set by the Chat Bot tab. Every swap asks it.
        self.swap_guard = None

    @property
    def proto(self) -> _ProtocolView:
        return self._protocol_view

    @property
    def is_connected(self) -> bool:
        session = self._session
        return bool(session is not None and session.connected)

    @property
    def mission_entries(self) -> tuple[MissionEntry, ...]:
        return self._snapshot_collection("missions")

    @property
    def available_mission_entries(self) -> tuple[AvailableMission, ...]:
        return self._snapshot_collection("available_missions")

    @property
    def weapon_entries(self) -> tuple[WeaponEntry, ...]:
        return self._snapshot_collection("weapons")

    @property
    def player_entries(self) -> tuple[PlayerEntry, ...]:
        return self._snapshot_collection("players")

    def _snapshot_collection(self, name: str) -> tuple:
        session = self._session
        if session is None:
            return ()
        return tuple(getattr(session.snapshot, name))

    def set_callbacks(
        self,
        on_players=None,
        on_chat=None,
        on_gamestate=None,
        on_missions=None,
        on_settings=None,
        on_available_maps=None,
        on_log=None,
        on_disconnect_ui=None,
        on_connect_done=None,
        on_weapons=None,
    ) -> None:
        self._on_players = on_players
        self._on_chat = on_chat
        self._on_gamestate = on_gamestate
        self._on_missions = on_missions
        self._on_settings = on_settings
        self._on_available_maps = on_available_maps
        self._on_log = on_log
        self._on_disconnect_ui = on_disconnect_ui
        self._on_connect_done = on_connect_done
        self._on_weapons = on_weapons

    def set_raw_chat_callback(self, callback) -> None:
        self._on_raw_chat = callback

    def connect(self, host, port=4000, username="", password=""):
        generation = self.disconnect()
        with self._lock:
            if self._closed:
                return False, "Connection failed: server manager is closed"
        session = None
        try:
            session = self._session_factory(
                host, port, username, password
            )
            with self._lock:
                if (
                    self._closed
                    or generation != self._connection_generation
                ):
                    cancelled = True
                else:
                    self._session = session
                    cancelled = False
            if cancelled:
                session.close()
                return False, "Connection failed: server manager is closed"
            first = session.execute(AdminCommands.game_state()).result()
            if not first.accepted:
                session.close()
                with self._lock:
                    if self._session is session:
                        self._session = None
                        self._mutation_tail = completed_future(None)
                detail = first.replies[-1] if first.replies else "rejected"
                return False, f"Connection failed: {detail}"
            if not self._connection_is_current(session, generation):
                session.close()
                return False, "Connection failed: connection was cancelled"
            with self._lock:
                # Log the full picture once per connection.
                self._wire_seen.clear()
            self._apply_result(first)
            self._log(f"Connected to {host}:{port}")
            if (
                self._on_connect_done
                and self._connection_is_current(session, generation)
            ):
                self._on_connect_done()
            if not self._connection_is_current(session, generation):
                session.close()
                return False, "Connection failed: connection was cancelled"
            self.refresh_all()
            self.start_polling(self._poll_interval)
            if not self._connection_is_current(session, generation):
                session.close()
                return False, "Connection failed: connection was cancelled"
            return True, f"Connected to {host}:{port}"
        except Exception as error:
            with self._lock:
                if self._session is session:
                    self._session = None
                    self._mutation_tail = completed_future(None)
            if session is not None:
                session.close()
            self._log(f"Connection failed: {error}")
            return False, f"Connection failed: {error}"

    def disconnect(self) -> int:
        """Disconnect and return the generation that invalidated prior work."""

        self.stop_polling()
        with self._lock:
            self._connection_generation += 1
            generation = self._connection_generation
            session, self._session = self._session, None
            self._mutation_tail = completed_future(None)
        if session is not None:
            session.close()
        return generation

    def close(self) -> None:
        """Permanently close this manager and invalidate pending connects."""

        with self._lock:
            self._closed = True
        self.disconnect()

    def _connection_is_current(
        self,
        session: RetailAdminSession,
        generation: int,
    ) -> bool:
        with self._lock:
            return (
                not self._closed
                and self._connection_generation == generation
                and self._session is session
            )

    def execute_raw(self, user_text: str) -> Future:
        """The sole untyped command entry point, for an explicit raw console."""

        self._log(f">> RAW {_redact_raw_command(user_text)}")
        return self._serialize_mutation(
            lambda session: self._execute_raw_now(
                user_text, expected_session=session
            )
        )

    def _execute_raw_now(
        self,
        user_text: str,
        *,
        expected_session: RetailAdminSession,
    ) -> Future:
        with self._lock:
            session = self._require_session(expected_session)
            source = session.execute_raw(user_text)
        sanitized: Future = Future()

        def sanitize(done: Future) -> None:
            if sanitized.cancelled():
                return
            try:
                result = done.result()
                sanitized.set_result(RawResult(
                    command=_redact_raw_command(result.command),
                    replies=tuple(
                        _redact_setting_lines(reply)
                        for reply in result.replies
                    ),
                    accepted=result.accepted,
                ))
            except BaseException as error:
                sanitized.set_exception(error)

        source.add_done_callback(sanitize)
        sanitized.add_done_callback(
            lambda done: self._observe_raw_result(
                done, source_session=session
            )
        )
        return sanitized

    def _execute(
        self,
        spec: CommandSpec,
        *,
        confirm: Optional[Callable[[], Future]] = None,
        verify: Optional[Callable[[CommandResult, CommandResult], bool]] = None,
        quiet: bool = False,
        expected_session: Optional[RetailAdminSession] = None,
    ) -> Future:
        if (confirm is None) != (verify is None):
            raise ValueError(
                "confirmed mutations require an operation-specific verifier"
            )
        prefix = "__QUIET__" if quiet else ""
        # The 5 s poll commands go to the console only; their replies reach
        # the wire log when something changed (_apply_result).
        self._log(f"{prefix}>> {spec.operation.value}", to_file=not quiet)
        is_background_read = quiet and not spec.mutating
        with self._lock:
            session = self._require_session(expected_session)
            future = session.execute(
                spec,
                priority=(
                    RequestPriority.BACKGROUND
                    if is_background_read
                    else RequestPriority.INTERACTIVE
                ),
                coalesce_key=(
                    spec.operation if is_background_read else None
                ),
            )
        future.add_done_callback(
            lambda done: self._observe_result(
                done,
                quiet=quiet,
                source_session=session,
            )
        )
        if confirm is None:
            return future
        assert verify is not None

        def bound_confirmation() -> Future:
            with self._lock:
                self._require_session(session)
                return confirm()

        return self._confirm_mutation(
            future, bound_confirmation, verify
        )

    def _serialize_mutation(
        self,
        workflow_factory: Callable[[RetailAdminSession], Future],
    ) -> Future:
        """Commit one complete semantic workflow behind prior mutations."""

        observer: Future = Future()
        gate: Future = Future()
        with self._lock:
            session = self._require_session()
            predecessor = self._mutation_tail
            self._mutation_tail = gate

        def start(_previous: Future) -> None:
            try:
                with self._lock:
                    self._require_session(session)
                    workflow = workflow_factory(session)
            except BaseException as error:
                _settle_future(gate)
                _settle_future(observer, error=error)
                return

            def finish(done: Future) -> None:
                try:
                    result = done.result()
                except BaseException as error:
                    _settle_future(gate)
                    _settle_future(observer, error=error)
                else:
                    _settle_future(gate)
                    _settle_future(observer, result=result)

            workflow.add_done_callback(finish)

        predecessor.add_done_callback(start)
        return observer

    def _observe_result(
        self,
        future: Future,
        *,
        quiet: bool = False,
        source_session: Optional[RetailAdminSession] = None,
    ) -> None:
        with self._lock:
            if (
                source_session is not None
                and self._session is not source_session
            ):
                self._log(
                    "Ignored completion from a replaced retail session"
                )
                return
            try:
                result = future.result()
            except Exception as error:
                self._operation_failed(error)
                return
            self._apply_result(result, quiet=quiet)

    def _observe_raw_result(
        self,
        future: Future,
        *,
        source_session: Optional[RetailAdminSession] = None,
    ) -> None:
        with self._lock:
            if (
                source_session is not None
                and self._session is not source_session
            ):
                self._log(
                    "Ignored raw completion from a replaced retail session"
                )
                return
            try:
                result = future.result()
            except Exception as error:
                self._operation_failed(error)
                return
            for reply in result.replies:
                self._log(f"<< {_redact_setting_lines(reply)}")

    def _operation_failed(self, error: Exception) -> None:
        self._log(f"Admin operation failed: {error}")
        if not self.is_connected and self._on_disconnect_ui:
            self._on_disconnect_ui()

    @staticmethod
    def _then(
        first: Future,
        next_factory: Callable[[object], Future],
    ) -> Future:
        output: Future = Future()

        def first_done(done: Future) -> None:
            try:
                first_result = done.result()
                if not _result_succeeded(first_result):
                    _settle_future(output, result=first_result)
                    return
                second = next_factory(first_result)
            except BaseException as error:
                _settle_future(output, error=error)
                return

            def second_done(followup: Future) -> None:
                try:
                    _settle_future(output, result=followup.result())
                except BaseException as error:
                    _settle_future(output, error=error)

            second.add_done_callback(second_done)

        first.add_done_callback(first_done)
        return output

    @staticmethod
    def _confirm_mutation(
        mutation: Future,
        confirm_factory: Callable[[], Future],
        verify: Callable[[CommandResult, CommandResult], bool],
    ) -> Future:
        """Confirm state while preserving the ACK and shielding the workflow."""

        output: Future = Future()

        def unverified(
            acknowledgement: CommandResult, detail: object
        ) -> CommandResult:
            message = str(detail).strip() or "confirmation failed"
            return replace(
                acknowledgement,
                verified=False,
                verification_error=f"retail confirmation failed: {message}",
            )

        def mutation_done(done: Future) -> None:
            try:
                acknowledgement = done.result()
            except BaseException as error:
                _settle_future(output, error=error)
                return
            if getattr(acknowledgement, "accepted", True) is False:
                _settle_future(output, result=acknowledgement)
                return
            try:
                confirmation = confirm_factory()
            except BaseException as error:
                _settle_future(
                    output,
                    result=unverified(acknowledgement, error),
                )
                return

            def confirmation_done(followup: Future) -> None:
                try:
                    confirmed = followup.result()
                    if getattr(confirmed, "accepted", True) is False:
                        detail = (
                            confirmed.replies[-1]
                            if confirmed.replies
                            else f"{confirmed.operation.value} was rejected"
                        )
                        result = unverified(acknowledgement, detail)
                    else:
                        verified = bool(verify(acknowledgement, confirmed))
                        result = replace(
                            acknowledgement,
                            verified=verified,
                            verification_error=(
                                None
                                if verified
                                else "retail readback did not confirm the requested effect"
                            ),
                        )
                    _settle_future(output, result=result)
                except BaseException as error:
                    _settle_future(
                        output,
                        result=unverified(acknowledgement, error),
                    )

            confirmation.add_done_callback(confirmation_done)

        mutation.add_done_callback(mutation_done)
        return output

    @staticmethod
    def _verify_acknowledgement(
        mutation: Future,
        verify: Callable[[CommandResult], bool],
    ) -> Future:
        """Mark command-specific ACKs without inventing a state readback."""

        output: Future = Future()

        def mutation_done(done: Future) -> None:
            try:
                acknowledgement = done.result()
                if getattr(acknowledgement, "accepted", True) is False:
                    result = acknowledgement
                else:
                    verified = bool(verify(acknowledgement))
                    result = replace(
                        acknowledgement,
                        verified=verified,
                        verification_error=(
                            None
                            if verified
                            else "retail reply was not the command-specific acknowledgement"
                        ),
                    )
                _settle_future(output, result=result)
            except BaseException as error:
                _settle_future(output, error=error)

        mutation.add_done_callback(mutation_done)
        return output

    @classmethod
    def _sequence(cls, factories: Iterable[Callable[[], Future]]) -> Future:
        chain = completed_future(None)
        for factory in factories:
            chain = cls._then(chain, lambda _result, make=factory: make())
        return chain

    # ---- canonical reads -------------------------------------------------

    def refresh_game_state(self, quiet=False) -> Future:
        return self._execute(AdminCommands.game_state(), quiet=quiet)

    def refresh_settings(self, quiet=False) -> Future:
        return self._execute(AdminCommands.game_settings(), quiet=quiet)

    def refresh_players(self, quiet=False) -> Future:
        return self._execute(AdminCommands.players(), quiet=quiet)

    def refresh_missions(self, quiet=False) -> Future:
        return self._execute(AdminCommands.missions(), quiet=quiet)

    def refresh_available_maps(self, quiet=False) -> Future:
        return self._execute(AdminCommands.available_missions(), quiet=quiet)

    def refresh_weapons(self, quiet=False) -> Future:
        return self._execute(AdminCommands.weapons(), quiet=quiet)

    def refresh_chat(self, quiet=False) -> Future:
        return self._execute(AdminCommands.chat(), quiet=quiet)

    def refresh_all(self) -> tuple[Future, ...]:
        return (
            self.refresh_game_state(quiet=True),
            self.refresh_players(quiet=True),
            self.refresh_missions(quiet=True),
            self.refresh_available_maps(quiet=True),
            self.refresh_settings(quiet=True),
            self.refresh_chat(quiet=True),
            self.refresh_weapons(quiet=True),
        )

    # ---- settings/chat ---------------------------------------------------

    def set_setting(self, key, value) -> Future:
        spec = AdminCommands.set_setting(key, value)
        parts = spec.text.split(" ", 2)
        canonical = parts[1]
        expected = parts[2] if len(parts) == 3 else ""
        return self._serialize_mutation(
            lambda session: self._execute(
                spec,
                confirm=self.refresh_settings,
                verify=lambda ack, readback: (
                    _ack_is(ack, "OK - Setting Changed.")
                    and _setting_matches(
                        canonical, expected, readback.value
                    )
                ),
                expected_session=session,
            )
        )

    def send_chat(self, message, quiet=False) -> Future:
        chunks = _split_chat_message(message)
        return self._serialize_mutation(
            lambda session: self._send_chat_chunks(
                chunks,
                quiet=quiet,
                session=session,
            )
        )

    def _send_chat_chunks(
        self,
        chunks: Iterable[str],
        *,
        quiet: bool,
        session: RetailAdminSession,
    ) -> Future:
        return self._sequence(
            lambda chunk=chunk: self._send_chat_chunk(
                chunk,
                quiet=quiet,
                expected_session=session,
            )
            for chunk in chunks
        )

    def _send_chat_chunk(
        self,
        message: str,
        *,
        quiet: bool,
        expected_session: RetailAdminSession,
    ) -> Future:
        return self._verify_acknowledgement(
            self._execute(
                AdminCommands.send_chat(message),
                quiet=quiet,
                expected_session=expected_session,
            ),
            lambda ack: _ack_is(ack, "OK - Chat sent."),
        )

    def announce(self, message: str) -> Future:
        return self.send_chat(message)

    def warn_player(self, player_id, message="You have been warned!") -> Future:
        player_target = self._resolve_player(player_id)
        chunks = _split_chat_message(
            f"WARNING to {player_target.name}: {message}"
        )

        def workflow(session: RetailAdminSession) -> Future:
            self._resolve_current_player(player_target)
            return self._send_chat_chunks(
                chunks,
                quiet=False,
                session=session,
            )

        return self._serialize_mutation(workflow)

    # ---- player operations ----------------------------------------------

    def punt_player(self, player_id, message="") -> Future:
        target = self._resolve_player(player_id)

        def workflow(session: RetailAdminSession) -> Future:
            player = self._resolve_current_player(target)

            def predicate(readback):
                return _player_by_id(
                    readback.value, player.server_id
                ) is None

            return self._execute(
                AdminCommands.punt(player),
                confirm=lambda: self._eventual_confirmation(
                    self.refresh_players, predicate
                ),
                verify=lambda ack, readback: (
                    _ack_is(ack, "OK - Player punted.")
                    and predicate(readback)
                ),
                expected_session=session,
            )

        return self._serialize_mutation(workflow)

    def ban_player(self, player_id, message="") -> Future:
        target = self._resolve_player(player_id)

        def workflow(session: RetailAdminSession) -> Future:
            player = self._resolve_current_player(target)

            def predicate(readback):
                return _player_by_id(
                    readback.value, player.server_id
                ) is None

            return self._execute(
                AdminCommands.ban(player),
                confirm=lambda: self._eventual_confirmation(
                    self.refresh_players, predicate
                ),
                verify=lambda ack, readback: (
                    _terminal_ack_is(ack, "OK - Player Banned.")
                    and predicate(readback)
                ),
                expected_session=session,
            )

        return self._serialize_mutation(workflow)

    def kill_player(self, player_id) -> Future:
        target = self._resolve_player(player_id)
        return self._serialize_mutation(
            lambda session: self._kill_player_now(target, session)
        )

    def _kill_player_now(
        self,
        target: PlayerEntry,
        session: RetailAdminSession,
    ) -> Future:
        player = self._resolve_current_player(target)

        def predicate(readback):
            return _kill_readback_matches(player, readback.value)

        return self._execute(
            AdminCommands.kill(player),
            confirm=lambda: self._eventual_confirmation(
                self.refresh_players, predicate
            ),
            verify=lambda ack, readback: (
                _ack_is(ack, "OK - Player Killed.")
                and predicate(readback)
            ),
            expected_session=session,
        )

    def swap_player(self, player_id, *, allow_coop: bool = False) -> Future:
        target = self._resolve_player(player_id)
        return self._serialize_mutation(
            lambda session: self._swap_player_now(
                target, session, allow_coop=allow_coop
            )
        )

    def swaps_blocked(self) -> bool:
        """True on a co-op map while co-op protection is on."""
        guard = self.swap_guard
        return bool(guard is not None and guard.blocks_swaps())

    def _swap_player_now(
        self,
        target: PlayerEntry,
        session: RetailAdminSession,
        *,
        allow_coop: bool = False,
    ) -> Future:
        if self.swap_guard is not None:
            self.swap_guard.check(allow_coop)
        player = self._resolve_current_player(target)
        if player.team != target.team:
            raise ValueError(
                f"player {target.server_id} team changed while its "
                "swap was queued"
            )
        return self._execute(
            AdminCommands.swap_team(player),
            confirm=self.refresh_players,
            verify=lambda ack, readback: (
                _ack_is(ack, "OK - Player Swapped.")
                and (
                    (current := _player_by_id(
                        readback.value, player.server_id
                    ))
                    is not None
                    and current.team != player.team
                )
            ),
            expected_session=session,
        )

    def zero_player(self, player_id) -> Future:
        target = self._resolve_player(player_id)

        def workflow(session: RetailAdminSession) -> Future:
            player = self._resolve_current_player(target)
            return self._execute(
                AdminCommands.zero_score(player),
                confirm=self.refresh_players,
                verify=lambda ack, readback: (
                    _ack_is(ack, "OK - Player Zeroed.")
                    and _zero_readback_matches(
                        player.server_id, readback.value
                    )
                ),
                expected_session=session,
            )

        return self._serialize_mutation(workflow)

    def _eventual_confirmation(
        self,
        refresh_factory: Callable[[], Future],
        predicate: Callable[[CommandResult], bool],
    ) -> Future:
        """Retry an asynchronous retail effect without blocking its worker."""

        output: Future = Future()
        with self._lock:
            session = self._require_session()

        def attempt(remaining: int) -> None:
            try:
                with self._lock:
                    self._require_session(session)
                    refresh = refresh_factory()
            except BaseException as error:
                _settle_future(output, error=error)
                return

            def refreshed(done: Future) -> None:
                try:
                    result = done.result()
                    if getattr(result, "accepted", False) is False:
                        _settle_future(output, result=result)
                        return
                    complete = (
                        predicate(result)
                    )
                    if complete or remaining <= 1:
                        _settle_future(output, result=result)
                        return
                    if self._verification_delay:
                        timer = threading.Timer(
                            self._verification_delay,
                            lambda: attempt(remaining - 1),
                        )
                        timer.daemon = True
                        timer.start()
                    else:
                        attempt(remaining - 1)
                except BaseException as error:
                    _settle_future(output, error=error)

            refresh.add_done_callback(refreshed)

        attempt(self._verification_attempts)
        return output

    def swap_and_kill(self, player_id, name="", *, allow_coop: bool = False) -> Future:
        target = self._resolve_player(player_id)
        return self._serialize_mutation(
            lambda session: self._move_player_now(
                target, session, allow_coop=allow_coop
            )
        )

    def _move_player_now(
        self,
        target: PlayerEntry,
        session: RetailAdminSession,
        *,
        allow_coop: bool = False,
    ) -> Future:
        return self._then(
            self._swap_player_now(target, session, allow_coop=allow_coop),
            lambda _result: self._kill_player_now(
                self._rebind_owned_player(target, session),
                session,
            ),
        )

    def _move_players_now(
        self,
        targets: Iterable[PlayerEntry],
        session: RetailAdminSession,
        expected_roster: Iterable[PlayerEntry],
    ) -> Future:
        for expected in expected_roster:
            try:
                current = self._resolve_current_player(expected)
            except ValueError as error:
                raise ValueError(
                    f"team plan is stale: {error}"
                ) from error
            if current.team != expected.team:
                raise ValueError(
                    "team plan is stale because "
                    f"{expected.name} changed teams"
                )
        return self._sequence(
            lambda target=target: self._move_player_now(
                self._rebind_owned_player(
                    target,
                    session,
                    expected_team=target.team,
                ),
                session,
            )
            for target in targets
        )

    def _rebind_owned_player(
        self,
        target: PlayerEntry,
        session: RetailAdminSession,
        *,
        expected_team: Optional[int] = None,
    ) -> PlayerEntry:
        """Adopt readback identity only inside an already-owned workflow."""

        with self._lock:
            self._require_session(session)
            current = next(
                (
                    item
                    for item in self.player_entries
                    if item.server_id == target.server_id
                ),
                None,
            )
            if current is None:
                raise ValueError(
                    f"player id {target.server_id} disappeared during "
                    "the committed workflow"
                )
            if current.name != target.name:
                raise ValueError(
                    f"player id {target.server_id} changed from "
                    f"{target.name!r} to {current.name!r} during "
                    "the committed workflow"
                )
            if (
                expected_team is not None
                and current.team != expected_team
            ):
                raise ValueError(
                    f"player {target.name} changed teams during "
                    "the committed workflow"
                )
            return current

    def _resolve_player(self, value) -> PlayerEntry:
        if isinstance(value, PlayerEntry):
            current = next(
                (
                    item
                    for item in self.player_entries
                    if item.server_id == value.server_id
                ),
                None,
            )
            if current is None:
                raise ValueError(
                    f"player id {value.server_id} is not in the "
                    "current snapshot"
                )
            if current.name != value.name:
                raise ValueError(
                    f"player id {value.server_id} changed from "
                    f"{value.name!r} to {current.name!r}"
                )
            target = value
        else:
            try:
                player_id = int(value)
            except (TypeError, ValueError) as error:
                raise ValueError("player target must be a numeric retail id") from error
            target = next(
                (item for item in self.player_entries if item.server_id == player_id),
                None,
            )
        if target is None:
            raise ValueError(f"player id {value!r} is not in the current snapshot")
        if target.server_id == 0:
            raise ValueError("player id 0 is the host and cannot be targeted")
        return target

    def _resolve_current_player(
        self, target: PlayerEntry
    ) -> PlayerEntry:
        current = next(
            (
                item
                for item in self.player_entries
                if item.server_id == target.server_id
            ),
            None,
        )
        if current is None:
            raise ValueError(
                f"player id {target.server_id} is missing"
            )
        if current.name != target.name:
            raise ValueError(
                f"player id {target.server_id} changed from "
                f"{target.name!r} to {current.name!r}"
            )
        return current

    # ---- mission operations ---------------------------------------------

    def add_mission(
        self,
        filename,
        *,
        auto_switch_sides=None,
        insert_at=None,
        one_shot=False,
    ) -> Future:
        target = self._resolve_available_mission(filename)
        AdminCommands.add_mission(
            target,
            auto_switch_sides=auto_switch_sides,
            insert_at=insert_at,
            one_shot=one_shot,
        )
        return self._serialize_mutation(
            lambda session: self._add_mission_now(
                target,
                auto_switch_sides=auto_switch_sides,
                insert_at=insert_at,
                one_shot=one_shot,
                session=session,
            )
        )

    def _add_mission_now(
        self,
        target: AvailableMission,
        *,
        auto_switch_sides,
        insert_at,
        one_shot: bool,
        session: RetailAdminSession,
    ) -> Future:
        mission = self._resolve_current_available_mission(target)
        before = tuple(self.mission_entries)
        expected_index = (
            int(insert_at)
            if insert_at is not None
            else max(
                (item.queue_index for item in before),
                default=-1,
            ) + 1
        )
        expected_double_time = (
            bool(auto_switch_sides)
            if auto_switch_sides is not None or insert_at is not None
            else None
        )
        return self._execute(
            AdminCommands.add_mission(
                mission,
                auto_switch_sides=auto_switch_sides,
                insert_at=insert_at,
                one_shot=one_shot,
            ),
            confirm=self.refresh_missions,
            verify=lambda ack, readback: (
                _ack_is(ack, "OK - Entry Added")
                and _mission_add_readback_matches(
                    before,
                    readback.value,
                    mission.filename,
                    expected_index=expected_index,
                    expected_one_shot=bool(one_shot),
                    expected_double_time=expected_double_time,
                )
            ),
            expected_session=session,
        )

    def remove_mission(self, index_or_entry) -> Future:
        target = self._resolve_mission(index_or_entry)

        def workflow(session: RetailAdminSession) -> Future:
            mission = self._resolve_current_mission(target)
            before = tuple(self.mission_entries)
            return self._execute(
                AdminCommands.remove_mission(mission),
                confirm=self.refresh_missions,
                verify=lambda ack, readback: (
                    _ack_is(ack, "OK - Mission Removed.")
                    and len(readback.value) == len(before) - 1
                    and sum(
                        item.filename.casefold()
                        == mission.filename.casefold()
                        for item in readback.value
                    )
                    == sum(
                        item.filename.casefold()
                        == mission.filename.casefold()
                        for item in before
                    ) - 1
                ),
                expected_session=session,
            )

        return self._serialize_mutation(workflow)

    def clear_missions(self) -> Future:
        return self._serialize_mutation(
            lambda session: self._execute(
                AdminCommands.clear_missions(),
                confirm=self.refresh_missions,
                verify=lambda ack, readback: (
                    _ack_is(ack, "OK - Mission list reset.")
                    and not readback.value
                ),
                expected_session=session,
            )
        )

    def set_next_mission(
        self, index_or_entry, *, add_if_missing=False
    ) -> Future:
        try:
            target = self._resolve_mission(index_or_entry)
        except _MissionNotFoundError:
            if not add_if_missing or not isinstance(index_or_entry, str):
                raise
            available = self._resolve_available_mission(index_or_entry)
            AdminCommands.add_mission(
                available, auto_switch_sides=False
            )
            return self._serialize_mutation(
                lambda session: self._then(
                    self._add_mission_now(
                        available,
                        auto_switch_sides=False,
                        insert_at=None,
                        one_shot=False,
                        session=session,
                    ),
                    lambda _result: self._set_next_mission_now(
                        self._resolve_mission(index_or_entry),
                        session,
                    ),
                )
            )

        return self._serialize_mutation(
            lambda session: self._set_next_mission_now(
                target, session
            )
        )

    def _set_next_mission_now(
        self,
        target: MissionEntry,
        session: RetailAdminSession,
    ) -> Future:
        mission = self._resolve_current_mission(target)
        return self._execute(
            AdminCommands.set_next_mission(mission),
            confirm=self.refresh_missions,
            verify=lambda ack, readback: (
                _ack_is(ack, "OK - Next Mission Set.")
            ),
            expected_session=session,
        )

    def cycle_mission(self) -> Future:
        # The retail server deliberately lingers for 620 ticks after accepting
        # CYCLE, and a single-map queue can remain identical. Its specific ACK
        # is emitted only after the action call, so it is the reliable proof.
        return self._serialize_mutation(
            self._cycle_mission_now
        )

    def _cycle_mission_now(
        self, session: RetailAdminSession
    ) -> Future:
        return self._verify_acknowledgement(
            self._execute(
                AdminCommands.cycle_mission(),
                expected_session=session,
            ),
            lambda ack: _ack_is(ack, "OK - Server is cycling..."),
        )

    def next_map(self) -> Future:
        return self.cycle_mission()

    def switch_mission(
        self, index_or_entry, *, add_if_missing=False
    ) -> Future:
        try:
            target = self._resolve_mission(index_or_entry)
        except _MissionNotFoundError:
            if not add_if_missing or not isinstance(index_or_entry, str):
                raise
            available = self._resolve_available_mission(index_or_entry)
            AdminCommands.add_mission(
                available, auto_switch_sides=False
            )

            def prepare(session: RetailAdminSession) -> Future:
                return self._then(
                    self._add_mission_now(
                        available,
                        auto_switch_sides=False,
                        insert_at=None,
                        one_shot=False,
                        session=session,
                    ),
                    lambda _result: self._set_next_mission_now(
                        self._resolve_mission(index_or_entry),
                        session,
                    ),
                )
        else:
            def prepare(session: RetailAdminSession) -> Future:
                return self._set_next_mission_now(target, session)

        return self._serialize_mutation(
            lambda session: self._then(
                prepare(session),
                lambda _result: self._cycle_mission_now(session),
            )
        )

    def _resolve_mission(self, value) -> MissionEntry:
        if isinstance(value, MissionEntry):
            return self._resolve_current_mission(value)
        if isinstance(value, str) and not value.strip().isdigit():
            filename = value.casefold()
            matches = [
                item
                for item in self.mission_entries
                if item.filename.casefold() == filename
            ]
            if not matches:
                raise _MissionNotFoundError(
                    f"mission {value!r} is not in the current queue snapshot"
                )
            if len(matches) > 1:
                raise ValueError(
                    f"mission filename {value!r} is ambiguous in the "
                    "current queue; use a MissionEntry or queue index"
                )
            return matches[0]
        try:
            index = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError("mission target must be a retail queue index") from error
        mission = next(
            (item for item in self.mission_entries if item.queue_index == index),
            None,
        )
        if mission is None:
            raise _MissionNotFoundError(
                f"mission queue index {index} is not in the current snapshot"
            )
        return mission

    def _resolve_current_mission(
        self, target: MissionEntry
    ) -> MissionEntry:
        current = next(
            (
                item
                for item in self.mission_entries
                if item.queue_index == target.queue_index
            ),
            None,
        )
        if current is None:
            raise ValueError(
                f"mission queue target {target.queue_index} is missing"
            )
        if current.filename.casefold() != target.filename.casefold():
            raise ValueError(
                f"mission queue target {target.queue_index} changed from "
                f"{target.filename!r} to {current.filename!r}"
            )
        return current

    def _resolve_available_mission(self, value) -> AvailableMission:
        if isinstance(value, AvailableMission):
            return self._resolve_current_available_mission(value)
        filename = str(value).casefold()
        mission = next(
            (
                item
                for item in self.available_mission_entries
                if item.filename.casefold() == filename
            ),
            None,
        )
        if mission is None:
            raise ValueError(
                f"mission {value!r} is not in MISSION AVAILABLE snapshot"
            )
        return mission

    def _resolve_current_available_mission(
        self, target: AvailableMission
    ) -> AvailableMission:
        current = next(
            (
                item
                for item in self.available_mission_entries
                if item.catalog_index == target.catalog_index
            ),
            None,
        )
        if current is None:
            raise ValueError(
                f"available mission {target.catalog_index} is missing"
            )
        if current.filename.casefold() != target.filename.casefold():
            raise ValueError(
                f"available mission {target.catalog_index} changed from "
                f"{target.filename!r} to {current.filename!r}"
            )
        return current

    # ---- weapon operations ----------------------------------------------

    def set_weapon(self, id_or_entry, mode) -> Future:
        target = self._resolve_weapon(id_or_entry)
        expected_mode = _weapon_mode(mode)

        def workflow(session: RetailAdminSession) -> Future:
            weapon = self._resolve_current_weapon(target)
            return self._execute(
                AdminCommands.set_weapon(weapon, expected_mode),
                confirm=self.refresh_weapons,
                verify=lambda ack, readback: (
                    _ack_is(ack, "OK - Weapon availbility changed.")
                    and (
                        (current := _weapon_by_id(
                            readback.value, weapon.admdef_id
                        ))
                        is not None
                        and current.mode is expected_mode
                    )
                ),
                expected_session=session,
            )

        return self._serialize_mutation(workflow)

    def set_all_weapons(self, mode) -> Future:
        expected_mode = _weapon_mode(mode)
        return self._serialize_mutation(
            lambda session: self._execute(
                AdminCommands.set_all_weapons(expected_mode),
                confirm=self.refresh_weapons,
                verify=lambda ack, readback: (
                    _ack_is(
                        ack,
                        "OK - All weapons availbility changed.",
                    )
                    and bool(readback.value)
                    and all(
                        weapon.mode is expected_mode
                        for weapon in readback.value
                    )
                ),
                expected_session=session,
            )
        )

    def _resolve_weapon(self, value) -> WeaponEntry:
        if isinstance(value, WeaponEntry):
            return self._resolve_current_weapon(value)
        try:
            admdef_id = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError("weapon target must be a retail AdmDef id") from error
        weapon = next(
            (item for item in self.weapon_entries if item.admdef_id == admdef_id),
            None,
        )
        if weapon is None:
            raise ValueError(
                f"weapon AdmDef id {admdef_id} is not in the current snapshot"
            )
        return weapon

    def _resolve_current_weapon(
        self, target: WeaponEntry
    ) -> WeaponEntry:
        current = _weapon_by_id(self.weapon_entries, target.admdef_id)
        if current is None:
            raise ValueError(
                f"weapon AdmDef id {target.admdef_id} is missing"
            )
        if current.name != target.name:
            raise ValueError(
                f"weapon AdmDef id {target.admdef_id} changed from "
                f"{target.name!r} to {current.name!r}"
            )
        return current

    # ---- CMD operations (retail exposes acceptance, not command output) --

    def set_time_of_day(self, hhmm) -> Future:
        spec = AdminCommands.tod(str(hhmm))
        return self._serialize_mutation(
            lambda session: self._execute(
                spec, expected_session=session
            )
        )

    def set_time_rate(self, minutes) -> Future:
        spec = AdminCommands.tod_rate(minutes)
        return self._serialize_mutation(
            lambda session: self._execute(
                spec, expected_session=session
            )
        )

    # ---- team workflows --------------------------------------------------

    @staticmethod
    def _completed_team_workflow(message: str) -> TeamWorkflow:
        return TeamWorkflow(
            (message,),
            completed_future(
                CommandResult(
                    AdminOperation.PLAYER_SWAPTEAM,
                    (message,),
                    True,
                    verified=True,
                )
            ),
        )

    def mix_teams(self) -> TeamWorkflow:
        if self.swaps_blocked():
            return self._completed_team_workflow(COOP_REFUSAL)
        players = [
            player
            for player in self.player_entries
            if player.server_id != 0
        ]
        team_one = [player for player in players if player.team == 1]
        team_two = [player for player in players if player.team == 2]
        if len(team_one) + len(team_two) < 2:
            return self._completed_team_workflow(
                "Need at least 2 players to mix"
            )
        if abs(len(team_one) - len(team_two)) <= 1:
            return self._completed_team_workflow(
                "Teams are already balanced (within 1 player)"
            )
        larger = team_one if len(team_one) > len(team_two) else team_two
        moves = random.sample(
            larger, (abs(len(team_one) - len(team_two))) // 2
        )
        targets = tuple(moves)
        expected_roster = tuple(players)
        completion = self._serialize_mutation(
            lambda session: self._move_players_now(
                targets, session, expected_roster
            )
        )
        return TeamWorkflow(
            tuple(f"Swapping {player.name}" for player in moves),
            completion,
        )

    def shuffle_teams(self) -> TeamWorkflow:
        """Randomly shuffle ALL players across both teams."""
        if self.swaps_blocked():
            return self._completed_team_workflow(COOP_REFUSAL)
        all_players = [
            player
            for player in self.player_entries
            if player.server_id != 0
        ]
        if len(all_players) < 2:
            return self._completed_team_workflow("No players to shuffle")

        team1 = [p for p in all_players if p.team == 1]
        team2 = [p for p in all_players if p.team == 2]

        if not team1 or not team2:
            return self._completed_team_workflow(
                "Need players on both teams to shuffle"
            )

        swap_count = min(len(team1), len(team2))
        from_team1 = random.sample(team1, swap_count)
        from_team2 = random.sample(team2, swap_count)
        targets = tuple(from_team1 + from_team2)

        names = [p.name for p in targets]
        name_list = ", ".join(names)

        # Pre-shuffle announcement
        self.send_chat(f"Mixing teams: {name_list}")

        expected_roster = tuple(all_players)
        completion = self._serialize_mutation(
            lambda session: self._move_players_now(
                targets, session, expected_roster
            )
        )

        # Chain closing chat after all moves complete
        completion = self._then(
            completion,
            lambda _result: self.send_chat(
                "Teams shuffled! Good luck all."
            ),
        )

        return TeamWorkflow(
            tuple(f"Swapping {player.name}" for player in targets),
            completion,
        )

    def get_team_stats(self):
        players = [
            player
            for player in self.player_entries
            if player.server_id != 0
        ]
        team_one = [player for player in players if player.team == 1]
        team_two = [player for player in players if player.team == 2]
        score_one = sum(player.kills or 0 for player in team_one)
        score_two = sum(player.kills or 0 for player in team_two)
        return {
            "team_a_count": len(team_one),
            "team_b_count": len(team_two),
            "team_a_score": score_one,
            "team_b_score": score_two,
            "difference": abs(len(team_one) - len(team_two)),
            "score_diff": abs(score_one - score_two),
        }

    # ---- timer-driven polling -------------------------------------------

    def start_polling(self, interval=5.0) -> None:
        with self._lock:
            self._poll_interval = float(interval)
            if self._closed or self._session is None or self._polling:
                return
            session = self._session
            generation = self._connection_generation
            self._polling = True
            self._last_poll_success = _time.time()
        self._schedule_poll(self._poll_interval, session, generation)
        self._start_stale_watchdog()

    def stop_polling(self) -> None:
        with self._lock:
            self._polling = False
            timer, self._poll_timer = self._poll_timer, None
            stale_timer, self._stale_watchdog_timer = self._stale_watchdog_timer, None
        if timer is not None:
            timer.cancel()
        if stale_timer is not None:
            stale_timer.cancel()

    def _schedule_poll(
        self,
        delay: float,
        session: RetailAdminSession,
        generation: int,
    ) -> None:
        timer = threading.Timer(
            delay,
            lambda: self._poll_tick(session, generation),
        )
        timer.daemon = True
        with self._lock:
            if (
                not self._polling
                or self._closed
                or self._connection_generation != generation
                or self._session is not session
                or not session.connected
            ):
                return
            self._poll_timer = timer
        timer.start()

    def _start_stale_watchdog(self) -> None:
        """Start a watchdog that detects when the server stops responding."""
        with self._lock:
            if self._closed or not self._polling:
                return
            timer = threading.Timer(5.0, self._stale_watchdog_tick)
            timer.daemon = True
            self._stale_watchdog_timer = timer
        timer.start()

    def _stale_watchdog_tick(self) -> None:
        """Check if the server has stopped responding to polls."""
        with self._lock:
            if not self._polling or self._closed:
                return
            elapsed = _time.time() - self._last_poll_success
            if elapsed < self._stale_threshold:
                # Server is responding, schedule next check
                timer = threading.Timer(5.0, self._stale_watchdog_tick)
                timer.daemon = True
                self._stale_watchdog_timer = timer
                timer.start()
                return
        # Server is stale — trigger disconnect flow
        self._log(
            f"Server not responding for {elapsed:.0f}s "
            f"(threshold: {self._stale_threshold:.0f}s) — treating as disconnected"
        )
        # Tear the connection down for real, then tell the UI. disconnect()
        # closes the socket, clears _session and bumps the connection
        # generation, so ServerManager.is_connected becomes False. Without it
        # the UI reconnect tick sees is_connected == True and refuses to
        # reconnect, stranding the client silently.
        self.disconnect()
        if self._on_disconnect_ui:
            self._on_disconnect_ui()

    def _poll_tick(
        self,
        session: RetailAdminSession,
        generation: int,
    ) -> None:
        with self._lock:
            if (
                not self._polling
                or self._closed
                or self._connection_generation != generation
                or self._session is not session
                or not session.connected
            ):
                return
        try:
            futures = (
                self._execute(
                    AdminCommands.game_state(),
                    quiet=True,
                    expected_session=session,
                ),
                self._execute(
                    AdminCommands.players(),
                    quiet=True,
                    expected_session=session,
                ),
                self._execute(
                    AdminCommands.chat(),
                    quiet=True,
                    expected_session=session,
                ),
                self._execute(
                    AdminCommands.missions(),
                    quiet=True,
                    expected_session=session,
                ),
                self._execute(
                    AdminCommands.game_settings(),
                    quiet=True,
                    expected_session=session,
                ),
            )
        except ConnectionError:
            # Keep the loop alive. _schedule_poll bails out by itself if the
            # session is genuinely gone, and the watchdog then takes over.
            self._schedule_poll(self._poll_interval, session, generation)
            return

        def _on_poll_cycle_done(_future):
            """Record poll success and schedule next cycle."""
            # Any accepted command proves the server is alive. Keying this off
            # game_settings alone meant one rejected settings read looked
            # identical to a dead server.
            for poll_future in futures:
                try:
                    result = poll_future.result(timeout=0)
                except Exception:
                    continue
                if getattr(result, 'accepted', True):
                    with self._lock:
                        self._last_poll_success = _time.time()
                    break
            self._schedule_poll(
                self._poll_interval,
                session,
                generation,
            )
        futures[-1].add_done_callback(_on_poll_cycle_done)

    # ---- result adaptation ----------------------------------------------

    def _apply_result(self, result: CommandResult, *, quiet: bool = False) -> None:
        log_prefix = "__QUIET__" if quiet else ""
        if not result.accepted:
            for reply in result.replies:
                text = _redact_setting_lines(reply)
                self._log(
                    f"{log_prefix}<< {text}",
                    to_file=not quiet or self._wire_changed(result.operation, text),
                )
            return
        value = result.value
        operation = result.operation
        if operation is AdminOperation.PLAYER_LIST:
            self.players = [
                _legacy_player(item) for item in value if item.server_id != 0
            ]
            if self._on_players:
                self._on_players(self.players)
        elif operation is AdminOperation.MISSION_LIST:
            self.missions = [_legacy_mission(item) for item in value]
            if self._on_missions:
                self._on_missions(self.missions)
        elif operation is AdminOperation.MISSION_AVAILABLE:
            self.available_maps_data = "\n".join(
                f"{item.catalog_index}. {item.filename} ({item.description})"
                for item in value
            )
            if self._on_available_maps:
                self._on_available_maps(self.available_maps_data)
        elif operation is AdminOperation.GET_GAMESETTINGS:
            settings = value.values if isinstance(value, GameSettings) else {}
            self.game_settings = {
                key.casefold(): setting for key, setting in settings.items()
            }
            if self._on_settings:
                self._on_settings(self.game_settings)
        elif operation is AdminOperation.GET_GAMESTATE:
            self.game_state = {"mode": str(value), "raw": str(value)}
            if self._on_gamestate:
                self._on_gamestate(self.game_state)
        elif operation is AdminOperation.CHAT_GET:
            self._apply_chat(tuple(value))
        elif operation is AdminOperation.WEAPON_LIST:
            self.weapons = [
                {
                    "id": item.admdef_id,
                    "name": item.name,
                    "mode": item.mode.value,
                }
                for item in value
            ]
            if self._on_weapons:
                self._on_weapons(list(self.weapon_entries))
        for reply in result.replies:
            text = _redact_setting_lines(reply)
            if quiet and operation is AdminOperation.CHAT_GET:
                # New chat lines are logged one by one by the Mods tab.
                to_file = False
            elif quiet and operation is AdminOperation.MISSION_LIST:
                to_file = self._wire_mission_list(text)
            else:
                to_file = not quiet or self._wire_changed(operation, text)
            self._log(f"{log_prefix}<< {text}", to_file=to_file)

    def _wire_changed(self, operation, text: str) -> bool:
        """True when a quiet reply differs from the last one written."""
        key = text
        if operation is AdminOperation.PLAYER_LIST:
            # Kills, deaths and ping move every poll; the roster (name,
            # slot, team) is what matters in the log.
            key = "\n".join(
                "\t".join(field.strip() for field in line.split("\t")[:3])
                for line in text.splitlines()
            )
        elif operation is AdminOperation.GET_GAMESETTINGS:
            # "GameTime = 23/30" counts down every minute; only the time
            # limit after the slash is a setting.
            key = "\n".join(
                "GameTime /" + line.partition("/")[2]
                if line.startswith("GameTime ") else line
                for line in text.splitlines()
            )
        with self._lock:
            if self._wire_seen.get(operation) == key:
                return False
            self._wire_seen[operation] = key
            return True

    def _wire_mission_list(self, text: str) -> bool:
        """Write a changed queue in full; a new current map as one line."""
        marker = "<CURRENT MISSION>"
        queue = text.replace(marker, "<>")
        with self._lock:
            last_queue, last_text = self._wire_seen.get(
                AdminOperation.MISSION_LIST, (None, None)
            )
            if text == last_text:
                return False
            self._wire_seen[AdminOperation.MISSION_LIST] = (queue, text)
        if queue != last_queue:
            return True
        current = next(
            (line.split(" - ")[0] for line in text.splitlines() if marker in line),
            "none",
        )
        count = len([line for line in text.splitlines() if line.strip()])
        wire_log(f"Mission queue unchanged ({count} maps), current is now {current}")
        return False

    def _apply_chat(self, lines: tuple[str, ...]) -> None:
        raw = "\n".join(lines)
        if self._on_raw_chat:
            self._on_raw_chat(raw)
        overlap = 0
        maximum = min(len(self._last_raw_chat_lines), len(lines))
        for size in range(maximum, 0, -1):
            if self._last_raw_chat_lines[-size:] == lines[:size]:
                overlap = size
                break
        for line in lines[overlap:]:
            self._chat_message_id += 1
            self.chat_messages.append({
                "id": self._chat_message_id,
                "time": "",
                "text": line,
                "raw": line,
            })
        self._last_raw_chat_lines = lines
        self.chat_messages = self.chat_messages[-500:]
        if self._on_chat:
            self._on_chat(self.chat_messages)

    def _require_session(
        self,
        expected_session: Optional[RetailAdminSession] = None,
    ) -> RetailAdminSession:
        session = self._session
        if session is None:
            raise ConnectionError("not connected to a retail admin session")
        if (
            expected_session is not None
            and session is not expected_session
        ):
            raise ConnectionError(
                "retail admin session changed before workflow completed"
            )
        return session

    def _log(self, message: str, *, to_file: bool = True) -> None:
        if to_file:
            wire_log(message)
        if self._on_log:
            self._on_log(message)


def _settle_future(
    future: Future,
    *,
    result: object = None,
    error: BaseException | None = None,
) -> None:
    """Complete an observer unless cancellation already detached it."""

    if future.done():
        return
    try:
        if error is None:
            future.set_result(result)
        else:
            future.set_exception(error)
    except InvalidStateError:
        pass


def _result_succeeded(result: object) -> bool:
    if isinstance(result, CommandResult):
        return result.accepted and result.verified is True
    return (
        getattr(result, "accepted", True) is not False
        and getattr(result, "verified", None) is not False
    )


def _ack_is(result: CommandResult, expected: str) -> bool:
    return tuple(reply.strip() for reply in result.replies) == (expected,)


def _terminal_ack_is(result: CommandResult, expected: str) -> bool:
    return bool(result.replies) and result.replies[-1].strip() == expected


def _player_by_id(
    players: Iterable[PlayerEntry], server_id: int
) -> PlayerEntry | None:
    return next(
        (player for player in players if player.server_id == server_id),
        None,
    )


def _kill_readback_matches(
    before: PlayerEntry, players: Iterable[PlayerEntry]
) -> bool:
    current = _player_by_id(players, before.server_id)
    if current is None:
        return False
    if before.deaths is None or current.deaths is None:
        # Some retail PLAYER LIST layouts omit counters. In that layout the
        # command-specific KILL acknowledgement is the only direct evidence.
        return True
    return current.deaths > before.deaths


def _zero_readback_matches(
    server_id: int, players: Iterable[PlayerEntry]
) -> bool:
    current = _player_by_id(players, server_id)
    if current is None:
        return False
    if current.kills is None and current.deaths is None:
        return False
    return current.kills in (None, 0) and current.deaths in (None, 0)


def _weapon_by_id(
    weapons: Iterable[WeaponEntry], admdef_id: int
) -> WeaponEntry | None:
    return next(
        (weapon for weapon in weapons if weapon.admdef_id == admdef_id),
        None,
    )


def _mission_add_readback_matches(
    before: tuple[MissionEntry, ...],
    after: Iterable[MissionEntry],
    filename: str,
    *,
    expected_index: int,
    expected_one_shot: bool,
    expected_double_time: bool | None,
) -> bool:
    after = tuple(after)
    filename = filename.casefold()
    if (
        sum(item.filename.casefold() == filename for item in after)
        != sum(item.filename.casefold() == filename for item in before) + 1
    ):
        return False
    added = next(
        (
            item
            for item in after
            if item.queue_index == expected_index
            and item.filename.casefold() == filename
        ),
        None,
    )
    if added is None or added.one_shot is not expected_one_shot:
        return False
    return (
        expected_double_time is None
        or added.double_time is expected_double_time
    )


def _setting_matches(
    key: str, expected: str, settings: object
) -> bool:
    if not isinstance(settings, GameSettings):
        return False
    actual = settings.get(key)
    if actual is None:
        return False
    kind = SETTING_SCHEMA[key]
    if kind == "boolean":
        truthy = {"1", "true", "yes", "on"}
        return (
            str(actual).strip().casefold() in truthy
        ) == (
            str(expected).strip().casefold() in truthy
        )
    if kind == "number":
        try:
            return float(actual) == float(expected)
        except (TypeError, ValueError):
            return False
    if key == "GameTime":
        actual = str(actual).rsplit("/", 1)[-1]
    if kind == "integer":
        try:
            return int(float(actual)) == int(expected)
        except (TypeError, ValueError):
            return False
    return str(actual) == str(expected)


def _split_chat_message(message: str) -> tuple[str, ...]:
    """Split chat without crossing retail's byte or dispatcher-token caps."""

    if not isinstance(message, str):
        raise TypeError("chat message must be text")
    if not message or not message.split():
        raise ValueError("command argument cannot be empty")
    if any(character in message for character in "\x00\r\n"):
        raise ValueError("command argument cannot contain NUL or line breaks")
    try:
        message.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("retail command arguments are ASCII only") from error

    limit = chat_limit()
    chunks: list[str] = []
    words: list[str] = []
    length = 0
    for word in message.split():
        if len(word) > limit:
            raise ValueError(
                f"chat word cannot exceed {limit} characters"
            )
        added_length = len(word) + (1 if words else 0)
        if (
            words
            and (
                len(words) >= MAX_CHAT_TOKENS
                or length + added_length > limit
            )
        ):
            chunks.append(" ".join(words))
            words = []
            length = 0
            added_length = len(word)
        words.append(word)
        length += added_length
    if words:
        chunks.append(" ".join(words))
    return tuple(chunks)


def completed_future(value) -> Future:
    future: Future = Future()
    future.set_result(value)
    return future


def _redact_raw_command(command: str) -> str:
    parts = command.split(None, 2)
    if (
        len(parts) == 3
        and parts[0].casefold() == "set"
        and parts[1].casefold() in _SECRET_SETTING_NAMES
    ):
        return f"{parts[0]} {parts[1]} <redacted>"
    return command


def _redact_setting_lines(reply: str) -> str:
    redacted: list[str] = []
    for line in reply.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        ending = line[len(content):]
        key, separator, _value = content.partition("=")
        if separator and key.strip().casefold() in _SECRET_SETTING_NAMES:
            redacted.append(f"{key}= <redacted>{ending}")
        else:
            redacted.append(line)
    return "".join(redacted)


def _weapon_mode(value) -> WeaponMode:
    if isinstance(value, WeaponMode):
        return value
    try:
        return WeaponMode[str(value).upper()]
    except KeyError as error:
        raise ValueError("weapon mode must be ALWAYS, NEVER, or ARMORY") from error


def player_entry_from_legacy(
    player: Mapping[str, object],
) -> PlayerEntry:
    """Rebuild the exact immutable identity carried by a displayed row."""

    if not isinstance(player, Mapping):
        raise TypeError("displayed player must be a mapping")
    try:
        server_id = int(player["id"])
        team = int(player["team"])
        revision = int(player["revision"])
    except KeyError as error:
        raise ValueError(
            f"displayed player is missing {error.args[0]!r}"
        ) from error
    except (TypeError, ValueError) as error:
        raise ValueError(
            "displayed player id, team, and revision must be integers"
        ) from error
    name = str(player.get("name", ""))
    if not name:
        raise ValueError("displayed player name cannot be empty")
    if revision <= 0:
        raise ValueError(
            "displayed player has no authoritative snapshot revision"
        )

    def optional_integer(key: str) -> Optional[int]:
        raw = player.get(key)
        if raw in (None, "", "-"):
            return None
        try:
            return int(raw)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"displayed player {key} must be an integer"
            ) from error

    return PlayerEntry(
        server_id=server_id,
        name=name,
        team=team,
        player_class=str(player.get("class", "")),
        kills=optional_integer("kills"),
        deaths=optional_integer("deaths"),
        ping=optional_integer("ping"),
        revision=revision,
    )


def _legacy_player(player: PlayerEntry) -> dict[str, object]:
    team_name = (
        "Joint Ops" if player.team == 1
        else "Rebels" if player.team == 2
        else str(player.team)
    )
    return {
        "id": str(player.server_id),
        "name": player.name,
        "team": str(player.team),
        "team_name": team_name,
        "class": player.player_class,
        "kills": str(player.kills if player.kills is not None else 0),
        "score": str(player.kills if player.kills is not None else 0),
        "deaths": str(player.deaths if player.deaths is not None else "-"),
        "ping": str(player.ping if player.ping is not None else "-"),
        "revision": player.revision,
    }


def _legacy_mission(mission: MissionEntry) -> str:
    flags = (
        "(2x)" if mission.double_time else "()",
        "(IS FLIPPED)" if mission.is_flipped else "()",
        "(ONE_SHOT)" if mission.one_shot else "()",
        "<CURRENT MISSION>" if mission.is_current else "<>",
        "<NEXT MISSION>" if mission.is_next else "<>",
    )
    return (
        f"{mission.queue_index}: {mission.filename} - "
        + " ".join(flags)
    )


__all__ = [
    "CHAT_MAX_LEN",
    "ServerManager",
    "player_entry_from_legacy",
    "wire_log",
]
