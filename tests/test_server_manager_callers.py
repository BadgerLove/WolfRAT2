"""Static contract between active callers and the ServerManager facade."""

import ast
import inspect
from pathlib import Path
import unittest

from wolfrat.protocol import ServerManager


ROOT = Path(__file__).resolve().parents[1]
CALLER_PATHS = (ROOT / "wolfrat" / "app.py", ROOT / "wolfrat" / "web_server.py")
MANAGER_ATTRIBUTES = {"server", "sm"}


def _manager_attribute(node):
    if not isinstance(node, ast.Attribute):
        return None
    owner = node.value
    if (
        isinstance(owner, ast.Attribute)
        and isinstance(owner.value, ast.Name)
        and owner.value.id == "self"
        and owner.attr in MANAGER_ATTRIBUTES
    ):
        return node.attr
    return None


def _caller_trees():
    for path in CALLER_PATHS:
        yield path, ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


class ServerManagerCallerContractTests(unittest.TestCase):
    def test_active_manager_calls_exist_and_accept_the_written_arguments(self):
        violations = []
        checked = 0
        for path, tree in _caller_trees():
            for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
                name = _manager_attribute(call.func)
                if name is None:
                    continue
                checked += 1
                member = getattr(ServerManager, name, None)
                location = f"{path.relative_to(ROOT)}:{call.lineno}"
                if member is None or not callable(member):
                    violations.append(f"{location}: missing callable {name}()")
                    continue
                if any(isinstance(argument, ast.Starred) for argument in call.args):
                    continue
                if any(keyword.arg is None for keyword in call.keywords):
                    continue
                signature = inspect.signature(member)
                positional = [object()] * len(call.args)
                keywords = {keyword.arg: object() for keyword in call.keywords}
                try:
                    signature.bind(object(), *positional, **keywords)
                except TypeError as error:
                    violations.append(
                        f"{location}: {name}{signature} is incompatible: {error}"
                    )
        self.assertGreater(checked, 100, "caller scan did not find the active facade usage")
        self.assertEqual([], violations, "\n".join(violations))

    def test_active_manager_state_reads_exist_on_a_fresh_facade(self):
        manager = ServerManager()
        violations = []
        checked = set()
        for path, tree in _caller_trees():
            parents = {}
            for parent in ast.walk(tree):
                for child in ast.iter_child_nodes(parent):
                    parents[child] = parent
            for attribute in (
                node for node in ast.walk(tree) if isinstance(node, ast.Attribute)
            ):
                name = _manager_attribute(attribute)
                if name is None or not isinstance(attribute.ctx, ast.Load):
                    continue
                parent = parents.get(attribute)
                if isinstance(parent, ast.Call) and parent.func is attribute:
                    continue
                checked.add(name)
                if not hasattr(manager, name):
                    violations.append(
                        f"{path.relative_to(ROOT)}:{attribute.lineno}: "
                        f"missing state/property {name}"
                    )
        self.assertIn("players", checked)
        self.assertIn("mission_entries", checked)
        self.assertIn("is_connected", checked)
        self.assertEqual([], violations, "\n".join(violations))

    def test_migration_specific_keyword_contracts_are_locked(self):
        expected_keywords = {
            "refresh_missions": {"quiet"},
            "refresh_game_state": {"quiet"},
            "send_chat": {"quiet"},
            "set_callbacks": {"on_weapons"},
            "set_next_mission": {"add_if_missing"},
            "switch_mission": {"add_if_missing"},
        }
        # refresh_missions / refresh_game_state(quiet=...) lost their only
        # caller on 2026-11-21 with the Missions tab's Auto Refresh timer (an
        # extra 15 s read on top of ServerManager's own 5 s poll); the
        # signature check below still holds them.
        caller_exercised = {
            ("set_callbacks", "on_weapons"),
            ("set_next_mission", "add_if_missing"),
            ("switch_mission", "add_if_missing"),
        }
        written_keywords = {name: set() for name in expected_keywords}
        for _path, tree in _caller_trees():
            for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
                name = _manager_attribute(call.func)
                if name in written_keywords:
                    written_keywords[name].update(
                        keyword.arg
                        for keyword in call.keywords
                        if keyword.arg is not None
                    )

        for name, keywords in expected_keywords.items():
            signature = inspect.signature(getattr(ServerManager, name))
            for keyword in keywords:
                with self.subTest(method=name, keyword=keyword):
                    if (name, keyword) in caller_exercised:
                        self.assertIn(
                            keyword,
                            written_keywords[name],
                            f"active callers no longer exercise "
                            f"{name}(..., {keyword}=...)",
                        )
                    self.assertIn(
                        keyword,
                        signature.parameters,
                        f"ServerManager.{name} dropped caller keyword {keyword}",
                    )

        self.assertTrue(callable(getattr(ServerManager, "set_raw_chat_callback", None)))
        self.assertTrue(callable(getattr(ServerManager, "execute_raw", None)))


if __name__ == "__main__":
    unittest.main()
