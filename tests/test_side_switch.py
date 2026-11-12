"""x1/x2 in place: MISSION ADD <file> <0|1> (append) then REMOVE the copy.

The fake models retail's rules (OpenNova novaworld-net-re.md 5.70.1/5.70.2):
the (2x) flag lives on the catalog row, so every queue entry of that map
shows it; an Append may regrow and drop a pending SETNEXT.
"""

import unittest
from dataclasses import replace

from wolfrat.admin_commands import (
    AdminOperation,
    AdminSnapshot,
    AvailableMission,
    GameSettings,
    MissionEntry,
)
from wolfrat.protocol import ServerManager

from tests.test_server_manager import FakeSession


class FlagSession(FakeSession):
    drop_next_on_append = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.flags = {"A.BMS": False, "B.BMS": True, "C.BMS": True}
        missions = (
            MissionEntry(0, "A.BMS", is_current=True, revision=4),
            MissionEntry(1, "B.BMS", revision=4),
            MissionEntry(2, "C.BMS", is_next=True, revision=4),
            MissionEntry(3, "B.BMS", revision=4),
        )
        self.snapshot = replace(
            self.snapshot,
            missions=missions,
            available_missions=(
                AvailableMission(0, "A.BMS", "A", revision=5),
                AvailableMission(1, "B.BMS", "B", revision=5),
                AvailableMission(2, "C.BMS", "C", revision=5),
            ),
        )
        self._restamp()

    def _restamp(self):
        self.snapshot = replace(
            self.snapshot,
            missions=tuple(
                replace(m, double_time=self.flags[m.filename])
                for m in self.snapshot.missions
            ),
        )

    def execute(self, spec, **schedule):
        if spec.operation is AdminOperation.MISSION_ADD:
            parts = spec.text.split()
            if len(parts) >= 4:
                self.flags[parts[2]] = parts[3] == "1"
        result = super().execute(spec, **schedule)
        if spec.operation is AdminOperation.MISSION_ADD:
            if self.drop_next_on_append:
                self.snapshot = replace(
                    self.snapshot,
                    missions=tuple(
                        replace(m, is_next=False)
                        for m in self.snapshot.missions
                    ),
                )
        if spec.operation in {
            AdminOperation.MISSION_ADD,
            AdminOperation.MISSION_REMOVE,
            AdminOperation.MISSION_SETNEXT,
        }:
            self._restamp()
        if spec.operation is AdminOperation.MISSION_LIST:
            self._restamp()
            from wolfrat.admin_session import CommandResult
            from tests.test_server_manager import completed
            value = self.snapshot.missions
            self.snapshot = self.snapshot.apply(spec.operation, value)
            return completed(
                CommandResult(spec.operation, ("OK",), True, value)
            )
        return result


class SideSwitchTests(unittest.TestCase):
    def _manager(self, drop_next=False):
        sessions = []

        def factory(*args, **kwargs):
            session = FlagSession(*args, **kwargs)
            session.drop_next_on_append = drop_next
            sessions.append(session)
            return session

        manager = ServerManager(
            session_factory=factory,
            verification_attempts=3,
            verification_delay=0,
        )
        ok, _ = manager.connect("127.0.0.1", 4000, "admin", "pw")
        self.assertTrue(ok)
        manager.refresh_missions().result(timeout=2)
        manager.refresh_available_maps().result(timeout=2)
        self.addCleanup(manager.disconnect)
        self.addCleanup(manager.stop_polling)
        session = sessions[-1]
        session.specs.clear()
        return manager, session

    def _queue(self, session):
        return [
            (m.queue_index, m.filename, m.double_time, m.is_next)
            for m in session.snapshot.missions
        ]

    def test_x2_to_x1_keeps_order_and_next(self):
        manager, session = self._manager()
        target = manager.mission_entries[1]
        manager.set_mission_side_switch(target, False).result(timeout=2)
        texts = [s.text for s in session.specs if "MISSION" in s.text
                 and s.text != "MISSION LIST"]
        self.assertEqual(texts, ["MISSION ADD B.BMS 0", "MISSION REMOVE 4"])
        self.assertEqual(
            self._queue(session),
            [
                (0, "A.BMS", False, False),
                (1, "B.BMS", False, False),
                (2, "C.BMS", True, True),
                (3, "B.BMS", False, False),
            ],
        )

    def test_x1_to_x2(self):
        manager, session = self._manager()
        manager.set_mission_side_switch(
            manager.mission_entries[2], False
        ).result(timeout=2)
        self.assertFalse(session.flags["C.BMS"])
        manager.set_mission_side_switch(
            manager.mission_entries[2], True
        ).result(timeout=2)
        self.assertTrue(session.flags["C.BMS"])
        self.assertEqual(len(session.snapshot.missions), 4)

    def test_dropped_next_is_set_again(self):
        manager, session = self._manager(drop_next=True)
        manager.set_mission_side_switch(
            manager.mission_entries[1], False
        ).result(timeout=2)
        texts = [s.text for s in session.specs if "MISSION" in s.text
                 and s.text != "MISSION LIST"]
        self.assertEqual(
            texts,
            ["MISSION ADD B.BMS 0", "MISSION REMOVE 4", "MISSION SETNEXT 2"],
        )
        self.assertEqual(
            [m.is_next for m in session.snapshot.missions],
            [False, False, True, False],
        )


if __name__ == "__main__":
    unittest.main()
