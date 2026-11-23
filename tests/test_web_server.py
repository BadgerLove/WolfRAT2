import asyncio
from concurrent.futures import Future
import json
import unittest
from unittest.mock import patch

from wolfrat.admin_commands import MissionEntry, PlayerEntry
from wolfrat.admin_session import CommandResult, RawResult
import wolfrat.web_server as web_server_module
from wolfrat.web_server import WolfWebServer


class FakeServerManager:
    is_connected = True
    players = [{"id": 7, "name": "Alice"}]
    chat_messages = ["hello"]
    game_state = {"mode": "AAS"}
    missions = []
    available_maps_data = ""
    game_settings = {
        "ServerName": "Test Server",
        "ServerPassword": "server-secret",
        "SideAPassword": "alpha-secret",
        "SideBPassword": "bravo-secret",
    }
    mission_entries = (
        MissionEntry(queue_index=4, filename="CP08.BMS", revision=1),
    )
    player_entries = (
        PlayerEntry(server_id=7, name="Alice", team=1, revision=6),
    )

    def __init__(self):
        self.raw_commands = []
        self.semantic_actions = []

    def execute_raw(self, command):
        self.raw_commands.append(command)
        result = Future()
        result.set_result(RawResult(command, ("ERROR denied",), False))
        return result

    def _accept(self, action):
        self.semantic_actions.append(action)
        result = Future()
        result.set_result(CommandResult(
            operation=action,
            replies=("OK",),
            accepted=True,
            verified=True,
        ))
        return result

    def cycle_mission(self):
        return self._accept("next_map")

    def refresh_players(self):
        return self._accept("refresh_players")

    def refresh_chat(self):
        return self._accept("refresh_chat")

    def switch_mission(self, mission):
        return self._accept(("switch_mission", mission))

    def punt_player(self, player):
        return self._accept(("kick", player))

    def ban_player(self, player):
        return self._accept(("ban", player))

    def kill_player(self, player):
        return self._accept(("kill", player))

    def swap_player(self, player):
        return self._accept(("swap", player))

    def swap_and_kill(self, player, name=""):
        return self._accept(("swap_and_kill", player))

    def zero_player(self, player):
        return self._accept(("zero", player))


class FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


class FakeWeb:
    @staticmethod
    def json_response(payload, status=200):
        return {"payload": payload, "status": status}


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send_str(self, message):
        self.messages.append(json.loads(message))


class WebStateTests(unittest.TestCase):
    def test_state_and_settings_never_expose_retail_passwords(self):
        server = WolfWebServer(FakeServerManager(), host="127.0.0.1", port=0)

        state = server._build_state()

        self.assertEqual({"ServerName": "Test Server"}, state["settings"])
        self.assertNotIn("secret", json.dumps(state).casefold())
        self.assertTrue(state["connected"])
        self.assertEqual(
            {
                "id": 7,
                "name": "Alice",
                "team": "1",
                "team_name": "Joint Ops",
                "class": "",
                "kills": "0",
                "score": "0",
                "deaths": "-",
                "ping": "-",
                "revision": 6,
            },
            state["players"][0],
        )

    def test_map_resolution_uses_authoritative_mission_records(self):
        server = WolfWebServer(FakeServerManager(), host="127.0.0.1", port=0)

        mission = server._find_mission_entry(
            "cp08.bms", queue_index=4, revision=1
        )

        self.assertIs(FakeServerManager.mission_entries[0], mission)

    def test_map_resolution_rejects_an_ambiguous_filename(self):
        manager = FakeServerManager()
        first = MissionEntry(
            queue_index=4, filename="CP08.BMS", revision=2
        )
        second = MissionEntry(
            queue_index=9, filename="CP08.BMS", revision=2
        )
        manager.mission_entries = (first, second)
        server = WolfWebServer(manager, host="127.0.0.1", port=0)

        with self.assertRaisesRegex(ValueError, "ambiguous"):
            server._find_mission_entry("cp08.bms")

        self.assertIs(
            second,
            server._find_mission_entry(
                "cp08.bms", queue_index=9, revision=2
            ),
        )

    def test_map_switch_targets_the_selected_duplicate_queue_entry(self):
        manager = FakeServerManager()
        first = MissionEntry(
            queue_index=4, filename="CP08.BMS", revision=3
        )
        second = MissionEntry(
            queue_index=9, filename="CP08.BMS", revision=3
        )
        manager.mission_entries = (first, second)
        server = WolfWebServer(manager, host="127.0.0.1", port=0)
        server._check_auth = lambda _request: True

        with patch.object(web_server_module, "web", FakeWeb, create=True):
            response = asyncio.run(server._handle_map_switch(
                FakeRequest({
                    "map": "CP08.BMS",
                    "index": 9,
                    "revision": 3,
                })
            ))

        self.assertEqual(
            [("switch_mission", second)],
            manager.semantic_actions,
        )
        self.assertEqual(9, response["payload"]["index"])
        self.assertTrue(response["payload"]["verified"])

    def test_map_switch_rejects_ambiguous_filename_only_request(self):
        manager = FakeServerManager()
        manager.mission_entries = (
            MissionEntry(queue_index=4, filename="CP08.BMS", revision=3),
            MissionEntry(queue_index=9, filename="CP08.BMS", revision=3),
        )
        server = WolfWebServer(manager, host="127.0.0.1", port=0)
        server._check_auth = lambda _request: True

        with patch.object(web_server_module, "web", FakeWeb, create=True):
            response = asyncio.run(server._handle_map_switch(
                FakeRequest({"map": "CP08.BMS"})
            ))

        self.assertEqual(409, response["status"])
        self.assertIn("ambiguous", response["payload"]["error"])
        self.assertEqual([], manager.semantic_actions)

    def test_map_switch_ignores_the_poll_revision(self):
        # The session bumps the revision on every 5 s poll; a tap made against
        # the previous poll's list must still switch the map.
        manager = FakeServerManager()
        mission = MissionEntry(queue_index=9, filename="CP08.BMS", revision=4)
        manager.mission_entries = (mission,)
        server = WolfWebServer(manager, host="127.0.0.1", port=0)
        server._check_auth = lambda _request: True

        for body in (
            {"map": "CP08.BMS", "index": 9, "revision": 3},
            {"map": "CP08.BMS", "index": 9},
        ):
            with patch.object(web_server_module, "web", FakeWeb, create=True):
                response = asyncio.run(
                    server._handle_map_switch(FakeRequest(body))
                )
            self.assertEqual(200, response["status"], response["payload"])

        self.assertEqual(
            [("switch_mission", mission), ("switch_mission", mission)],
            manager.semantic_actions,
        )

    def test_map_switch_follows_a_unique_file_when_the_queue_shifted(self):
        manager = FakeServerManager()
        mission = MissionEntry(queue_index=5, filename="CP08.BMS", revision=4)
        manager.mission_entries = (
            MissionEntry(queue_index=4, filename="CP01.BMS", revision=4),
            mission,
        )
        server = WolfWebServer(manager, host="127.0.0.1", port=0)

        self.assertIs(
            mission, server._find_mission_entry("cp08.bms", queue_index=4)
        )
        self.assertIs(mission, server._find_mission_entry("cp08.bms"))

    def test_map_switch_rejects_a_file_that_left_the_queue(self):
        manager = FakeServerManager()
        manager.mission_entries = (
            MissionEntry(queue_index=4, filename="CP01.BMS", revision=4),
        )
        server = WolfWebServer(manager, host="127.0.0.1", port=0)
        server._check_auth = lambda _request: True

        with patch.object(web_server_module, "web", FakeWeb, create=True):
            response = asyncio.run(server._handle_map_switch(
                FakeRequest({"map": "CP08.BMS", "index": 4})
            ))

        self.assertEqual(409, response["status"])
        self.assertIn("stale", response["payload"]["error"])
        self.assertEqual([], manager.semantic_actions)

    def test_result_payload_reports_the_retail_outcome(self):
        accepted = RawResult("ignored", ("OK",), True)
        rejected = RawResult("ignored", ("ERROR denied",), False)

        self.assertEqual(
            {
                "ok": True,
                "accepted": True,
                "replies": ["OK"],
                "command": "user input",
            },
            WolfWebServer._result_payload(accepted, command="user input"),
        )
        self.assertEqual(
            "ERROR denied",
            WolfWebServer._result_payload(rejected)["error"],
        )

    def test_result_payload_preserves_typed_verification(self):
        verified = CommandResult(
            operation="PLAYER KILL",
            replies=("OK",),
            accepted=True,
            verified=True,
        )
        not_verified = CommandResult(
            operation="PLAYER KILL",
            replies=("OK",),
            accepted=True,
            verified=False,
            verification_error="player readback did not confirm the mutation",
        )
        unverified = CommandResult(
            operation="CMD TOD",
            replies=("OK",),
            accepted=True,
        )

        self.assertEqual(
            {
                "ok": True,
                "accepted": True,
                "replies": ["OK"],
                "verified": True,
                "verification_error": None,
            },
            WolfWebServer._result_payload(verified),
        )
        self.assertEqual(
            False,
            WolfWebServer._result_payload(not_verified)["verified"],
        )
        self.assertEqual(
            False,
            WolfWebServer._result_payload(not_verified)["ok"],
        )
        self.assertEqual(
            "player readback did not confirm the mutation",
            WolfWebServer._result_payload(not_verified)["verification_error"],
        )
        self.assertEqual(
            False,
            WolfWebServer._result_payload(unverified)["ok"],
        )

    def test_raw_password_setting_is_redacted_from_web_results_and_logs(self):
        self.assertEqual(
            "SET ServerPassword <redacted>",
            WolfWebServer._redact_command("SET ServerPassword server-secret"),
        )
        self.assertEqual(
            "GET GAMESTATE",
            WolfWebServer._redact_command("GET GAMESTATE"),
        )

    def test_there_is_no_raw_command_route(self):
        server = WolfWebServer(FakeServerManager(), host="127.0.0.1", port=0)
        self.assertFalse(hasattr(server, "_handle_command"))
        paths = {
            route.resource.canonical
            for route in server.application.router.routes()
        }
        self.assertIn("/api/chat/send", paths)
        self.assertNotIn("/api/command", paths)

    def test_quick_actions_call_typed_manager_operations(self):
        manager = FakeServerManager()
        server = WolfWebServer(manager, host="127.0.0.1", port=0)
        server._check_auth = lambda _request: True

        with patch.object(web_server_module, "web", FakeWeb, create=True):
            responses = [
                asyncio.run(server._handle_quick_action(
                    FakeRequest({"action": action})
                ))
                for action in ("next_map", "refresh_players", "refresh_chat")
            ]

        self.assertEqual(
            ["next_map", "refresh_players", "refresh_chat"],
            manager.semantic_actions,
        )
        self.assertTrue(all(response["payload"]["accepted"] for response in responses))
        self.assertTrue(all(response["payload"]["verified"] for response in responses))
        self.assertEqual([], manager.raw_commands)

    def test_player_action_rejects_ids_above_the_retail_roster_range(self):
        manager = FakeServerManager()
        server = WolfWebServer(manager, host="127.0.0.1", port=0)
        server._check_auth = lambda _request: True

        with patch.object(web_server_module, "web", FakeWeb, create=True):
            response = asyncio.run(server._handle_player_action(
                FakeRequest({"pid": 251, "action": "kill"})
            ))

        self.assertEqual(400, response["status"])
        self.assertEqual(
            "Player id 251 out of range (1-250)",
            response["payload"]["error"],
        )
        self.assertEqual([], manager.semantic_actions)

    def test_player_action_passes_the_exact_authoritative_entry(self):
        manager = FakeServerManager()
        player = PlayerEntry(
            server_id=7, name="Alice", team=1, revision=6
        )
        manager.player_entries = (player,)
        server = WolfWebServer(manager, host="127.0.0.1", port=0)
        server._check_auth = lambda _request: True

        with patch.object(web_server_module, "web", FakeWeb, create=True):
            response = asyncio.run(server._handle_player_action(
                FakeRequest({
                    "pid": 7,
                    "name": "Alice",
                    "revision": 6,
                    "action": "kill",
                })
            ))

        self.assertEqual([("kill", player)], manager.semantic_actions)
        self.assertEqual(7, response["payload"]["pid"])
        self.assertEqual(6, response["payload"]["revision"])

    def test_player_action_rejects_a_reused_player_slot(self):
        manager = FakeServerManager()
        manager.player_entries = (
            PlayerEntry(
                server_id=7, name="Replacement", team=2, revision=6
            ),
        )
        server = WolfWebServer(manager, host="127.0.0.1", port=0)
        server._check_auth = lambda _request: True

        with patch.object(web_server_module, "web", FakeWeb, create=True):
            response = asyncio.run(server._handle_player_action(
                FakeRequest({
                    "pid": 7,
                    "name": "Alice",
                    "revision": 6,
                    "action": "ban",
                })
            ))

        self.assertEqual(409, response["status"])
        self.assertIn("stale", response["payload"]["error"])
        self.assertEqual([], manager.semantic_actions)

    def test_player_action_ignores_the_poll_revision(self):
        # 2026-09-20: "revision changed from 35385 to 35386" rejected a team
        # swap from the phone. Same id, same name = same player.
        manager = FakeServerManager()
        player = PlayerEntry(server_id=7, name="Alice", team=1, revision=8)
        manager.player_entries = (player,)
        server = WolfWebServer(manager, host="127.0.0.1", port=0)
        server._check_auth = lambda _request: True

        for body in (
            {"pid": 7, "name": "Alice", "revision": 6, "action": "swap"},
            {"pid": 7, "name": "Alice", "action": "swap"},
        ):
            with patch.object(web_server_module, "web", FakeWeb, create=True):
                response = asyncio.run(
                    server._handle_player_action(FakeRequest(body))
                )
            self.assertEqual(200, response["status"], response["payload"])

        self.assertEqual(
            # The web swap must kill too: a bare SWAPTEAM leaves the player
            # alive in the middle of the team they just left.
            [("swap_and_kill", player), ("swap_and_kill", player)],
            manager.semantic_actions,
        )

    def test_player_action_still_rejects_the_host_before_identity_lookup(self):
        manager = FakeServerManager()
        manager.player_entries = (
            PlayerEntry(server_id=0, name="Host", team=0, revision=9),
        )
        server = WolfWebServer(manager, host="127.0.0.1", port=0)
        server._check_auth = lambda _request: True

        with patch.object(web_server_module, "web", FakeWeb, create=True):
            response = asyncio.run(server._handle_player_action(
                FakeRequest({
                    "pid": 0,
                    "name": "Host",
                    "revision": 9,
                    "action": "kill",
                })
            ))

        self.assertEqual(400, response["status"])
        self.assertIn("out of range", response["payload"]["error"])
        self.assertEqual([], manager.semantic_actions)

    def test_phone_chat_result_includes_retail_rejection(self):
        manager = FakeServerManager()
        rejected = Future()
        rejected.set_result(RawResult("CHAT SEND [ADMIN] hi", ("ERROR denied",), False))
        manager.send_chat = lambda line: rejected
        server = WolfWebServer(manager, host="127.0.0.1", port=0)

        payload = asyncio.run(server._phone_message("hi"))

        self.assertEqual(
            {
                "ok": False,
                "accepted": False,
                "replies": ["ERROR denied"],
                "error": "ERROR denied",
            },
            payload,
        )


if __name__ == "__main__":
    unittest.main()
