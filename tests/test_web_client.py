"""The phone page (wolfrat/web_templates): behaviour in node, plus static checks.

The behaviour test runs tests/web_client_result_test.js (a fake browser) on
app.js. The static checks hold the page to its Content-Security-Policy (no
inline scripts, no inline event handlers, nothing loaded from elsewhere) and
check the install files: manifest, icons and service worker.
"""
from pathlib import Path
import json
import re
import shutil
import struct
import subprocess
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = REPOSITORY_ROOT / "wolfrat" / "web_templates"
DARK = "#09090b"

# The fixed file names the web server serves (no directory serving).
STATIC_FILES = (
    "index.html",
    "app.js",
    "style.css",
    "manifest.webmanifest",
    "sw.js",
    "icon-192.png",
    "icon-512.png",
    "icon-maskable-512.png",
)


def png_size(path):
    data = path.read_bytes()[:24]
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise AssertionError(f"{path.name} is not a PNG")
    return struct.unpack(">II", data[16:24])


class WebClientResultTests(unittest.TestCase):
    def test_browser_renders_operation_results(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is required for the browser client behavior test")

        completed = subprocess.run(
            [
                node,
                str(REPOSITORY_ROOT / "tests" / "web_client_result_test.js"),
                str(TEMPLATES / "app.js"),
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        self.assertEqual(
            0,
            completed.returncode,
            completed.stdout + completed.stderr,
        )


class WebClientStaticTests(unittest.TestCase):
    def test_every_static_file_is_there(self):
        for name in STATIC_FILES:
            self.assertTrue((TEMPLATES / name).is_file(), name)

    def test_page_has_no_inline_script_or_handler(self):
        html = (TEMPLATES / "index.html").read_text(encoding="utf-8")
        scripts = re.findall(r"<script\b[^>]*>", html, flags=re.IGNORECASE)
        self.assertEqual(['<script src="/static/app.js">'], scripts)
        self.assertRegex(html, r"<script src=\"/static/app\.js\"></script>")
        self.assertIsNone(
            re.search(r"\son[a-z]+\s*=", html, flags=re.IGNORECASE),
            "inline event handlers are blocked by the page's CSP",
        )
        self.assertNotIn("javascript:", html.lower())

    def test_page_loads_nothing_from_elsewhere(self):
        html = (TEMPLATES / "index.html").read_text(encoding="utf-8")
        for attribute in re.findall(r"\b(?:src|href)=\"([^\"]+)\"", html):
            self.assertTrue(
                attribute.startswith("/") or attribute.startswith("#"),
                f"{attribute!r} is not a local file",
            )
        self.assertIn('<link rel="manifest" href="/manifest.webmanifest">', html)
        for name in ("app.js", "style.css", "sw.js"):
            text = (TEMPLATES / name).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"https?://", text), f"{name} names another site")
            self.assertNotIn("@import", text)
        script = (TEMPLATES / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("eval(", script)
        self.assertNotIn("new Function", script)
        self.assertNotIn("setAttribute('on", script)

    def test_manifest_and_icons(self):
        manifest = json.loads((TEMPLATES / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual("WolfRAT", manifest["name"])
        self.assertEqual("WolfRAT", manifest["short_name"])
        self.assertEqual("/", manifest["start_url"])
        self.assertEqual("standalone", manifest["display"])
        self.assertEqual(DARK, manifest["background_color"])
        self.assertEqual(DARK, manifest["theme_color"])
        icons = {icon["src"]: icon for icon in manifest["icons"]}
        expected = {
            "/static/icon-192.png": ((192, 192), "any"),
            "/static/icon-512.png": ((512, 512), "any"),
            "/static/icon-maskable-512.png": ((512, 512), "maskable"),
        }
        self.assertEqual(set(expected), set(icons))
        for src, (size, purpose) in expected.items():
            self.assertEqual(f"{size[0]}x{size[1]}", icons[src]["sizes"])
            self.assertEqual("image/png", icons[src]["type"])
            self.assertEqual(purpose, icons[src]["purpose"])
            self.assertEqual(size, png_size(TEMPLATES / src.rsplit("/", 1)[1]))

    def test_service_worker_keeps_the_app_shell_only(self):
        worker = (TEMPLATES / "sw.js").read_text(encoding="utf-8")
        shell = re.search(r"const SHELL = \[(.*?)\];", worker, flags=re.DOTALL)
        self.assertIsNotNone(shell)
        entries = re.findall(r"'([^']+)'", shell.group(1))
        self.assertIn("/", entries)
        self.assertIn("/static/app.js", entries)
        for entry in entries:
            self.assertFalse(entry.startswith("/api"), entry)
            self.assertNotEqual("/ws", entry)
        self.assertIn("url.pathname.startsWith('/api/') || url.pathname === '/ws'", worker)
        self.assertIn("request.method !== 'GET'", worker)


if __name__ == "__main__":
    unittest.main()
