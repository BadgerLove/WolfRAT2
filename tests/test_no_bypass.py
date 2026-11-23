"""Architecture guardrails for the mandatory retail-admin seam."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

# Keep the enforcement surface explicit.  These are application entry points
# and adapters, not the private transport or command catalog.
RUNTIME_CALLERS = (
    REPOSITORY_ROOT / "app.py",
    REPOSITORY_ROOT / "protocol.py",
    REPOSITORY_ROOT / "wolfrat" / "app.py",
    REPOSITORY_ROOT / "wolfrat" / "protocol.py",
    REPOSITORY_ROOT / "wolfrat" / "web_server.py",
)
WEB_RUNTIME_CALLERS = (
    REPOSITORY_ROOT / "wolfrat" / "web_templates" / "app.js",
    REPOSITORY_ROOT / "wolfrat" / "web_templates" / "index.html",
)
# Calls named send that are not retail admin traffic: web_push.send delivers a
# phone notification to the phone's push service over https.
NOT_ADMIN_SENDS = frozenset({("web_push", "send")})

COMMAND_LITERAL = re.compile(
    r"^\s*(?:"
    r"GET\s+(?:GAMESTATE|GAMESETTINGS|SETTINGS)(?:\s|$)|"
    r"SET\s+(?:[A-Za-z][A-Za-z0-9_]*)(?:\s|$)|"
    r"PLAYER\s+(?:LIST|PUNT|BAN|KILL|SWAPTEAM|ZEROSCORE)(?:\s|$)|"
    r"MISSION\s+(?:LIST|AVAILABLE|ADD|REMOVE|CLEAR|CYCLE|SETNEXT)(?:\s|$)|"
    r"WEAPON\s+(?:LIST|SET)(?:\s|$)|"
    r"CHAT\s+(?:GET|SEND)(?:\s|$)|"
    r"CMD\s+(?:TOD|TODRATE|WARN)(?:\s|$)|"
    r"QUERY(?:\s|$)|GOTO(?:\s|$)|QUIT(?:\s|$)|"
    r"PETERRABBIT(?:\s|$)|EVENTLOG(?:\s|$)"
    r")",
    re.IGNORECASE,
)


def _existing_runtime_callers() -> tuple[Path, ...]:
    return tuple(path for path in RUNTIME_CALLERS if path.is_file())


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _location(path: Path, node: ast.AST) -> str:
    return f"{path.relative_to(REPOSITORY_ROOT)}:{getattr(node, 'lineno', '?')}"


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return tuple(reversed(parts))


def _literal_text(expression: ast.AST) -> str | None:
    """Return the static prefix of a string expression, if it has one."""

    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return expression.value
    if isinstance(expression, ast.JoinedStr):
        chunks: list[str] = []
        for value in expression.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                chunks.append(value.value)
            else:
                break
        return "".join(chunks)
    if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Add):
        left = _literal_text(expression.left)
        if left is not None:
            return left
    return None


class MandatoryAdminSessionTests(unittest.TestCase):
    maxDiff = None

    def test_runtime_callers_do_not_own_network_transports(self) -> None:
        violations: list[str] = []
        for path in _existing_runtime_callers():
            for node in ast.walk(_tree(path)):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "socket" or alias.name.startswith("socket."):
                            violations.append(
                                f"{_location(path, node)} imports {alias.name!r}"
                            )
                elif isinstance(node, ast.ImportFrom):
                    if node.module == "socket" or (
                        node.module and node.module.startswith("socket.")
                    ):
                        violations.append(
                            f"{_location(path, node)} imports from {node.module!r}"
                        )
                elif isinstance(node, ast.Call):
                    chain = _attribute_chain(node.func)
                    if chain[-2:] in (
                        ("socket", "socket"),
                        ("socket", "create_connection"),
                    ):
                        violations.append(
                            f"{_location(path, node)} constructs a network transport"
                        )

        self.assertEqual(
            [],
            violations,
            "Runtime callers must use RetailAdminSession; transport ownership is "
            "private to admin_session.py:\n" + "\n".join(violations),
        )

    def test_runtime_callers_do_not_call_legacy_send_apis(self) -> None:
        violations: list[str] = []
        for path in _existing_runtime_callers():
            for node in ast.walk(_tree(path)):
                if not isinstance(node, ast.Call):
                    continue
                chain = _attribute_chain(node.func)
                if (chain and chain[-1] in {"send", "send_command"}
                        and chain not in NOT_ADMIN_SENDS):
                    violations.append(
                        f"{_location(path, node)} calls {chain[-1]!r}"
                    )

        self.assertEqual(
            [],
            violations,
            "All runtime admin traffic must cross RetailAdminSession:\n"
            + "\n".join(violations),
        )

    def test_runtime_does_not_embed_retail_commands(self) -> None:
        violations: list[str] = []
        for path in _existing_runtime_callers():
            tree = _tree(path)
            for node in ast.walk(tree):
                # A formatted command is unambiguously serialization rather
                # than a UI label, wherever it appears.
                if isinstance(node, (ast.JoinedStr, ast.BinOp)):
                    text = _literal_text(node)
                    if text is not None and COMMAND_LITERAL.match(text):
                        violations.append(
                            f"{_location(path, node)} formats retail command {text!r}"
                        )
                if not isinstance(node, ast.Call):
                    continue
                chain = _attribute_chain(node.func)
                if not chain or chain[-1] not in {
                    "send",
                    "send_command",
                    "execute_raw",
                }:
                    continue
                for argument in node.args:
                    text = _literal_text(argument)
                    if text is not None and COMMAND_LITERAL.match(text):
                        violations.append(
                            f"{_location(path, argument)} embeds retail command "
                            f"{text!r}"
                        )

        self.assertEqual(
            [],
            violations,
            "Command serialization belongs in wolfrat/admin_commands.py. The raw "
            "console may hand user input to execute_raw, but runtime callers must "
            "not embed command text:\n" + "\n".join(violations),
        )

    def test_web_runtime_does_not_embed_retail_commands(self) -> None:
        violations: list[str] = []
        for path in WEB_RUNTIME_CALLERS:
            text = path.read_text(encoding="utf-8-sig")
            for line_number, line in enumerate(text.splitlines(), start=1):
                # Capture every quoted prefix. This also sees single-quoted
                # values nested inside HTML's double-quoted event attributes.
                for match in re.finditer(r"""['"`]\s*([^'"`\r\n]+)""", line):
                    value = match.group(1)
                    if COMMAND_LITERAL.match(value):
                        violations.append(
                            f"{path.relative_to(REPOSITORY_ROOT)}:{line_number} "
                            f"embeds retail command {value!r}"
                        )

        self.assertEqual(
            [],
            violations,
            "Web controls must send named intents to typed ServerManager "
            "operations. Only explicit user-entered console text may use the "
            "raw endpoint:\n" + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
