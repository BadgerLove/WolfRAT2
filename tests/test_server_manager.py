import inspect
import threading
import unittest
from concurrent.futures import Future
from dataclasses import replace
from unittest.mock import patch

from wolfrat.admin_commands import (
    AdminOperation,
    AdminSnapshot,
    AvailableMission,
    GameSettings,
    MissionEntry,
    PlayerEntry,
    WeaponEntry,
    WeaponMode,
)
from wolfrat.admin_session import CommandResult, RawResult, RequestPriority
from wolfrat.protocol import ServerManager, player_entry_from_legacy


def completed(value):
    future = Future()
    future.set_result(value)
    return future


class FakeSession:
    def __init__(self, host, port=4000, username="", password=""):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.connected = False
        self.closed = False
        self.specs = []
        self.schedules = []
        self.raw = []
        self.result_overrides = {}
        self.raw_overrides = []
        self.snapshot = AdminSnapshot(
            revision=4,
            settings=GameSettings({"ServerName": "Dev"}, revision=2),
            players=(
                PlayerEntry(0, "biggy", 0, revision=3),
                PlayerEntry(12, "Alice", 1, kills=4, deaths=2, ping=55, revision=3),
            ),
            missions=(
                MissionEntry(4, "CP08.BMS", is_current=True, revision=4),
            ),
            available_missions=(
                AvailableMission(7, "CP08.BMS", "Copperhead", revision=5),
                AvailableMission(8, "DM-DUST.NPZ", "Dust", revision=5),
            ),
            weapons=(
                WeaponEntry(37, "M4", WeaponMode.ARMORY, revision=6),
            ),
        )

    def execute(self, spec, **schedule):
        self.connected = True
        self.specs.append(spec)
        self.schedules.append(schedule)
        overrides = self.result_overrides.get(spec.operation, [])
        if overrides:
            override = overrides.pop(0)
            if isinstance(override, Future):
                return override
            if isinstance(override, BaseException):
                future = Future()
                future.set_exception(override)
                return future
            return completed(override)
        operation = spec.operation
        revision = self.snapshot.revision + 1
        reply = "OK"
        if operation is AdminOperation.SET_SETTING:
            _set, key, *value = spec.text.split(" ", 2)
            updated = dict(self.snapshot.settings.values)
            updated[key] = value[0] if value else ""
            self.snapshot = replace(
                self.snapshot,
                revision=revision,
                settings=GameSettings(updated, revision=revision),
            )
            reply = "OK - Setting Changed."
        elif operation in {
            AdminOperation.PLAYER_PUNT,
            AdminOperation.PLAYER_BAN,
        }:
            self.snapshot = replace(
                self.snapshot,
                revision=revision,
                players=tuple(
                    replace(player, revision=revision)
                    for player in self.snapshot.players
                    if player.server_id != spec.identity.key
                ),
            )
            reply = (
                "OK - Player punted."
                if operation is AdminOperation.PLAYER_PUNT
                else "OK - Player Banned."
            )
        elif operation in {
            AdminOperation.PLAYER_KILL,
            AdminOperation.PLAYER_SWAPTEAM,
            AdminOperation.PLAYER_ZEROSCORE,
        }:
            updated_players = []
            for player in self.snapshot.players:
                if player.server_id != spec.identity.key:
                    updated_players.append(replace(player, revision=revision))
                elif operation is AdminOperation.PLAYER_KILL:
                    updated_players.append(replace(
                        player,
                        deaths=(player.deaths or 0) + 1,
                        revision=revision,
                    ))
                elif operation is AdminOperation.PLAYER_SWAPTEAM:
                    updated_players.append(replace(
                        player,
                        team=2 if player.team == 1 else 1,
                        revision=revision,
                    ))
                else:
                    updated_players.append(replace(
                        player, kills=0, deaths=0, revision=revision
                    ))
            self.snapshot = replace(
                self.snapshot,
                revision=revision,
                players=tuple(updated_players),
            )
            reply = {
                AdminOperation.PLAYER_KILL: "OK - Player Killed.",
                AdminOperation.PLAYER_SWAPTEAM: "OK - Player Swapped.",
                AdminOperation.PLAYER_ZEROSCORE: "OK - Player Zeroed.",
            }[operation]
        elif operation is AdminOperation.MISSION_ADD:
            available = next(
                item
                for item in self.snapshot.available_missions
                if item.catalog_index == spec.identity.key
            )
            next_index = max(
                (item.queue_index for item in self.snapshot.missions),
                default=-1,
            ) + 1
            self.snapshot = replace(
                self.snapshot,
                revision=revision,
                missions=self.snapshot.missions + (
                    MissionEntry(
                        next_index,
                        available.filename,
                        revision=revision,
                    ),
                ),
            )
            reply = "OK - Entry Added"
        elif operation is AdminOperation.MISSION_REMOVE:
            self.snapshot = replace(
                self.snapshot,
                revision=revision,
                missions=tuple(
                    replace(mission, revision=revision)
                    for mission in self.snapshot.missions
                    if mission.queue_index != spec.identity.key
                ),
            )
            reply = "OK - Mission Removed."
        elif operation is AdminOperation.MISSION_CLEAR:
            self.snapshot = replace(
                self.snapshot, revision=revision, missions=()
            )
            reply = "OK - Mission list reset."
        elif operation is AdminOperation.MISSION_SETNEXT:
            self.snapshot = replace(
                self.snapshot,
                revision=revision,
                missions=tuple(
                    replace(
                        mission,
                        is_next=mission.queue_index == spec.identity.key,
                        revision=revision,
                    )
                    for mission in self.snapshot.missions
                ),
            )
            reply = "OK - Next Mission Set."
        elif operation is AdminOperation.MISSION_CYCLE:
            reply = "OK - Server is cycling..."
        elif operation is AdminOperation.WEAPON_SET:
            _weapon, _set, target, mode = spec.text.split()
            self.snapshot = replace(
                self.snapshot,
                revision=revision,
                weapons=tuple(
                    replace(
                        weapon,
                        mode=(
                            WeaponMode[mode]
                            if target == "ALL"
                            or weapon.admdef_id == int(target)
                            else weapon.mode
                        ),
                        revision=revision,
                    )
                    for weapon in self.snapshot.weapons
                ),
            )
            reply = (
                "OK - All weapons availbility changed."
                if target == "ALL"
                else "OK - Weapon availbility changed."
            )
        elif operation is AdminOperation.CHAT_SEND:
            reply = "OK - Chat sent."
        value = {
            AdminOperation.GET_GAMESTATE: "Game",
            AdminOperation.GET_GAMESETTINGS: self.snapshot.settings,
            AdminOperation.PLAYER_LIST: self.snapshot.players,
            AdminOperation.MISSION_LIST: self.snapshot.missions,
            AdminOperation.MISSION_AVAILABLE: self.snapshot.available_missions,
            AdminOperation.WEAPON_LIST: self.snapshot.weapons,
            AdminOperation.CHAT_GET: ("Alice: hi",),
        }.get(spec.operation)
        if value is not None:
            self.snapshot = self.snapshot.apply(spec.operation, value)
        return completed(CommandResult(spec.operation, (reply,), True, value))

    def execute_raw(self, text):
        self.raw.append(text)
        if self.raw_overrides:
            override = self.raw_overrides.pop(0)
            if isinstance(override, BaseException):
                future = Future()
                future.set_exception(override)
                return future
            return completed(override)
        return completed(RawResult(text, ("OK",), True))

    def close(self):
        self.connected = False
        self.closed = True


class ServerManagerFacadeTests(unittest.TestCase):
    def setUp(self):
        self.sessions = []

        def factory(*args, **kwargs):
            session = FakeSession(*args, **kwargs)
            self.sessions.append(session)
            return session

        self.manager = ServerManager(
            session_factory=factory,
            verification_attempts=3,
            verification_delay=0,
        )
        success, _ = self.manager.connect("127.0.0.1", 4000, "admin", "pw")
        self.assertTrue(success)
        self.session = self.sessions[-1]
        self.session.specs.clear()
        self.session.schedules.clear()

    def tearDown(self):
        self.manager.stop_polling()
        self.manager.disconnect()

    def test_facade_has_no_generic_send_bypass(self):
        self.assertFalse(hasattr(self.manager, "send"))
        self.assertFalse(hasattr(self.manager, "send_command"))
        self.assertFalse(hasattr(self.manager.proto, "send"))
        self.assertTrue(self.manager.is_connected)
        source = inspect.getsource(type(self.manager))
        self.assertNotIn("socket.", source)

    def test_close_invalidates_a_connection_attempt_before_it_can_be_adopted(self):
        entered_factory = threading.Event()
        release_factory = threading.Event()
        sessions = []

        def blocking_factory(*args, **kwargs):
            entered_factory.set()
            self.assertTrue(release_factory.wait(timeout=2))
            session = FakeSession(*args, **kwargs)
            sessions.append(session)
            return session

        manager = ServerManager(session_factory=blocking_factory)
        outcome = []
        worker = threading.Thread(
            target=lambda: outcome.append(
                manager.connect("127.0.0.1", 4000, "admin", "pw")
            )
        )
        worker.start()
        self.assertTrue(entered_factory.wait(timeout=2))

        manager.close()
        release_factory.set()
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(1, len(outcome))
        self.assertFalse(outcome[0][0])
        self.assertIn("closed", outcome[0][1].casefold())
        self.assertEqual(1, len(sessions))
        self.assertTrue(sessions[0].closed)
        self.assertEqual([], sessions[0].specs)
        self.assertFalse(manager.is_connected)

    def test_newest_overlapping_connect_owns_the_only_open_session(self):
        older_disconnected = threading.Event()
        release_older = threading.Event()
        sessions = []

        def factory(*args, **kwargs):
            session = FakeSession(*args, **kwargs)
            sessions.append(session)
            return session

        manager = ServerManager(session_factory=factory)
        original_disconnect = manager.disconnect

        def pause_older_after_disconnect():
            generation = original_disconnect()
            if threading.current_thread().name == "older-connect":
                older_disconnected.set()
                self.assertTrue(release_older.wait(timeout=2))
            return generation

        manager.disconnect = pause_older_after_disconnect
        outcomes = {}
        older = threading.Thread(
            name="older-connect",
            target=lambda: outcomes.setdefault(
                "older",
                manager.connect("older.example", 4000, "admin", "pw"),
            ),
        )
        newer = threading.Thread(
            name="newer-connect",
            target=lambda: outcomes.setdefault(
                "newer",
                manager.connect("newer.example", 4000, "admin", "pw"),
            ),
        )

        try:
            older.start()
            self.assertTrue(older_disconnected.wait(timeout=2))
            newer.start()
            newer.join(timeout=2)
            self.assertFalse(newer.is_alive())
            release_older.set()
            older.join(timeout=2)
            self.assertFalse(older.is_alive())

            by_host = {session.host: session for session in sessions}
            self.assertTrue(outcomes["newer"][0])
            self.assertFalse(outcomes["older"][0])
            self.assertFalse(by_host["newer.example"].closed)
            self.assertTrue(by_host["older.example"].closed)
        finally:
            release_older.set()
            older.join(timeout=2)
            newer.join(timeout=2)
            manager.close()

    def test_every_refresh_uses_a_typed_catalog_operation(self):
        futures = (
            self.manager.refresh_game_state(),
            self.manager.refresh_settings(),
            self.manager.refresh_players(),
            self.manager.refresh_missions(),
            self.manager.refresh_available_maps(),
            self.manager.refresh_weapons(),
            self.manager.refresh_chat(),
        )
        self.assertTrue(all(future.done() for future in futures))
        self.assertEqual(
            [spec.operation for spec in self.session.specs],
            [
                AdminOperation.GET_GAMESTATE,
                AdminOperation.GET_GAMESETTINGS,
                AdminOperation.PLAYER_LIST,
                AdminOperation.MISSION_LIST,
                AdminOperation.MISSION_AVAILABLE,
                AdminOperation.WEAPON_LIST,
                AdminOperation.CHAT_GET,
            ],
        )

    def test_callbacks_receive_legacy_shapes_from_typed_results(self):
        seen = {}
        self.manager.set_callbacks(
            on_players=lambda value: seen.setdefault("players", value),
            on_missions=lambda value: seen.setdefault("missions", value),
            on_settings=lambda value: seen.setdefault("settings", value),
            on_gamestate=lambda value: seen.setdefault("state", value),
            on_chat=lambda value: seen.setdefault("chat", value),
            on_available_maps=lambda value: seen.setdefault("available", value),
            on_weapons=lambda value: seen.setdefault("weapons", value),
        )
        raw = []
        self.manager.set_raw_chat_callback(raw.append)

        self.manager.refresh_all()

        self.assertEqual([player["id"] for player in seen["players"]], ["12"])
        self.assertIn("4: CP08.BMS", seen["missions"][0])
        self.assertEqual(seen["settings"]["servername"], "Dev")
        self.assertEqual(seen["state"]["mode"], "Game")
        self.assertEqual(seen["chat"][0]["text"], "Alice: hi")
        self.assertIn("7. CP08.BMS", seen["available"])
        self.assertIsInstance(seen["weapons"], list)
        self.assertEqual(37, seen["weapons"][0].admdef_id)
        self.assertEqual(raw, ["Alice: hi"])

    def test_legacy_players_expose_their_exact_snapshot_revision(self):
        self.manager.refresh_players().result()

        visible = self.manager.players[0]
        authoritative = self.manager.player_entries[1]
        self.assertEqual(authoritative.server_id, int(visible["id"]))
        self.assertEqual(authoritative.name, visible["name"])
        self.assertEqual(authoritative.revision, visible["revision"])
        self.assertEqual(
            authoritative,
            player_entry_from_legacy(visible),
        )

    def test_player_id_is_resolved_then_mutation_is_confirmed_by_list(self):
        self.manager.kill_player(12).result()
        self.assertEqual(
            [spec.operation for spec in self.session.specs],
            [AdminOperation.PLAYER_KILL, AdminOperation.PLAYER_LIST],
        )
        self.assertEqual(self.session.specs[0].identity.key, 12)

        with self.assertRaises(ValueError):
            self.manager.kill_player(0)
        with self.assertRaises(ValueError):
            self.manager.kill_player(99)

    def test_stale_player_revision_cannot_be_laundered_into_current_slot(self):
        displayed = self.manager.player_entries[1]
        newer_revision = displayed.revision + 1
        self.session.snapshot = replace(
            self.session.snapshot,
            revision=newer_revision,
            players=tuple(
                replace(player, revision=newer_revision)
                for player in self.session.snapshot.players
            ),
        )

        with self.assertRaisesRegex(ValueError, "revision"):
            self.manager.kill_player(displayed)

        self.assertEqual([], self.session.specs)

    def test_displayed_player_cannot_target_a_reused_numeric_slot(self):
        displayed = dict(self.manager.players[0])
        target = player_entry_from_legacy(displayed)
        replacement_revision = target.revision + 1
        host = self.session.snapshot.players[0]
        self.session.snapshot = replace(
            self.session.snapshot,
            revision=replacement_revision,
            players=(
                replace(host, revision=replacement_revision),
                PlayerEntry(
                    target.server_id,
                    "Bob",
                    2,
                    revision=replacement_revision,
                ),
            ),
        )

        with self.assertRaisesRegex(ValueError, "Alice.*Bob"):
            self.manager.kill_player(target)

        self.assertEqual([], self.session.specs)

    def test_queued_warning_rechecks_the_displayed_player_identity(self):
        displayed = player_entry_from_legacy(self.manager.players[0])
        blocker_ack = Future()
        self.session.result_overrides[AdminOperation.MISSION_CLEAR] = [
            blocker_ack
        ]

        blocker = self.manager.clear_missions()
        warning = self.manager.warn_player(
            displayed, "You have been warned!"
        )
        replacement_revision = displayed.revision + 1
        host = self.session.snapshot.players[0]
        self.session.snapshot = replace(
            self.session.snapshot,
            revision=replacement_revision,
            missions=(),
            players=(
                replace(host, revision=replacement_revision),
                PlayerEntry(
                    displayed.server_id,
                    "Bob",
                    2,
                    revision=replacement_revision,
                ),
            ),
        )
        blocker_ack.set_result(CommandResult(
            AdminOperation.MISSION_CLEAR,
            ("OK - Mission list reset.",),
            True,
        ))

        self.assertTrue(blocker.result().verified)
        with self.assertRaisesRegex(ValueError, "Alice.*Bob"):
            warning.result()
        self.assertEqual(
            [
                AdminOperation.MISSION_CLEAR,
                AdminOperation.MISSION_LIST,
            ],
            [spec.operation for spec in self.session.specs],
        )

    def test_mission_and_weapon_operations_resolve_authoritative_records(self):
        self.manager.switch_mission(4).result()
        self.assertEqual(
            [spec.operation for spec in self.session.specs],
            [
                AdminOperation.MISSION_SETNEXT,
                AdminOperation.MISSION_LIST,
                AdminOperation.MISSION_CYCLE,
            ],
        )
        self.session.specs.clear()

        self.manager.add_mission("CP08.BMS", auto_switch_sides=False).result()
        self.assertEqual(
            [spec.operation for spec in self.session.specs],
            [AdminOperation.MISSION_ADD, AdminOperation.MISSION_LIST],
        )
        self.session.specs.clear()

        self.manager.set_weapon(37, WeaponMode.NEVER).result()
        self.assertEqual(
            [spec.operation for spec in self.session.specs],
            [AdminOperation.WEAPON_SET, AdminOperation.WEAPON_LIST],
        )

    def test_add_mission_verifies_position_and_serialized_flags(self):
        before = (
            MissionEntry(0, "CP08.BMS", revision=7),
            MissionEntry(1, "CP10.BMS", revision=7),
        )
        self.session.snapshot = replace(
            self.session.snapshot,
            revision=7,
            missions=before,
        )
        acknowledgement = CommandResult(
            AdminOperation.MISSION_ADD,
            ("OK - Entry Added",),
            True,
        )
        wrong_readback = CommandResult(
            AdminOperation.MISSION_LIST,
            ("mission list",),
            True,
            (
                before[0],
                before[1],
                MissionEntry(
                    2,
                    "DM-DUST.NPZ",
                    one_shot=False,
                    double_time=False,
                    revision=8,
                ),
            ),
            verified=True,
        )
        self.session.result_overrides[AdminOperation.MISSION_ADD] = [
            acknowledgement
        ]
        self.session.result_overrides[AdminOperation.MISSION_LIST] = [
            wrong_readback
        ]

        wrong = self.manager.add_mission(
            "DM-DUST.NPZ",
            auto_switch_sides=True,
            insert_at=1,
            one_shot=True,
        ).result()

        self.assertTrue(wrong.accepted)
        self.assertFalse(wrong.verified)
        self.assertIn("readback", wrong.verification_error)

        applied = (
            before[0],
            MissionEntry(
                1,
                "DM-DUST.NPZ",
                one_shot=True,
                double_time=True,
                revision=9,
            ),
            replace(before[1], queue_index=2, revision=9),
        )
        self.session.snapshot = replace(
            self.session.snapshot,
            revision=9,
            missions=before,
        )
        self.session.result_overrides[AdminOperation.MISSION_ADD] = [
            acknowledgement
        ]
        self.session.result_overrides[AdminOperation.MISSION_LIST] = [
            CommandResult(
                AdminOperation.MISSION_LIST,
                ("mission list",),
                True,
                applied,
                verified=True,
            )
        ]

        correct = self.manager.add_mission(
            "DM-DUST.NPZ",
            auto_switch_sides=True,
            insert_at=1,
            one_shot=True,
        ).result()

        self.assertTrue(correct.verified)

    def test_filename_switch_adds_reads_back_resolves_then_cycles(self):
        self.manager.switch_mission(
            "DM-DUST.NPZ", add_if_missing=True
        ).result()
        self.assertEqual(
            [spec.operation for spec in self.session.specs],
            [
                AdminOperation.MISSION_ADD,
                AdminOperation.MISSION_LIST,
                AdminOperation.MISSION_SETNEXT,
                AdminOperation.MISSION_LIST,
                AdminOperation.MISSION_CYCLE,
            ],
        )
        setnext = next(
            spec
            for spec in self.session.specs
            if spec.operation is AdminOperation.MISSION_SETNEXT
        )
        self.assertEqual(setnext.identity.key, 5)

    def test_filename_setnext_never_guesses_when_map_is_missing(self):
        with self.assertRaises(ValueError):
            self.manager.set_next_mission("DM-DUST.NPZ")

        self.manager.set_next_mission(
            "DM-DUST.NPZ", add_if_missing=True
        ).result()
        self.assertEqual(
            [spec.operation for spec in self.session.specs],
            [
                AdminOperation.MISSION_ADD,
                AdminOperation.MISSION_LIST,
                AdminOperation.MISSION_SETNEXT,
                AdminOperation.MISSION_LIST,
            ],
        )

    def test_duplicate_mission_filename_requires_an_explicit_queue_target(self):
        self.session.snapshot = replace(
            self.session.snapshot,
            revision=7,
            missions=(
                MissionEntry(4, "CP08.BMS", revision=7),
                MissionEntry(5, "CP08.BMS", revision=7),
            ),
        )

        with self.assertRaisesRegex(
            ValueError, "ambiguous.*MissionEntry|ambiguous.*queue index"
        ):
            self.manager.set_next_mission(
                "CP08.BMS", add_if_missing=True
            )

        self.assertEqual([], self.session.specs)

    def test_setnext_add_if_missing_holds_one_gate(self):
        add_ack = Future()
        self.session.result_overrides[AdminOperation.MISSION_ADD] = [
            add_ack
        ]

        selected = self.manager.set_next_mission(
            "DM-DUST.NPZ", add_if_missing=True
        )
        setting = self.manager.set_setting("ServerName", "After")
        self.session.snapshot = replace(
            self.session.snapshot,
            revision=5,
            missions=self.session.snapshot.missions + (
                MissionEntry(
                    5,
                    "DM-DUST.NPZ",
                    double_time=False,
                    revision=5,
                ),
            ),
        )
        add_ack.set_result(CommandResult(
            AdminOperation.MISSION_ADD,
            ("OK - Entry Added",),
            True,
        ))

        self.assertTrue(selected.result().verified)
        self.assertTrue(setting.result().verified)
        self.assertEqual(
            [
                AdminOperation.MISSION_ADD,
                AdminOperation.MISSION_LIST,
                AdminOperation.MISSION_SETNEXT,
                AdminOperation.MISSION_LIST,
                AdminOperation.SET_SETTING,
                AdminOperation.GET_GAMESETTINGS,
            ],
            [spec.operation for spec in self.session.specs],
        )
        self.assertEqual(
            "MISSION ADD DM-DUST.NPZ 0",
            self.session.specs[0].text,
        )

    def test_rapid_mission_removals_cannot_retarget_a_shifted_queue_slot(self):
        original = MissionEntry(
            4, "CP08.BMS", is_current=True, revision=4
        )
        shifted = MissionEntry(5, "DM-DUST.NPZ", revision=4)
        self.session.snapshot = replace(
            self.session.snapshot,
            missions=(original, shifted),
        )
        first_ack = Future()
        self.session.result_overrides[AdminOperation.MISSION_REMOVE] = [
            first_ack
        ]

        first = self.manager.remove_mission(4)
        second = self.manager.remove_mission(4)

        self.assertEqual(
            [AdminOperation.MISSION_REMOVE],
            [spec.operation for spec in self.session.specs],
        )

        self.session.snapshot = replace(
            self.session.snapshot,
            revision=5,
            missions=(
                replace(shifted, queue_index=4, revision=5),
            ),
        )
        first_ack.set_result(CommandResult(
            AdminOperation.MISSION_REMOVE,
            ("OK - Mission Removed.",),
            True,
        ))

        self.assertTrue(first.result().verified)
        with self.assertRaisesRegex(ValueError, "changed|missing"):
            second.result()
        self.assertEqual(
            [
                AdminOperation.MISSION_REMOVE,
                AdminOperation.MISSION_LIST,
            ],
            [spec.operation for spec in self.session.specs],
        )

    def test_queued_duplicate_mission_target_is_not_upgraded_after_shift(self):
        leading = MissionEntry(3, "AS-LEAD.BMS", revision=4)
        selected = MissionEntry(4, "CP08.BMS", revision=4)
        duplicate = MissionEntry(5, "CP08.BMS", revision=4)
        self.session.snapshot = replace(
            self.session.snapshot,
            revision=4,
            missions=(leading, selected, duplicate),
        )
        first_ack = Future()
        self.session.result_overrides[AdminOperation.MISSION_REMOVE] = [
            first_ack
        ]

        first = self.manager.remove_mission(leading)
        later = self.manager.remove_mission(selected)

        self.assertEqual(
            [AdminOperation.MISSION_REMOVE],
            [spec.operation for spec in self.session.specs],
        )

        # Removing the leading row shifts both identical filenames. Queue slot
        # 4 still says CP08.BMS, but it is now the other occurrence.
        self.session.snapshot = replace(
            self.session.snapshot,
            revision=5,
            missions=(
                replace(selected, queue_index=3, revision=5),
                replace(duplicate, queue_index=4, revision=5),
            ),
        )
        first_ack.set_result(CommandResult(
            AdminOperation.MISSION_REMOVE,
            ("OK - Mission Removed.",),
            True,
        ))

        self.assertTrue(first.result().verified)
        with self.assertRaisesRegex(ValueError, "revision|stale"):
            later.result()
        self.assertEqual(
            [
                AdminOperation.MISSION_REMOVE,
                AdminOperation.MISSION_LIST,
            ],
            [spec.operation for spec in self.session.specs],
            "the stale target must fail before a second REMOVE is sent",
        )

    def test_cancelling_a_queued_mutation_observer_does_not_cancel_its_workflow(self):
        mutation_ack = Future()
        self.session.result_overrides[AdminOperation.MISSION_CLEAR] = [
            mutation_ack
        ]

        observer = self.manager.clear_missions()
        following = self.manager.cycle_mission()
        self.assertTrue(observer.cancel())
        self.assertEqual(
            [AdminOperation.MISSION_CLEAR],
            [spec.operation for spec in self.session.specs],
        )

        self.session.snapshot = replace(
            self.session.snapshot,
            revision=5,
            missions=(),
        )
        mutation_ack.set_result(CommandResult(
            AdminOperation.MISSION_CLEAR,
            ("OK - Mission list reset.",),
            True,
        ))

        self.assertTrue(observer.cancelled())
        self.assertTrue(following.result().verified)
        self.assertEqual(
            [
                AdminOperation.MISSION_CLEAR,
                AdminOperation.MISSION_LIST,
                AdminOperation.MISSION_CYCLE,
            ],
            [spec.operation for spec in self.session.specs],
        )

    def test_send_chat_splits_on_retail_character_and_token_limits(self):
        message = " ".join("x" for _ in range(24))

        result = self.manager.send_chat(message).result()

        commands = [
            spec.text.removeprefix("CHAT SEND ")
            for spec in self.session.specs
        ]
        self.assertTrue(result.verified)
        self.assertEqual(2, len(commands))
        self.assertEqual(message.split(), " ".join(commands).split())
        self.assertTrue(all(len(command) <= 62 for command in commands))
        self.assertTrue(
            all(len(command.split()) <= 23 for command in commands)
        )

    def test_raw_console_is_the_only_raw_entry_point(self):
        result = self.manager.execute_raw("GET GAMESTATE").result()
        self.assertTrue(result.accepted)
        self.assertEqual(self.session.raw, ["GET GAMESTATE"])

    def test_raw_console_waits_for_a_typed_mutation_confirmation(self):
        mutation_ack = Future()
        self.session.result_overrides[AdminOperation.MISSION_CLEAR] = [
            mutation_ack
        ]

        typed = self.manager.clear_missions()
        raw = self.manager.execute_raw("MISSION CLEAR")

        self.assertEqual([], self.session.raw)
        self.session.snapshot = replace(
            self.session.snapshot,
            revision=5,
            missions=(),
        )
        mutation_ack.set_result(CommandResult(
            AdminOperation.MISSION_CLEAR,
            ("OK - Mission list reset.",),
            True,
        ))

        self.assertTrue(typed.result().verified)
        self.assertTrue(raw.result().accepted)
        self.assertEqual(["MISSION CLEAR"], self.session.raw)

    def test_queued_workflows_never_migrate_to_a_reconnected_session(self):
        mutation_ack = Future()
        self.session.result_overrides[AdminOperation.MISSION_CLEAR] = [
            mutation_ack
        ]
        accepted = self.manager.clear_missions()
        queued = self.manager.cycle_mission()
        old_session = self.session

        success, _message = self.manager.connect(
            "127.0.0.2", 4000, "admin", "pw"
        )

        self.assertTrue(success)
        new_session = self.sessions[-1]
        before_completion = tuple(new_session.specs)
        mutation_ack.set_result(CommandResult(
            AdminOperation.MISSION_CLEAR,
            ("OK - Mission list reset.",),
            True,
        ))

        uncertain = accepted.result()
        self.assertTrue(uncertain.accepted)
        self.assertFalse(uncertain.verified)
        self.assertIn("session", uncertain.verification_error)
        with self.assertRaisesRegex(ConnectionError, "session"):
            queued.result()
        self.assertEqual(
            [AdminOperation.MISSION_CLEAR],
            [spec.operation for spec in old_session.specs],
        )
        self.assertEqual(before_completion, tuple(new_session.specs))
        self.assertNotIn(
            AdminOperation.MISSION_CYCLE,
            [spec.operation for spec in new_session.specs],
        )

    def test_old_session_read_completion_cannot_overwrite_new_ui_state(self):
        old_readback = Future()
        self.session.result_overrides[AdminOperation.PLAYER_LIST] = [
            old_readback
        ]
        observed = []
        self.manager.set_callbacks(on_players=observed.append)
        old_read = self.manager.refresh_players()

        success, _message = self.manager.connect(
            "127.0.0.2", 4000, "admin", "pw"
        )

        self.assertTrue(success)
        new_players = list(self.manager.players)
        callback_count = len(observed)
        old_readback.set_result(CommandResult(
            AdminOperation.PLAYER_LIST,
            ("stale player list",),
            True,
            (
                PlayerEntry(
                    99,
                    "Stale",
                    2,
                    revision=99,
                ),
            ),
            verified=True,
        ))
        self.assertTrue(old_read.result().accepted)
        self.assertEqual(new_players, self.manager.players)
        self.assertEqual(callback_count, len(observed))

    def test_swap_and_kill_holds_one_gate_against_other_mutations(self):
        swap_ack = Future()
        self.session.result_overrides[AdminOperation.PLAYER_SWAPTEAM] = [
            swap_ack
        ]

        combined = self.manager.swap_and_kill(12)
        setting = self.manager.set_setting("ServerName", "After")
        self.session.snapshot = replace(
            self.session.snapshot,
            revision=5,
            players=tuple(
                replace(
                    player,
                    team=2 if player.server_id == 12 else player.team,
                    revision=5,
                )
                for player in self.session.snapshot.players
            ),
        )
        swap_ack.set_result(CommandResult(
            AdminOperation.PLAYER_SWAPTEAM,
            ("OK - Player Swapped.",),
            True,
        ))

        self.assertTrue(combined.result().verified)
        self.assertTrue(setting.result().verified)
        # Kill comes first: killed after the swap, JO blames the victim's
        # last attacker, now a teammate, for a team kill (and punts them).
        self.assertEqual(
            [
                AdminOperation.PLAYER_KILL,
                AdminOperation.PLAYER_LIST,
                AdminOperation.PLAYER_SWAPTEAM,
                AdminOperation.PLAYER_LIST,
                AdminOperation.SET_SETTING,
                AdminOperation.GET_GAMESETTINGS,
            ],
            [spec.operation for spec in self.session.specs],
        )

    def test_switch_add_setnext_and_cycle_hold_one_gate(self):
        add_ack = Future()
        self.session.result_overrides[AdminOperation.MISSION_ADD] = [
            add_ack
        ]

        switched = self.manager.switch_mission(
            "DM-DUST.NPZ", add_if_missing=True
        )
        setting = self.manager.set_setting("ServerName", "After")
        self.session.snapshot = replace(
            self.session.snapshot,
            revision=5,
            missions=self.session.snapshot.missions + (
                MissionEntry(
                    5,
                    "DM-DUST.NPZ",
                    double_time=False,
                    revision=5,
                ),
            ),
        )
        add_ack.set_result(CommandResult(
            AdminOperation.MISSION_ADD,
            ("OK - Entry Added",),
            True,
        ))

        self.assertTrue(switched.result().verified)
        self.assertTrue(setting.result().verified)
        self.assertEqual(
            [
                AdminOperation.MISSION_ADD,
                AdminOperation.MISSION_LIST,
                AdminOperation.MISSION_SETNEXT,
                AdminOperation.MISSION_LIST,
                AdminOperation.MISSION_CYCLE,
                AdminOperation.SET_SETTING,
                AdminOperation.GET_GAMESETTINGS,
            ],
            [spec.operation for spec in self.session.specs],
        )
        self.assertEqual(
            "MISSION ADD DM-DUST.NPZ 0",
            self.session.specs[0].text,
        )

    def test_quiet_poll_reads_use_the_same_typed_operations(self):
        seen = []
        self.manager.set_callbacks(on_log=seen.append)

        self.manager.refresh_missions(quiet=True).result()
        self.manager.refresh_game_state(quiet=True).result()

        self.assertEqual(
            [spec.operation for spec in self.session.specs],
            [AdminOperation.MISSION_LIST, AdminOperation.GET_GAMESTATE],
        )
        self.assertEqual(
            [
                {
                    "priority": RequestPriority.BACKGROUND,
                    "coalesce_key": AdminOperation.MISSION_LIST,
                },
                {
                    "priority": RequestPriority.BACKGROUND,
                    "coalesce_key": AdminOperation.GET_GAMESTATE,
                },
            ],
            self.session.schedules,
        )
        self.assertTrue(any(line.startswith("__QUIET__") for line in seen))

    def test_stale_poll_completion_cannot_start_a_new_connection_poll_chain(self):
        timers = []

        class ControlledTimer:
            def __init__(self, _delay, callback):
                self.callback = callback
                self.started = False
                self.cancelled = False
                self.fired = False
                timers.append(self)

            def start(self):
                self.started = True

            def cancel(self):
                self.cancelled = True

            def fire(self):
                self.fired = True
                self.callback()

        sessions = []

        def factory(*args, **kwargs):
            session = FakeSession(*args, **kwargs)
            sessions.append(session)
            return session

        with patch("wolfrat.protocol.threading.Timer", ControlledTimer):
            manager = ServerManager(session_factory=factory)
            try:
                self.assertTrue(manager.connect("older.example")[0])
                manager.stop_polling()
                older_session = sessions[-1]
                stale_settings = Future()
                older_session.result_overrides[
                    AdminOperation.GET_GAMESETTINGS
                ] = [stale_settings]

                manager.start_polling(5)
                timers[-1].fire()
                self.assertFalse(stale_settings.done())

                self.assertTrue(manager.connect("newer.example")[0])
                active_before_stale_completion = [
                    timer
                    for timer in timers
                    if (
                        timer.started
                        and not timer.cancelled
                        and not timer.fired
                    )
                ]
                self.assertEqual(1, len(active_before_stale_completion))

                stale_settings.set_result(CommandResult(
                    AdminOperation.GET_GAMESETTINGS,
                    ("ServerName = Older",),
                    True,
                    older_session.snapshot.settings,
                ))

                active_after_stale_completion = [
                    timer
                    for timer in timers
                    if (
                        timer.started
                        and not timer.cancelled
                        and not timer.fired
                    )
                ]
                self.assertEqual(1, len(active_after_stale_completion))
            finally:
                manager.close()

    def test_rejected_setnext_stops_before_confirmation_and_cycle(self):
        rejection = CommandResult(
            AdminOperation.MISSION_SETNEXT,
            ("ERROR - Next Mission Not Set.",),
            False,
        )
        self.session.result_overrides[AdminOperation.MISSION_SETNEXT] = [rejection]

        result = self.manager.switch_mission(4).result()

        self.assertIs(result, rejection)
        self.assertEqual(
            [spec.operation for spec in self.session.specs],
            [AdminOperation.MISSION_SETNEXT],
        )

    def test_rejected_kill_never_swaps_player(self):
        rejection = CommandResult(
            AdminOperation.PLAYER_KILL,
            ("ERROR - Player Not Found.",),
            False,
        )
        self.session.result_overrides[AdminOperation.PLAYER_KILL] = [rejection]

        result = self.manager.swap_and_kill(12).result()

        self.assertIs(result, rejection)
        self.assertEqual(
            [spec.operation for spec in self.session.specs],
            [AdminOperation.PLAYER_KILL],
        )

    def test_rejected_swap_after_kill_is_reported(self):
        rejection = CommandResult(
            AdminOperation.PLAYER_SWAPTEAM,
            ("ERROR - Player Not Found.",),
            False,
        )
        self.session.result_overrides[AdminOperation.PLAYER_SWAPTEAM] = [rejection]

        result = self.manager.swap_and_kill(12).result()

        self.assertIs(result, rejection)
        self.assertEqual(
            [spec.operation for spec in self.session.specs],
            [
                AdminOperation.PLAYER_KILL,
                AdminOperation.PLAYER_LIST,
                AdminOperation.PLAYER_SWAPTEAM,
            ],
        )

    def test_rejected_sequence_step_stops_later_announcements(self):
        rejection = CommandResult(
            AdminOperation.CHAT_SEND,
            ("ERROR - Chat Rejected.",),
            False,
        )
        self.session.result_overrides[AdminOperation.CHAT_SEND] = [rejection]

        result = self.manager.announce("word " * 30).result()

        self.assertIs(result, rejection)
        self.assertEqual(
            [spec.operation for spec in self.session.specs],
            [AdminOperation.CHAT_SEND],
        )

    def test_successful_confirmation_preserves_the_mutation_ack(self):
        result = self.manager.kill_player(12).result()

        self.assertEqual(("OK - Player Killed.",), result.replies)
        self.assertEqual(AdminOperation.PLAYER_KILL, result.operation)
        self.assertTrue(result.verified)
        self.assertEqual(
            [spec.operation for spec in self.session.specs],
            [AdminOperation.PLAYER_KILL, AdminOperation.PLAYER_LIST],
        )

    def test_accepted_noop_readbacks_are_not_reported_as_verified(self):
        cases = (
            (
                AdminOperation.SET_SETTING,
                CommandResult(
                    AdminOperation.SET_SETTING,
                    ("OK - Setting Changed.",),
                    True,
                ),
                lambda: self.manager.set_setting("ServerName", "Changed"),
            ),
            (
                AdminOperation.PLAYER_SWAPTEAM,
                CommandResult(
                    AdminOperation.PLAYER_SWAPTEAM,
                    ("OK - Player Swapped.",),
                    True,
                ),
                lambda: self.manager.swap_player(12),
            ),
            (
                AdminOperation.MISSION_SETNEXT,
                CommandResult(
                    AdminOperation.MISSION_SETNEXT,
                    ("OK - Next Mission Set.",),
                    True,
                ),
                lambda: self.manager.set_next_mission(4),
            ),
            (
                AdminOperation.WEAPON_SET,
                CommandResult(
                    AdminOperation.WEAPON_SET,
                    ("OK - Weapon availbility changed.",),
                    True,
                ),
                lambda: self.manager.set_weapon(37, WeaponMode.NEVER),
            ),
        )

        for operation, acknowledgement, submit in cases:
            with self.subTest(operation=operation):
                self.session.result_overrides[operation] = [acknowledgement]
                result = submit().result()
                self.assertTrue(result.accepted)
                self.assertFalse(result.verified)
                self.assertIn("readback", result.verification_error)

    def test_ack_only_actions_require_their_exact_retail_reply(self):
        cycle = self.manager.cycle_mission().result()
        self.assertTrue(cycle.verified)
        self.assertEqual(
            [AdminOperation.MISSION_CYCLE],
            [spec.operation for spec in self.session.specs],
        )

        self.session.specs.clear()
        self.session.result_overrides[AdminOperation.CHAT_SEND] = [
            CommandResult(
                AdminOperation.CHAT_SEND,
                ("OK - Command executed.",),
                True,
            )
        ]
        chat = self.manager.send_chat("hello").result()
        self.assertTrue(chat.accepted)
        self.assertFalse(chat.verified)
        self.assertIn("command-specific", chat.verification_error)
        self.assertEqual(
            [AdminOperation.CHAT_SEND],
            [spec.operation for spec in self.session.specs],
        )

    def test_cancelling_workflow_observer_does_not_stop_committed_steps(self):
        first = Future()
        second = Future()
        followups = []
        output = self.manager._then(
            first,
            lambda result: followups.append(result) or second,
        )

        self.assertTrue(output.cancel())
        acknowledgement = CommandResult(
            AdminOperation.MISSION_SETNEXT,
            ("OK - Next Mission Set.",),
            True,
            verified=True,
        )
        first.set_result(acknowledgement)

        self.assertEqual([acknowledgement], followups)
        second.set_result(CommandResult(
            AdminOperation.MISSION_CYCLE,
            ("OK - Server is cycling...",),
            True,
            verified=True,
        ))
        self.assertTrue(output.cancelled())

    def test_cancelling_confirmation_observer_still_runs_readback(self):
        mutation = Future()
        confirmation = Future()
        refreshes = []
        output = self.manager._confirm_mutation(
            mutation,
            lambda: refreshes.append(True) or confirmation,
            lambda _ack, _readback: True,
        )

        self.assertTrue(output.cancel())
        mutation.set_result(CommandResult(
            AdminOperation.WEAPON_SET,
            ("OK - Weapon availbility changed.",),
            True,
        ))
        self.assertEqual([True], refreshes)
        confirmation.set_result(CommandResult(
            AdminOperation.WEAPON_LIST,
            ("3. NEVER WPN",),
            True,
            (),
            verified=True,
        ))
        self.assertTrue(output.cancelled())

    def test_confirmation_rejection_or_failure_preserves_the_mutation_ack(self):
        acknowledgement = CommandResult(
            AdminOperation.PLAYER_KILL, ("OK - Player Killed.",), True
        )
        rejected_readback = CommandResult(
            AdminOperation.PLAYER_LIST, ("ERROR - Read Failed.",), False
        )
        self.session.result_overrides[AdminOperation.PLAYER_KILL] = [acknowledgement]
        self.session.result_overrides[AdminOperation.PLAYER_LIST] = [rejected_readback]
        rejected = self.manager.kill_player(12).result()
        self.assertEqual(AdminOperation.PLAYER_KILL, rejected.operation)
        self.assertEqual(("OK - Player Killed.",), rejected.replies)
        self.assertTrue(rejected.accepted)
        self.assertFalse(rejected.verified)
        self.assertIn("ERROR - Read Failed.", rejected.verification_error)

        self.session.specs.clear()
        self.session.result_overrides[AdminOperation.PLAYER_KILL] = [acknowledgement]
        self.session.result_overrides[AdminOperation.PLAYER_LIST] = [
            RuntimeError("readback failed")
        ]
        failed = self.manager.kill_player(12).result()
        self.assertEqual(AdminOperation.PLAYER_KILL, failed.operation)
        self.assertEqual(("OK - Player Killed.",), failed.replies)
        self.assertTrue(failed.accepted)
        self.assertFalse(failed.verified)
        self.assertIn("readback failed", failed.verification_error)

    def test_raw_secret_commands_and_setting_replies_are_redacted(self):
        logs = []
        self.manager.set_callbacks(on_log=logs.append)
        self.session.raw_overrides.append(
            RawResult(
                "SET ServerPassword hunter2",
                (
                    "ServerPassword = hunter2\n"
                    "SideAPassword = red-team\n"
                    "ServerName = Dev",
                ),
                True,
            )
        )

        result = self.manager.execute_raw(
            "SET ServerPassword hunter2"
        ).result()

        visible = result.command + "\n" + "\n".join(result.replies) + "\n".join(logs)
        self.assertNotIn("hunter2", visible)
        self.assertNotIn("red-team", visible)
        self.assertIn("<redacted>", result.command.casefold())
        self.assertIn("ServerName = Dev", result.replies[0])

    def test_typed_settings_keep_secret_values_locally_but_never_log_them(self):
        logs = []
        self.manager.set_callbacks(on_log=logs.append)
        settings = GameSettings(
            {"ServerName": "Dev", "ServerPassword": "hunter2"},
            revision=9,
        )
        result = CommandResult(
            AdminOperation.GET_GAMESETTINGS,
            ("ServerName = Dev\nServerPassword = hunter2",),
            True,
            settings,
        )
        self.session.result_overrides[AdminOperation.GET_GAMESETTINGS] = [result]

        returned = self.manager.refresh_settings().result()

        self.assertIs(returned, result)
        self.assertEqual(self.manager.game_settings["serverpassword"], "hunter2")
        self.assertNotIn("hunter2", "\n".join(logs))

    def test_team_tools_exclude_the_host_from_targets_and_statistics(self):
        self.session.snapshot = replace(
            self.session.snapshot,
            revision=8,
            players=(
                PlayerEntry(
                    0,
                    "biggy",
                    1,
                    kills=100,
                    deaths=0,
                    revision=8,
                ),
                PlayerEntry(
                    12,
                    "Alice",
                    1,
                    kills=4,
                    deaths=2,
                    revision=8,
                ),
                PlayerEntry(
                    13,
                    "Bob",
                    2,
                    kills=2,
                    deaths=3,
                    revision=8,
                ),
            ),
        )

        mix = self.manager.mix_teams()
        self.assertEqual(
            ("Teams are already balanced (within 1 player)",),
            mix.messages,
        )
        self.assertTrue(mix.completion.result().verified)
        stats = self.manager.get_team_stats()
        self.assertEqual(1, stats["team_a_count"])
        self.assertEqual(1, stats["team_b_count"])
        self.assertEqual(4, stats["team_a_score"])
        self.assertEqual(2, stats["team_b_score"])

        self.session.snapshot = replace(
            self.session.snapshot,
            players=self.session.snapshot.players[:2],
        )
        shuffle = self.manager.shuffle_teams()
        self.assertEqual(("No players to shuffle",), shuffle.messages)
        self.assertTrue(shuffle.completion.result().verified)
        self.assertEqual([], self.session.specs)

    def test_multi_player_team_workflow_holds_one_mutation_gate(self):
        players = (
            PlayerEntry(0, "biggy", 1, revision=8),
            PlayerEntry(12, "Alice", 1, deaths=0, revision=8),
            PlayerEntry(13, "Bob", 1, deaths=0, revision=8),
            PlayerEntry(14, "Carol", 1, deaths=0, revision=8),
            PlayerEntry(15, "Dave", 1, deaths=0, revision=8),
            PlayerEntry(16, "Eve", 1, deaths=0, revision=8),
            PlayerEntry(17, "Frank", 2, deaths=0, revision=8),
        )
        self.session.snapshot = replace(
            self.session.snapshot,
            revision=8,
            players=players,
        )
        first_swap = Future()
        self.session.result_overrides[
            AdminOperation.PLAYER_SWAPTEAM
        ] = [first_swap]

        with patch(
            "wolfrat.protocol.random.sample",
            lambda values, count: values[:count],
        ):
            workflow = self.manager.mix_teams()
        setting = self.manager.set_setting("ServerName", "After")
        self.session.snapshot = replace(
            self.session.snapshot,
            revision=9,
            players=tuple(
                replace(
                    player,
                    team=2 if player.server_id == 12 else player.team,
                    revision=9,
                )
                for player in players
            ),
        )
        first_swap.set_result(CommandResult(
            AdminOperation.PLAYER_SWAPTEAM,
            ("OK - Player Swapped.",),
            True,
        ))

        self.assertEqual(
            ("Swapping Alice", "Swapping Bob"), workflow.messages
        )
        self.assertTrue(workflow.completion.result().verified)
        self.assertTrue(setting.result().verified)
        self.assertEqual(
            [
                AdminOperation.PLAYER_KILL,
                AdminOperation.PLAYER_LIST,
                AdminOperation.PLAYER_SWAPTEAM,
                AdminOperation.PLAYER_LIST,
                AdminOperation.PLAYER_KILL,
                AdminOperation.PLAYER_LIST,
                AdminOperation.PLAYER_SWAPTEAM,
                AdminOperation.PLAYER_LIST,
                AdminOperation.SET_SETTING,
                AdminOperation.GET_GAMESETTINGS,
            ],
            [spec.operation for spec in self.session.specs],
        )

    def test_queued_team_plan_fails_before_swapping_if_roster_changed(self):
        players = (
            PlayerEntry(0, "biggy", 1, revision=8),
            PlayerEntry(12, "Alice", 1, revision=8),
            PlayerEntry(13, "Bob", 1, revision=8),
            PlayerEntry(14, "Carol", 1, revision=8),
            PlayerEntry(15, "Dave", 1, revision=8),
            PlayerEntry(16, "Eve", 1, revision=8),
            PlayerEntry(17, "Frank", 2, revision=8),
        )
        self.session.snapshot = replace(
            self.session.snapshot,
            revision=8,
            players=players,
        )
        earlier_ack = Future()
        self.session.result_overrides[
            AdminOperation.PLAYER_SWAPTEAM
        ] = [earlier_ack]

        earlier = self.manager.swap_player(12)
        with patch(
            "wolfrat.protocol.random.sample",
            lambda values, count: values[:count],
        ):
            planned = self.manager.mix_teams().completion
        self.session.snapshot = replace(
            self.session.snapshot,
            revision=9,
            players=tuple(
                replace(
                    player,
                    team=2 if player.server_id == 12 else player.team,
                    revision=9,
                )
                for player in players
            ),
        )
        earlier_ack.set_result(CommandResult(
            AdminOperation.PLAYER_SWAPTEAM,
            ("OK - Player Swapped.",),
            True,
        ))

        self.assertTrue(earlier.result().verified)
        with self.assertRaisesRegex(ValueError, "team plan.*stale"):
            planned.result()
        self.assertEqual(
            [
                AdminOperation.PLAYER_SWAPTEAM,
                AdminOperation.PLAYER_LIST,
            ],
            [spec.operation for spec in self.session.specs],
        )

    def test_empty_shuffle_exposes_a_verified_noop_workflow(self):
        self.session.snapshot = replace(
            self.session.snapshot,
            revision=8,
            players=(
                PlayerEntry(12, "Alice", 1, revision=8),
                PlayerEntry(13, "Bob", 2, revision=8),
            ),
        )

        workflow = self.manager.shuffle_teams()

        # A 1v1 has no other split: refuse rather than just trade sides.
        result = workflow.completion.result()
        self.assertEqual(("Not enough players to mix teams",), workflow.messages)
        self.assertTrue(result.accepted)
        self.assertTrue(result.verified)
        self.assertEqual(("Not enough players to mix teams",), result.replies)
        self.assertEqual([], self.session.specs)

    def test_shuffle_really_mixes_even_teams(self):
        from wolfrat.protocol import _shuffle_moves

        for size in (2, 3, 4, 8):
            team1 = [
                PlayerEntry(10 + i, f"A{i}", 1) for i in range(size)
            ]
            team2 = [
                PlayerEntry(50 + i, f"B{i}", 2) for i in range(size)
            ]
            before = {
                frozenset(p.server_id for p in team1),
                frozenset(p.server_id for p in team2),
            }
            for _ in range(200):
                moved = {p.server_id for p in _shuffle_moves(team1, team2)}
                self.assertTrue(moved)
                after1 = frozenset(
                    p.server_id
                    for p in team1 + team2
                    if (p.team == 1) != (p.server_id in moved)
                )
                after2 = frozenset(
                    p.server_id for p in team1 + team2
                ) - after1
                # Teams stay even and are a different split, not a
                # straight trade of sides.
                self.assertEqual(size, len(after1))
                self.assertNotIn(after1, before)
                self.assertNotIn(after2, before)
                # Never more moves than half the server.
                self.assertLessEqual(len(moved), size)


if __name__ == "__main__":
    unittest.main()
