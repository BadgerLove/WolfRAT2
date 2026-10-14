"""Sky colours: the script block, the sender, the keeper and the Sky page,
against a fake server that runs our server.wac block the way the game does."""

import struct

import pytest

from wolfrat import sky
from wolfrat.sky_page import SkyPage


class FakeSkyServer:
    """Sparse memory + what our server.wac block does once a second."""

    def __init__(self, capable=True, script=True, lightning=(85, 85, 90)):
        self.cells = {}
        self.script = script            # is our block compiled into the running map?
        self.players = 1                # the script only runs with someone on
        self.applied = []               # (code, {action: (r, g, b)}, fade)
        self.held = {}                  # action -> colour sent (None = map's own)
        for _, index in sky.SKY_ACTIONS:
            self.cells[sky.ACTION_TABLE + sky.ACTION_SIZE * index] = bytes(
                [sky.BROADCAST_KIND if capable else 0x04])
        r, g, b = lightning
        self.poke(sky.LIGHTNING_COLOUR, (r << 16) | (g << 8) | b)
        for n in range(200, 256):
            self.poke(sky.g_address(n), 0)

    def poke(self, address, value):
        self.cells[address] = struct.pack("<i", value)

    def peek(self, address):
        return struct.unpack("<i", self.cells[address][:4])[0]

    def g(self, n):
        return self.peek(sky.g_address(n))

    def read(self, address, size):
        data = self.cells.get(address)
        if data is None or len(data) < size:
            raise OSError("unmapped")
        return data[:size]

    def write(self, address, data):
        self.cells[address] = bytes(data)

    def run_script(self):
        if not self.script or not self.players:
            return
        code = self.g(sky.COMMAND_SLOT)
        if code == sky.CMD_LOOK:
            colours = {}
            for i, element in enumerate(sky.ELEMENTS):
                rgb = tuple(self.g(sky.LOOK_FIRST_SLOT + 3 * i + k) for k in range(3))
                colours[element.action] = rgb
                if element.key == "lightning":
                    r, g, b = rgb
                    self.poke(sky.LIGHTNING_COLOUR, (r << 16) | (g << 8) | b)
                else:
                    self.held[element.action] = None if rgb[0] > 255 else rgb
            self.applied.append((code, colours, self.g(sky.FADE_SLOT)))
            self.poke(sky.g_address(sky.COMMAND_SLOT), 0)
        elif code == sky.CMD_LIGHTNING:              # lightning(G240, G241, G242)
            r, g, b = (self.g(n) for n in sky.SINGLE_SLOTS)
            self.poke(sky.LIGHTNING_COLOUR, (r << 16) | (g << 8) | b)
            self.poke(sky.g_address(sky.COMMAND_SLOT), 0)
        elif code == sky.CMD_PING:
            self.poke(sky.g_address(sky.COMMAND_SLOT), sky.CMD_PONG)

    def lightning(self):
        return sky.unpack_rgb(self.peek(sky.LIGHTNING_COLOUR))


# ---- pure parts ---------------------------------------------------------

def test_fog_and_horizon_go_out_at_half_scale_and_off_is_999():
    assert sky.sent_value("fog", (255, 60, 0)) == (127, 30, 0)
    assert sky.sent_value("horizon", (1, 1, 1)) == (1, 1, 1)
    assert sky.sent_value("sun", (255, 60, 0)) == (255, 60, 0)
    assert sky.sent_value("sky", None) == (999, 999, 999)


def test_every_builtin_scene_is_sane():
    keys = [s.key for s in sky.BUILTIN_SCENES]
    assert len(keys) == len(set(keys)) >= 20
    for scene in sky.BUILTIN_SCENES:
        assert scene.key == sky.scene_key(scene.label) or scene.key in ("golden", "storm", "winter",
                                                                           "jungle", "mars")
        assert scene.key not in sky.RESERVED_WORDS
        assert not scene.look.is_empty()
        for rgb in scene.look.colours.values():
            assert all(0 <= c <= 255 for c in rgb)
    for name in ("zombie", "bloodmoon", "toxic", "sandstorm"):     # Dale asked for these by name
        assert name in keys


def test_the_script_block_is_crlf_and_small():
    text = sky.block_text()
    raw = text.encode("ascii")
    assert raw.count(b"\n") == raw.count(b"\r\n")
    code = [l for l in text.split("\r\n") if l.strip() and not l.startswith("//")]
    assert len(code) < 80                          # script space is shared with the map
    assert "lightning(G235, G236, G237)" in text
    assert f"if eq(G248, {sky.CMD_PING}) then" in text


@pytest.mark.parametrize("args, kind, key, minutes", [
    (["toxic"], "scene", "toxic", None),
    (["toxic", "10"], "scene", "toxic", 10),
    (["Blood", "Moon", "5"], "scene", "bloodmoon", 5),
    (["blood"], "scene", "bloodmoon", None),           # unique start of a name
    (["off"], "off", None, None),
    (["demo"], "demo", None, None),
    (["list"], "list", None, None),
    ([], "usage", None, None),
    (["lava"], "usage", None, None),
    (["toxic", "999"], "usage", None, None),
])
def test_chat(args, kind, key, minutes):
    request = sky.parse_chat(args, sky.BUILTIN_SCENES)
    assert request.kind == kind
    if key:
        assert request.scene.key == key and request.minutes == minutes
    if kind == "usage":
        assert len(request.message) <= 59


def test_chat_list_lines_fit_the_chat_limit():
    lines = sky.chat_lines("Skies:", [s.key for s in sky.BUILTIN_SCENES])
    assert len(lines) > 1 and all(len(line) <= 59 for line in lines)
    assert "zombie" in lines[0]


# ---- server.wac -------------------------------------------------------------

def test_install_keeps_the_rest_and_replaces_the_old_test_block(tmp_path):
    wac = tmp_path / "server.wac"
    wac.write_bytes(b"// lightning add-on\r\nif eq(G250, 1) then\r\nflash\r\nendif\r\n\r\n"
                    b"// >>> Sky colour TEST (Wendy 2026-10-05) - delete\r\nif eq(G248, 151) then\r\n"
                    b"endif\r\n// <<< Sky colour TEST\r\n")
    assert sky.old_test_block(tmp_path) and not sky.block_installed(tmp_path)
    assert sky.install_block(tmp_path) == "updated"
    data = wac.read_bytes()
    assert data.startswith(b"// lightning add-on\r\nif eq(G250, 1) then\r\nflash\r\nendif\r\n")
    assert b"Sky colour TEST" not in data and b"151" not in data
    assert data.count(b"\n") == data.count(b"\r\n")
    assert sky.block_installed(tmp_path)
    assert sky.install_block(tmp_path) == "already"
    assert (tmp_path / "server.wac.bak-wolfrat-sky").exists()
    assert sky.install_block(tmp_path, remove=True) == "removed"
    assert wac.read_bytes() == b"// lightning add-on\r\nif eq(G250, 1) then\r\nflash\r\nendif\r\n"


def test_install_into_an_empty_folder(tmp_path):
    assert sky.install_block(tmp_path) == "installed"
    assert sky.block_installed(tmp_path)


# ---- sender + keeper on the fake server ---------------------------------------

def run(server, sender, keeper, seconds=3):
    for _ in range(seconds):
        sender.pump(server, lambda: keeper.home_lightning(server))
        server.run_script()


def test_one_command_goes_out_whole_and_lightning_goes_home_after():
    server, sender = FakeSkyServer(), sky.Sender()
    keeper = sky.SkyKeeper(clock=lambda: 0.0)
    keeper.tick(map_name="M1", players=1, settled=True, map_scene=None)
    sender.push(keeper.show(sky.BUILTIN_SCENES[1], "page").command)      # Blood Moon
    run(server, sender, keeper)
    code, colours, fade = server.applied[-1]
    assert code == sky.CMD_LOOK and fade == sky.DEFAULT_FADE
    assert colours["fogcolor"] == (90, 10, 10)                          # half of (180, 20, 20)
    assert colours["sun"] == (100, 30, 30)
    assert server.lightning() == (255, 0, 0)
    assert keeper.lightning_home == (85, 85, 90)                       # read BEFORE it changed

    sender.push(keeper.off().command)
    run(server, sender, keeper)
    assert all(v is None for v in server.held.values())                 # all back to the map
    assert server.lightning() == (85, 85, 90)                          # no "off" for lightning


def test_a_scene_without_lightning_leaves_the_maps_lightning_alone():
    server, sender = FakeSkyServer(lightning=(10, 20, 30)), sky.Sender()
    keeper = sky.SkyKeeper(clock=lambda: 0.0)
    keeper.tick(map_name="M1", players=1, settled=True, map_scene=None)
    arctic = next(s for s in sky.BUILTIN_SCENES if s.key == "arctic")
    sender.push(keeper.show(arctic, "page").command)
    run(server, sender, keeper)
    assert server.lightning() == (10, 20, 30)


def test_never_writes_over_a_command_the_script_has_not_read():
    server, sender = FakeSkyServer(), sky.Sender()
    server.players = 0                                    # the script sits still
    keeper = sky.SkyKeeper(clock=lambda: 0.0)
    sender.push(sky.look_command(sky.BUILTIN_SCENES[0].look))
    sender.push(sky.Command(sky.CMD_PING), replace_looks=False)
    run(server, sender, keeper)
    assert server.g(sky.COMMAND_SLOT) == sky.CMD_LOOK and sender.queued()
    server.players = 1
    run(server, sender, keeper)
    assert server.g(sky.COMMAND_SLOT) == sky.CMD_PONG and not sender.queued()


def test_timed_scene_ends_and_a_map_change_ends_any_scene():
    now = [0.0]
    keeper = sky.SkyKeeper(clock=lambda: now[0])
    keeper.tick(map_name="M1", players=1, settled=True, map_scene=None)
    keeper.show(sky.BUILTIN_SCENES[2], "mod", minutes=10)
    now[0] = 601
    steps = keeper.tick(map_name="M1", players=1, settled=True, map_scene=None)
    assert keeper.active is None and steps[0].announce == "Sky: back to normal."

    keeper.show(sky.BUILTIN_SCENES[2], "mod")                      # until the map changes
    steps = keeper.tick(map_name="M2", players=1, settled=False, map_scene=None)
    assert keeper.active is None and steps[0].command is None      # the load reset it already
    assert keeper.lightning_home is None                           # read again on the new map


def test_map_rule_goes_on_after_the_load_and_a_mod_beats_it():
    now = [0.0]
    zombie = sky.BUILTIN_SCENES[0]
    keeper = sky.SkyKeeper(clock=lambda: now[0])
    assert keeper.tick(map_name="Zed", players=1, settled=False, map_scene=zombie) == []
    steps = keeper.tick(map_name="Zed", players=1, settled=True, map_scene=zombie)
    assert keeper.active.source == "map" and steps[0].command is not None
    keeper.show(sky.BUILTIN_SCENES[2], "mod", minutes=5)
    now[0] = 400
    keeper.tick(map_name="Zed", players=1, settled=True, map_scene=zombie)
    assert keeper.active is None                  # the mod's time ran out: no rule till next map
    keeper.tick(map_name="Next", players=1, settled=True, map_scene=None)
    keeper.tick(map_name="Zed", players=1, settled=True, map_scene=zombie)
    assert keeper.active.scene is zombie


def test_off_keeps_the_map_rule_off_for_that_map():
    zombie = sky.BUILTIN_SCENES[0]
    keeper = sky.SkyKeeper(clock=lambda: 0.0)
    keeper.tick(map_name="Zed", players=1, settled=True, map_scene=zombie)
    keeper.off("Mod")
    keeper.tick(map_name="Zed", players=1, settled=True, map_scene=zombie)
    assert keeper.active is None


def test_late_joiners_get_the_sky_again_without_a_fade():
    now = [0.0]
    keeper = sky.SkyKeeper(clock=lambda: now[0])
    keeper.tick(map_name="M", players=1, settled=True, map_scene=None)
    keeper.show(sky.BUILTIN_SCENES[0], "page")
    now[0] = 5
    assert keeper.tick(map_name="M", players=1, settled=True, map_scene=None) == []
    steps = keeper.tick(map_name="M", players=2, settled=True, map_scene=None)
    assert len(steps) == 1 and dict(steps[0].command.writes)[sky.FADE_SLOT] == 0
    now[0] = 5 + sky.RESEND_SECONDS
    assert len(keeper.tick(map_name="M", players=2, settled=True, map_scene=None)) == 1


def test_last_minute_option():
    keeper = sky.SkyKeeper(clock=lambda: 0.0)
    keeper.tick(map_name="M", players=1, settled=True, map_scene=None)
    keeper.show(sky.BUILTIN_SCENES[0], "page")
    assert not [s for s in keeper.tick(map_name="M", players=1, settled=True, map_scene=None,
                                       minutes_left=1, end_last_minute=False) if s.announce]
    steps = keeper.tick(map_name="M", players=1, settled=True, map_scene=None,
                        minutes_left=1, end_last_minute=True)
    assert keeper.active is None and "last minute" in steps[0].announce


def test_demo_walks_every_scene_then_puts_things_back():
    now = [0.0]
    scenes = list(sky.BUILTIN_SCENES[:3])
    keeper = sky.SkyKeeper(clock=lambda: now[0])
    keeper.tick(map_name="M", players=1, settled=True, map_scene=None)
    keeper.start_demo(scenes)
    seen, flashes = [], 0
    for _ in range(60):
        for step in keeper.tick(map_name="M", players=1, settled=True, map_scene=None):
            if step.announce.startswith("Sky demo "):
                seen.append(step.announce)
            flashes += step.flash
        now[0] += 1
    assert seen[:3] == ["Sky demo 1/3: Zombie", "Sky demo 2/3: Blood Moon", "Sky demo 3/3: Toxic"]
    assert seen[-1] == "Sky demo over: back to normal."
    assert flashes == 3                       # all three have a lightning colour
    assert not keeper.demo_running() and keeper.active is None


# ---- the page ------------------------------------------------------------------

def make_page(qtbot, tmp_path, server=None, installed=True):
    now = [0.0]
    said, logged = [], []
    folder = tmp_path / "server"
    folder.mkdir()
    if installed:
        sky.install_block(folder)
    page = SkyPage(tmp_path / "wolfrat_sky.json", log=logged.append, send_chat=said.append,
                   flash=lambda: True, server_dir=lambda: str(folder), clock=lambda: now[0])
    qtbot.addWidget(page)
    page._timer.stop()
    server = server or FakeSkyServer()
    page.server_tick(server, True, "Map One", 1, True)
    return page, server, said, logged, now


def settle(page, server, now, seconds=4):
    for _ in range(seconds * 2):
        page._pump()
        server.run_script()
        now[0] += 0.5


def test_page_pings_then_a_scene_reaches_the_server(qtbot, tmp_path):
    page, server, said, _, now = make_page(qtbot, tmp_path)
    settle(page, server, now)
    assert page._script == "yes" and "ready" in page.server_lbl.text()
    page.minutes_spin.setValue(0)
    page._scene_clicked("toxic")
    settle(page, server, now)
    assert server.applied and server.held["fogcolor"] == (30, 90, 20)
    assert said[-1] == "Sky: Toxic until the map changes."
    assert "Toxic" in page.now_lbl.text()


def test_page_waits_for_the_install_and_says_so(qtbot, tmp_path):
    page, server, _, _, now = make_page(qtbot, tmp_path, installed=False)
    assert page.install_btn.isHidden() is False
    page._scene_clicked("toxic")
    assert "Install" in page.now_lbl.text()
    page._install()
    assert sky.block_installed(tmp_path / "server")


def test_page_without_a_sky_exe_says_so(qtbot, tmp_path):
    page, _, _, _, _ = make_page(qtbot, tmp_path, server=FakeSkyServer(capable=False))
    assert "cannot send sky colours" in page.server_lbl.text()
    assert page.install_btn.isHidden()


def test_old_script_never_answers_our_ping(qtbot, tmp_path):
    server = FakeSkyServer(script=False)              # lines on disk, map not reloaded yet
    page, server, _, _, now = make_page(qtbot, tmp_path, server=server)
    settle(page, server, now, seconds=8)
    assert page._script == "no" and "next map load" in page.server_lbl.text()
    assert server.g(sky.COMMAND_SLOT) == 0             # our unread ping was taken back
    page._scene_clicked("toxic")
    settle(page, server, now)
    assert not server.applied                          # nothing goes out to a deaf script


def test_mod_chat(qtbot, tmp_path):
    page, server, said, _, now = make_page(qtbot, tmp_path)
    assert page.on_chat("Mod", ["toxic"]) == "Sky commands are switched off in WolfRAT."
    page.mods_cb.setChecked(True)
    settle(page, server, now)
    assert page.on_chat("Mod", ["lava"]).startswith("No sky called")
    assert page.on_chat("Mod", ["bloodmoon", "10"]) is None
    assert said[-1] == "Sky: Blood Moon for 10 min."
    settle(page, server, now)
    assert server.lightning() == (255, 0, 0)
    page.on_chat("Mod", ["list"])
    assert said[-1].startswith("Skies:") or "," in said[-1]
    page.on_chat("Mod", ["off"])
    settle(page, server, now)
    assert server.lightning() == (85, 85, 90) and server.held["sun"] is None


def test_make_your_own_save_and_use_it_from_chat(qtbot, tmp_path):
    page, server, _, _, now = make_page(qtbot, tmp_path)
    page.mods_cb.setChecked(True)
    for key in sky.ELEMENT_KEYS:
        page._use[key].setChecked(False)
    page.set_colour("fog", (0, 255, 0))
    page.set_colour("lightning", (255, 0, 255))
    page.name_edit.setText("Swamp Gas")
    page._save_scene()
    assert "swampgas" in page.scene_buttons
    settle(page, server, now)
    assert page.on_chat("Mod", ["swamp", "gas"]) is None
    settle(page, server, now)
    assert server.held["fogcolor"] == (0, 127, 0) and server.held["sun"] is None
    assert server.lightning() == (255, 0, 255)

    # it survives a restart of WolfRAT
    again = SkyPage(tmp_path / "wolfrat_sky.json")
    qtbot.addWidget(again)
    again._timer.stop()
    assert again.scene("swampgas").look.colours["fog"] == (0, 255, 0)


def test_a_builtin_name_cannot_be_taken(qtbot, tmp_path):
    page, _, _, _, _ = make_page(qtbot, tmp_path)
    page.name_edit.setText("Toxic")
    page._save_scene()
    assert "taken" in page.editor_lbl.text()


def test_map_rule_from_the_page(qtbot, tmp_path):
    page, server, said, _, now = make_page(qtbot, tmp_path)
    page.map_search.setText("Zed Valley")
    page.rule_combo.setCurrentIndex(page.rule_combo.findData("zombie"))
    page._set_rule()
    assert page.map_scene("Zed Valley").key == "zombie"
    page.server_tick(server, True, "Zed Valley", 1, True)
    settle(page, server, now)
    assert said[-1] == "Sky: Zombie."
    assert server.held["sky"] == (40, 220, 60)


def test_demo_button(qtbot, tmp_path):
    page, server, said, _, now = make_page(qtbot, tmp_path)
    settle(page, server, now)
    page._demo_clicked()
    assert page.demo_btn.text().startswith("■")
    settle(page, server, now, seconds=sky.DEMO_STEP_SECONDS * 2 + 2)
    demo = [line for line in said if line.startswith("Sky demo ")]
    assert demo[:2] == [f"Sky demo 1/{len(page.scenes())}: Zombie",
                        f"Sky demo 2/{len(page.scenes())}: Blood Moon"]
    page._demo_clicked()
    settle(page, server, now)
    assert not page.keeper.demo_running() and server.held["fogcolor"] is None


# ---- coloured lightning: "!lightning red" ---------------------------------------

@pytest.mark.parametrize("args, colour, far, bad", [
    (["red"], (255, 0, 0), False, False),
    (["yellow", "far"], (255, 230, 0), True, False),
    (["#ff00ff"], (255, 0, 255), False, False),
    (["far"], None, True, False),
    ([], None, False, False),
    (["lava"], None, False, True),
])
def test_lightning_words(args, colour, far, bad):
    request = sky.parse_lightning(args)
    assert bool(request.message) == bad
    if not bad:
        assert (request.colour, request.far) == (colour, far)
    assert len(sky.LIGHTNING_USAGE) <= 59


def test_coloured_strike_colours_flashes_and_puts_the_colour_back(qtbot, tmp_path):
    flashes = []
    page, server, _, logged, now = make_page(qtbot, tmp_path)
    page._flash = lambda far=False: flashes.append(far) or True
    settle(page, server, now)
    assert page.coloured_strike("Mod", (255, 0, 0), far=True) is None
    assert page.coloured_strike("Mod", (0, 0, 255)).startswith("Lightning: wait")
    settle(page, server, now, seconds=1)
    assert server.lightning() == (255, 0, 0) and flashes == [True]
    settle(page, server, now, seconds=4)
    assert server.lightning() == (85, 85, 90)               # the map's own, as found
    assert page._strike is None
    assert any("Mod called down #ff0000 lightning (distant)" in line for line in logged)


def test_coloured_strike_during_a_scene_goes_back_to_the_scenes_lightning(qtbot, tmp_path):
    page, server, _, _, now = make_page(qtbot, tmp_path)
    page._flash = lambda far=False: True
    settle(page, server, now)
    page._scene_clicked("bloodmoon")                          # red lightning
    settle(page, server, now)
    page.coloured_strike("Mod", (0, 255, 40))
    settle(page, server, now, seconds=1)
    assert server.lightning() == (0, 255, 40)
    settle(page, server, now, seconds=4)
    assert server.lightning() == (255, 0, 0)


def test_chat_says_exactly_what_is_missing(qtbot, tmp_path):
    page, server, _, _, now = make_page(qtbot, tmp_path, installed=False)
    page.mods_cb.setChecked(True)
    assert page.on_chat("Mod", ["demo"]) == "Sky: press Install on WolfRAT's Sky page first."
    assert page.coloured_strike("Mod", (255, 0, 0)) == "Sky: press Install on WolfRAT's Sky page first."
    (tmp_path / "b").mkdir()
    page2, _, _, _, _ = make_page(qtbot, tmp_path / "b", server=FakeSkyServer(capable=False))
    page2.mods_cb.setChecked(True)
    assert page2.on_chat("Mod", ["demo"]) == "Sky: this server exe has no sky-colour patch."
    for text in ("Sky: press Install on WolfRAT's Sky page first.",
                 "Sky: this server exe has no sky-colour patch.",
                 "Sky: WolfRAT is not running on the server PC."):
        assert len(text) <= 59
