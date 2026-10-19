import socket
import struct
import threading
import time
import unittest
from unittest.mock import patch

from wolfrat.admin_session import (
    RequestPriority,
    RetailAdminSession,
    RetailProtocolError,
    SocketTransport,
    StaleIdentityError,
)
from wolfrat.admin_commands import AdminCommands, AdminOperation


MAGIC = b"\x00\x00\x0d\x0a"


def server_frame(payload):
    return MAGIC + struct.pack("<I", 8 + len(payload)) + payload


class ScriptedTransport:
    """A byte-level retail server double, controlled entirely by the test."""

    def __init__(self, incoming):
        self._incoming = list(incoming)
        self._condition = threading.Condition()
        self.connected_to = None
        self.sent = []
        self.closed = False

    def connect(self, host, port, timeout):
        self.connected_to = (host, port, timeout)

    def sendall(self, data):
        with self._condition:
            if self.closed:
                raise ConnectionError("transport is closed")
            self.sent.append(data)
            self._condition.notify_all()

    def recv(self, size, timeout):
        deadline = time.monotonic() + timeout
        with self._condition:
            while not self._incoming and not self.closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("scripted receive timed out")
                self._condition.wait(remaining)
            if self.closed:
                return b""
            chunk = self._incoming.pop(0)
            head, tail = chunk[:size], chunk[size:]
            if tail:
                self._incoming.insert(0, tail)
            return head

    def queue(self, *chunks):
        with self._condition:
            self._incoming.extend(chunks)
            self._condition.notify_all()

    def close(self):
        with self._condition:
            self.closed = True
            self._condition.notify_all()

    def wait_for_sends(self, count, timeout=1):
        deadline = time.monotonic() + timeout
        with self._condition:
            while len(self.sent) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.fail_wait(count)
                self._condition.wait(remaining)

    def fail_wait(self, count):
        raise AssertionError(
            f"expected {count} sends within timeout, observed {len(self.sent)}"
        )


class RetailAdminSessionTests(unittest.TestCase):
    def test_socket_transport_close_interrupts_connect_in_progress(self):
        class BlockingSocket:
            def __init__(self):
                self.entered = threading.Event()
                self.released = threading.Event()
                self.closed = False

            def setsockopt(self, *_args):
                pass

            def settimeout(self, _timeout):
                pass

            def connect(self, _address):
                self.entered.set()
                self.released.wait(timeout=2)
                raise ConnectionAbortedError("socket closed")

            def close(self):
                self.closed = True
                self.released.set()

        sock = BlockingSocket()
        transport = SocketTransport()
        errors = []

        def connect():
            try:
                transport.connect("127.0.0.1", 4000, 0.1)
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=connect)
        with patch("wolfrat.admin_session.socket.socket", return_value=sock):
            worker.start()
            self.assertTrue(sock.entered.wait(timeout=1))
            try:
                transport.close()
                worker.join(timeout=0.5)

                self.assertTrue(sock.closed)
                self.assertFalse(worker.is_alive())
                self.assertIsInstance(errors[0], ConnectionAbortedError)
            finally:
                sock.close()
                worker.join(timeout=1)

    def test_close_interrupts_and_joins_authentication_in_progress(self):
        class BlockingConnectTransport:
            def __init__(self):
                self.entered = threading.Event()
                self.released = threading.Event()
                self.closed = False

            def connect(self, _host, _port, _timeout):
                self.entered.set()
                self.released.wait(timeout=2)
                raise ConnectionError("transport closed")

            def sendall(self, _data):
                raise AssertionError("authentication never reached send")

            def recv(self, _size, _timeout):
                raise AssertionError("authentication never reached receive")

            def close(self):
                self.closed = True
                self.released.set()

        transport = BlockingConnectTransport()
        session = RetailAdminSession(
            "127.0.0.1",
            username="admin",
            password="secret",
            transport_factory=lambda: transport,
            timeout=0.1,
        )
        future = session.execute_raw("GET GAMESTATE")
        self.assertTrue(transport.entered.wait(timeout=1))

        try:
            session.close()

            self.assertTrue(transport.closed)
            self.assertTrue(future.done())
            self.assertIsNotNone(session._worker)
            self.assertFalse(session._worker.is_alive())
        finally:
            transport.close()

    def test_socket_transport_uses_abortive_close_for_retail_disconnect(self):
        class RecordingSocket:
            def __init__(self):
                self.options = []
                self.shutdowns = []
                self.closed = False

            def setsockopt(self, *args):
                self.options.append(args)

            def settimeout(self, _timeout):
                pass

            def connect(self, _address):
                pass

            def shutdown(self, how):
                self.shutdowns.append(how)

            def close(self):
                self.closed = True

        sock = RecordingSocket()
        transport = SocketTransport()
        with patch("wolfrat.admin_session.socket.socket", return_value=sock):
            transport.connect("127.0.0.1", 4000, 0.25)
        transport.close()

        linger = [
            value
            for level, option, value in sock.options
            if level == socket.SOL_SOCKET and option == socket.SO_LINGER
        ]
        self.assertEqual(linger, [struct.pack("hh", 1, 0)])
        self.assertEqual(sock.shutdowns, [])
        self.assertTrue(sock.closed)

    def test_credentials_must_fit_the_retail_auth_fields(self):
        for username, password in (
            ("x" * 33, "secret"),
            ("admin", "x" * 33),
            ("admi\u00f1", "secret"),
        ):
            with self.subTest(username=username, password=password):
                with self.assertRaises(ValueError):
                    RetailAdminSession(
                        "127.0.0.1", username=username, password=password
                    )

    def test_raw_command_authenticates_and_returns_the_retail_reply(self):
        challenge = server_frame(b"A" * 32 + b"\x00")
        logged_in = server_frame(b"User logged in\x00")
        reply = server_frame(b"OK GAMESTATE\x00")
        # TCP is a byte stream: all three retail frames may arrive in one recv.
        transport = ScriptedTransport([challenge + logged_in + reply])
        session = RetailAdminSession(
            "127.0.0.1",
            4000,
            "badger",
            "aaaaaa",
            transport_factory=lambda: transport,
            timeout=0.25,
        )
        self.addCleanup(session.close)

        result = session.execute_raw("GET GAMESTATE").result(timeout=1)

        expected_auth_payload = bytes.fromhex(
            "0a8026fc02389e34faf0166cf2a88ea4ea6006dce2187e14"
            "dad0f6ad33e9cfe52b40e6bcc2f85ef4bab0d62cb2684e64"
            "aa20c69ca2d83ed49a90b67ef7af92a5ec"
        )
        self.assertEqual(("OK GAMESTATE",), result.replies)
        self.assertTrue(result.accepted)
        self.assertEqual(("127.0.0.1", 4000, 0.25), transport.connected_to)
        self.assertEqual(
            [
                MAGIC + b"\x49\x00\x00\x00" + expected_auth_payload,
                b"\x00\x00\x0d\x0a\x16\x00\x00\x00GET GAMESTATE\x00",
            ],
            transport.sent,
        )

    def test_typed_cmd_acceptance_requires_its_exact_catalog_ack(self):
        transport = ScriptedTransport(
            [
                server_frame(b"T" * 32 + b"\x00"),
                server_frame(b"User logged in\x00"),
                server_frame(b"OK\x00"),
                server_frame(b"OK - Command executed.\x00"),
            ]
        )
        session = RetailAdminSession(
            "127.0.0.1",
            username="admin",
            password="secret",
            transport_factory=lambda: transport,
            timeout=0.25,
        )
        self.addCleanup(session.close)

        wrong_ack = session.execute(
            AdminCommands.tod("1730")
        ).result(timeout=1)
        exact_ack = session.execute(
            AdminCommands.tod_rate(5)
        ).result(timeout=1)

        self.assertEqual(("OK",), wrong_ack.replies)
        self.assertFalse(wrong_ack.accepted)
        self.assertIsNone(wrong_ack.verified)
        self.assertEqual(
            ("OK - Command executed.",),
            exact_ack.replies,
        )
        self.assertTrue(exact_ack.accepted)
        self.assertIsNone(exact_ack.verified)

    def test_second_command_is_not_sent_until_the_first_reply_is_complete(self):
        transport = ScriptedTransport(
            [
                server_frame(b"B" * 32 + b"\x00"),
                server_frame(b"User logged in\x00"),
            ]
        )
        session = RetailAdminSession(
            "127.0.0.1",
            username="admin",
            password="secret",
            transport_factory=lambda: transport,
            timeout=0.5,
        )
        self.addCleanup(session.close)

        first = session.execute_raw("GET GAMESTATE")
        second = session.execute_raw("PLAYER LIST")
        transport.wait_for_sends(2)

        self.assertEqual(2, len(transport.sent), "second command bypassed first reply")
        transport.queue(server_frame(b"GAME\x00"))
        transport.wait_for_sends(3)
        transport.queue(server_frame(b"1\tHost\x00"))

        self.assertEqual(("GAME",), first.result(timeout=1).replies)
        self.assertEqual(("1\tHost",), second.result(timeout=1).replies)

    def test_raw_multireply_policy_keeps_following_command_serialized(self):
        transport = ScriptedTransport(
            [
                server_frame(b"C" * 32 + b"\x00"),
                server_frame(b"User logged in\x00"),
                server_frame(b"ERROR In Game\x00"),
            ]
        )
        session = RetailAdminSession(
            "127.0.0.1",
            username="admin",
            password="secret",
            transport_factory=lambda: transport,
            timeout=0.5,
        )
        self.addCleanup(session.close)

        goto = session.execute_raw("SET Unknown value")
        following = session.execute_raw("GET GAMESTATE")
        transport.wait_for_sends(2)
        time.sleep(0.01)

        self.assertEqual(2, len(transport.sent), "SET's second reply was ignored")
        transport.queue(server_frame(b"OK\x00"))
        transport.wait_for_sends(3)
        transport.queue(server_frame(b"GAME\x00"))

        self.assertEqual(("ERROR In Game", "OK"), goto.result(timeout=1).replies)
        self.assertFalse(goto.result().accepted)
        self.assertEqual(("GAME",), following.result(timeout=1).replies)

    def test_fragmented_frames_deliver_a_valid_empty_reply_to_subscribers(self):
        challenge = server_frame(b"D" * 32 + b"\x00")
        login = server_frame(b"User logged in\x00")
        empty_reply = server_frame(b"\x00")
        transport = ScriptedTransport(
            [
                challenge[:3],
                challenge[3:9],
                challenge[9:],
                login[:1],
                login[1:],
                empty_reply[:7],
                empty_reply[7:8],
                empty_reply[8:],
            ]
        )
        session = RetailAdminSession(
            "127.0.0.1",
            username="admin",
            password="secret",
            transport_factory=lambda: transport,
            timeout=0.25,
        )
        self.addCleanup(session.close)
        observed = []
        subscription = session.subscribe(observed.append)
        self.addCleanup(subscription.unsubscribe)

        result = session.execute_raw("CHAT GET").result(timeout=1)

        self.assertEqual(("",), result.replies)
        self.assertEqual([result], observed)
        self.assertTrue(session.connected)

    def test_reply_timeout_closes_generation_and_fails_queued_work(self):
        transport = ScriptedTransport(
            [
                server_frame(b"E" * 32 + b"\x00"),
                server_frame(b"User logged in\x00"),
            ]
        )
        session = RetailAdminSession(
            "127.0.0.1",
            username="admin",
            password="secret",
            transport_factory=lambda: transport,
            timeout=0.03,
        )
        self.addCleanup(session.close)

        first = session.execute_raw("GET GAMESTATE")
        queued = session.execute_raw("PLAYER LIST")

        with self.assertRaises(TimeoutError):
            first.result(timeout=1)
        with self.assertRaises(TimeoutError):
            queued.result(timeout=1)
        self.assertTrue(transport.closed)
        self.assertFalse(session.connected)
        self.assertEqual(2, len(transport.sent))

    def test_new_work_uses_a_new_generation_after_timeout(self):
        timed_out = ScriptedTransport(
            [
                server_frame(b"F" * 32 + b"\x00"),
                server_frame(b"User logged in\x00"),
            ]
        )
        replacement = ScriptedTransport(
            [
                server_frame(b"G" * 32 + b"\x00"),
                server_frame(b"User logged in\x00"),
                server_frame(b"GAME\x00"),
            ]
        )
        transports = iter((timed_out, replacement))
        session = RetailAdminSession(
            "127.0.0.1",
            username="admin",
            password="secret",
            transport_factory=lambda: next(transports),
            timeout=0.03,
        )
        self.addCleanup(session.close)

        with self.assertRaises(TimeoutError):
            session.execute_raw("GET GAMESTATE").result(timeout=1)
        result = session.execute_raw("GET GAMESTATE").result(timeout=1)

        self.assertEqual(("GAME",), result.replies)
        self.assertTrue(timed_out.closed)
        self.assertFalse(replacement.closed)
        self.assertTrue(session.connected)

    def test_typed_query_parses_result_and_updates_owned_snapshot(self):
        transport = ScriptedTransport(
            [
                server_frame(b"H" * 32 + b"\x00"),
                server_frame(b"User logged in\x00"),
                server_frame(b"Current State = GAME\x00"),
            ]
        )
        session = RetailAdminSession(
            "127.0.0.1",
            username="admin",
            password="secret",
            transport_factory=lambda: transport,
            timeout=0.25,
        )
        self.addCleanup(session.close)

        result = session.execute(AdminCommands.game_state()).result(timeout=1)

        self.assertEqual(AdminOperation.GET_GAMESTATE, result.operation)
        self.assertEqual("GAME", result.value)
        self.assertTrue(result.accepted)
        self.assertEqual("GAME", session.snapshot.game_state)
        self.assertEqual(1, session.snapshot.revision)

    def test_retail_error_is_a_result_and_does_not_poison_the_session(self):
        transport = ScriptedTransport(
            [
                server_frame(b"I" * 32 + b"\x00"),
                server_frame(b"User logged in\x00"),
                server_frame(b"ERROR unavailable\x00"),
                server_frame(b"Current State = GAME\x00"),
            ]
        )
        session = RetailAdminSession(
            "127.0.0.1",
            username="admin",
            password="secret",
            transport_factory=lambda: transport,
            timeout=0.25,
        )
        self.addCleanup(session.close)

        rejected = session.execute(AdminCommands.game_settings())
        following = session.execute(AdminCommands.game_state())

        rejected_result = rejected.result(timeout=1)
        self.assertFalse(rejected_result.accepted)
        self.assertIsNone(rejected_result.value)
        self.assertEqual("GAME", following.result(timeout=1).value)
        self.assertTrue(session.connected)

    def test_parse_error_fails_only_that_request_and_keeps_the_connection(self):
        # 2.10.1: before this, a reply the parser could not read closed the
        # transport and failed every queued poll, so one odd map title made
        # WolfRAT reconnect every 3 s (TK - Hilltop 2024, 2026-10-19).
        transport = ScriptedTransport(
            [
                server_frame(b"J" * 32 + b"\x00"),
                server_frame(b"User logged in\x00"),
                server_frame(b"Current State = GAME\x00"),
                server_frame(b"Current State = MENUS\x00"),
            ]
        )
        session = RetailAdminSession(
            "127.0.0.1",
            username="admin",
            password="secret",
            transport_factory=lambda: transport,
            timeout=0.25,
        )
        self.addCleanup(session.close)

        with patch.object(
            type(AdminCommands.game_state()),
            "parse",
            side_effect=[ValueError("odd row"), "MENUS"],
        ):
            unreadable = session.execute(AdminCommands.game_state())
            following = session.execute(AdminCommands.game_state())
            with self.assertRaises(ValueError) as caught:
                unreadable.result(timeout=1)
            self.assertIn("connection kept", str(caught.exception))
            self.assertEqual("MENUS", following.result(timeout=1).value)
        self.assertTrue(session.connected)
        self.assertFalse(transport.closed)

    def test_stale_identity_is_rejected_before_any_bytes_are_sent(self):
        transport = ScriptedTransport(
            [
                server_frame(b"J" * 32 + b"\x00"),
                server_frame(b"User logged in\x00"),
                server_frame(b"Alice\t7\t1\x00"),
                server_frame(b"Alice\t7\t2\x00"),
                server_frame(b"OK\x00"),
            ]
        )
        session = RetailAdminSession(
            "127.0.0.1",
            username="admin",
            password="secret",
            transport_factory=lambda: transport,
            timeout=0.25,
        )
        self.addCleanup(session.close)

        session.execute(AdminCommands.players()).result(timeout=1)
        stale_alice = session.snapshot.players[0]
        session.execute(AdminCommands.players()).result(timeout=1)
        current_alice = session.snapshot.players[0]

        with self.assertRaises(StaleIdentityError):
            session.execute(AdminCommands.kill(stale_alice))
        self.assertEqual(3, len(transport.sent))
        accepted = session.execute(AdminCommands.kill(current_alice)).result(
            timeout=1
        )
        self.assertTrue(accepted.accepted)
        self.assertEqual(4, len(transport.sent))

    def test_queued_identity_is_rechecked_after_an_earlier_refresh(self):
        transport = ScriptedTransport(
            [
                server_frame(b"K" * 32 + b"\x00"),
                server_frame(b"User logged in\x00"),
                server_frame(b"Alice\t7\t1\x00"),
            ]
        )
        session = RetailAdminSession(
            "127.0.0.1",
            username="admin",
            password="secret",
            transport_factory=lambda: transport,
            timeout=0.5,
        )
        self.addCleanup(session.close)

        session.execute(AdminCommands.players()).result(timeout=1)
        alice = session.snapshot.players[0]
        refresh = session.execute(AdminCommands.players())
        transport.wait_for_sends(3)
        queued_kill = session.execute(AdminCommands.kill(alice))
        transport.queue(server_frame(b"Alice\t7\t2\x00"))

        refresh.result(timeout=1)
        with self.assertRaises(StaleIdentityError):
            queued_kill.result(timeout=1)
        self.assertEqual(3, len(transport.sent))
        self.assertTrue(session.connected)

    def test_accepted_raw_mutation_invalidates_authoritative_identities(self):
        mission_row = (
            b"0: CP08.BMS - () () () <CURRENT MISSION> <>\x00"
        )
        transport = ScriptedTransport(
            [
                server_frame(b"R" * 32 + b"\x00"),
                server_frame(b"User logged in\x00"),
                server_frame(mission_row),
                server_frame(b"OK - Mission Removed.\x00"),
            ]
        )
        session = RetailAdminSession(
            "127.0.0.1",
            username="admin",
            password="secret",
            transport_factory=lambda: transport,
            timeout=0.25,
        )
        self.addCleanup(session.close)

        session.execute(AdminCommands.missions()).result(timeout=1)
        mission = session.snapshot.missions[0]
        raw = session.execute_raw("MISSION REMOVE 0")
        stale_typed = session.execute(AdminCommands.remove_mission(mission))

        self.assertTrue(raw.result(timeout=1).accepted)
        with self.assertRaises(StaleIdentityError):
            stale_typed.result(timeout=1)
        self.assertEqual(3, len(transport.sent))
        self.assertEqual(session.snapshot.missions, ())

    def test_raw_read_does_not_invalidate_authoritative_identities(self):
        transport = ScriptedTransport(
            [
                server_frame(b"S" * 32 + b"\x00"),
                server_frame(b"User logged in\x00"),
                server_frame(b"Alice\t7\t1\x00"),
                server_frame(b"Current State = GAME\x00"),
                server_frame(b"OK - Player Killed.\x00"),
            ]
        )
        session = RetailAdminSession(
            "127.0.0.1",
            username="admin",
            password="secret",
            transport_factory=lambda: transport,
            timeout=0.25,
        )
        self.addCleanup(session.close)

        session.execute(AdminCommands.players()).result(timeout=1)
        alice = session.snapshot.players[0]
        session.execute_raw("GET GAMESTATE").result(timeout=1)
        result = session.execute(AdminCommands.kill(alice)).result(timeout=1)

        self.assertTrue(result.accepted)
        self.assertEqual(4, len(transport.sent))

    def test_non_nul_retail_reply_fails_closed(self):
        transport = ScriptedTransport(
            [
                server_frame(b"L" * 32 + b"\x00"),
                server_frame(b"User logged in\x00"),
                server_frame(b"not terminated"),
            ]
        )
        session = RetailAdminSession(
            "127.0.0.1",
            username="admin",
            password="secret",
            transport_factory=lambda: transport,
            timeout=0.25,
        )
        self.addCleanup(session.close)

        with self.assertRaises(RetailProtocolError):
            session.execute_raw("GET GAMESTATE").result(timeout=1)
        self.assertTrue(transport.closed)
        self.assertFalse(session.connected)

    def test_cancelling_a_submitted_future_does_not_poison_the_session(self):
        transport = ScriptedTransport(
            [
                server_frame(b"M" * 32 + b"\x00"),
                server_frame(b"User logged in\x00"),
            ]
        )
        session = RetailAdminSession(
            "127.0.0.1",
            username="admin",
            password="secret",
            transport_factory=lambda: transport,
            timeout=0.5,
        )
        self.addCleanup(session.close)

        cancelled = session.execute_raw("GET GAMESTATE")
        following = session.execute_raw("PLAYER LIST")
        transport.wait_for_sends(2)
        self.assertTrue(cancelled.cancel())

        transport.queue(server_frame(b"GAME\x00"))
        transport.wait_for_sends(3)
        transport.queue(server_frame(b"Alice\t7\t1\x00"))

        self.assertTrue(cancelled.cancelled())
        self.assertEqual(("Alice\t7\t1",), following.result(timeout=1).replies)
        self.assertTrue(session.connected)

    def test_cancelling_queued_work_prevents_its_command_from_being_sent(self):
        transport = ScriptedTransport(
            [
                server_frame(b"N" * 32 + b"\x00"),
                server_frame(b"User logged in\x00"),
            ]
        )
        session = RetailAdminSession(
            "127.0.0.1",
            username="admin",
            password="secret",
            transport_factory=lambda: transport,
            timeout=0.5,
        )
        self.addCleanup(session.close)

        blocker = session.execute_raw("GET GAMESTATE")
        transport.wait_for_sends(2)
        cancelled = session.execute_raw("PLAYER KILL 12")
        self.assertTrue(cancelled.cancel())
        following = session.execute_raw("CHAT GET")

        transport.queue(server_frame(b"GAME\x00"))
        transport.wait_for_sends(3)
        transport.queue(server_frame(b"\x00"))

        self.assertEqual(("GAME",), blocker.result(timeout=1).replies)
        self.assertTrue(cancelled.cancelled())
        self.assertEqual(("",), following.result(timeout=1).replies)
        commands = [
            packet[8:-1].decode("ascii")
            for packet in transport.sent[1:]
        ]
        self.assertEqual(["GET GAMESTATE", "CHAT GET"], commands)

    def test_interactive_work_overtakes_queued_background_reads(self):
        transport = ScriptedTransport(
            [
                server_frame(b"O" * 32 + b"\x00"),
                server_frame(b"User logged in\x00"),
            ]
        )
        session = RetailAdminSession(
            "127.0.0.1",
            username="admin",
            password="secret",
            transport_factory=lambda: transport,
            timeout=0.5,
        )
        self.addCleanup(session.close)

        blocker = session.execute_raw("GET GAMESTATE")
        transport.wait_for_sends(2)
        background = session.execute(
            AdminCommands.players(),
            priority=RequestPriority.BACKGROUND,
            coalesce_key=AdminOperation.PLAYER_LIST,
        )
        interactive = session.execute(AdminCommands.weapons())

        transport.queue(server_frame(b"GAME\x00"))
        transport.wait_for_sends(3)
        self.assertEqual(
            "WEAPON LIST",
            transport.sent[2][8:-1].decode("ascii"),
        )
        transport.queue(server_frame(b"  3.  ALWAYS\tWPN_colt45\x00"))
        transport.wait_for_sends(4)
        transport.queue(server_frame(b"NAME\t #\tTEAM\x00"))

        blocker.result(timeout=1)
        self.assertEqual(3, interactive.result(timeout=1).value[0].admdef_id)
        self.assertEqual((), background.result(timeout=1).value)

    def test_duplicate_background_reads_are_coalesced(self):
        transport = ScriptedTransport(
            [
                server_frame(b"P" * 32 + b"\x00"),
                server_frame(b"User logged in\x00"),
            ]
        )
        session = RetailAdminSession(
            "127.0.0.1",
            username="admin",
            password="secret",
            transport_factory=lambda: transport,
            timeout=0.5,
        )
        self.addCleanup(session.close)

        blocker = session.execute_raw("GET GAMESTATE")
        transport.wait_for_sends(2)
        first = session.execute(
            AdminCommands.players(),
            priority=RequestPriority.BACKGROUND,
            coalesce_key=AdminOperation.PLAYER_LIST,
        )
        duplicate = session.execute(
            AdminCommands.players(),
            priority=RequestPriority.BACKGROUND,
            coalesce_key=AdminOperation.PLAYER_LIST,
        )

        transport.queue(server_frame(b"GAME\x00"))
        transport.wait_for_sends(3)
        transport.queue(server_frame(b"NAME\t #\tTEAM\x00"))

        blocker.result(timeout=1)
        self.assertEqual((), first.result(timeout=1).value)
        self.assertEqual((), duplicate.result(timeout=1).value)
        commands = [
            packet[8:-1].decode("ascii")
            for packet in transport.sent[1:]
        ]
        self.assertEqual(["GET GAMESTATE", "PLAYER LIST"], commands)

    def test_completion_callback_can_queue_a_fresh_coalesced_read(self):
        transport = ScriptedTransport(
            [
                server_frame(b"Q" * 32 + b"\x00"),
                server_frame(b"User logged in\x00"),
                server_frame(b"NAME\t #\tTEAM\x00"),
            ]
        )
        session = RetailAdminSession(
            "127.0.0.1",
            username="admin",
            password="secret",
            transport_factory=lambda: transport,
            timeout=0.5,
        )
        self.addCleanup(session.close)
        followups = []

        first = session.execute(
            AdminCommands.players(),
            priority=RequestPriority.BACKGROUND,
            coalesce_key=AdminOperation.PLAYER_LIST,
        )
        first.add_done_callback(
            lambda _done: followups.append(
                session.execute(
                    AdminCommands.players(),
                    priority=RequestPriority.BACKGROUND,
                    coalesce_key=AdminOperation.PLAYER_LIST,
                )
            )
        )
        transport.wait_for_sends(3)
        transport.queue(server_frame(b"NAME\t #\tTEAM\x00"))

        self.assertEqual((), first.result(timeout=1).value)
        self.assertEqual((), followups[0].result(timeout=1).value)
        commands = [
            packet[8:-1].decode("ascii")
            for packet in transport.sent[1:]
        ]
        self.assertEqual(["PLAYER LIST", "PLAYER LIST"], commands)


if __name__ == "__main__":
    unittest.main()
