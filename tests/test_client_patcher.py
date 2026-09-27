"""The Client patcher (2026-09-27) against a fake stock exe carrying the bytes
the jo-server-patches client scripts assert.  On the FMJ box the engine was
checked byte for byte against the public scripts (vehicle, grass, spectator,
all three together) and a grass v9 client + spectator mode rebuilt Dale's
daily driver exactly (e9a77dec)."""

import json
import os
import struct

import pytest

from tests.test_server_patcher import STOCK, everything, fake_exe
from wolfrat import client_patcher as C
from wolfrat import server_patcher as P

CLIENT = dict(STOCK)
CLIENT.update({
    0x435640: "3bc2740c",                                            # 02 vehicle weapon
    0x42E410: "6a03c705bc204d0203000000",                            # 13 A
    0x5CAB95: "e806f9e7ff", 0x5A426D: "83c41885ed",                  # 13 B, C
    0x49A6CA: "740d833d28194c02", 0x49A91A: "740d833d28194c02",      # 13 D
    0x49B991: "740d833d28194c02",
    0x7DEA3C: struct.pack("<f", 42.0).hex(),                         # 10 grass
    0x7C3DD0: struct.pack("<f", 64.0).hex(),
    0x60A45D: "d905608e7d00",
    0x7DF1BC: struct.pack("<f", 1 / 22).hex(),
    0x5FF963: "81f9ffff0000", 0x5FF973: "81f9ffff0000",
    0x5FF983: "81f9ffff0000", 0x5FF993: "81f9ffff0000",
    0x601BC5: "3bc57507897cac2083c501",
})


def game(folder, name="jointops.exe", data=None):
    exe = folder / name
    exe.write_bytes(data or fake_exe(CLIENT, laa=False))
    return str(exe)


def all_on(**kw):
    choice = C.Choice(laa=True, vehicle=True, spectator=True, grass=True)
    for key, value in kw.items():
        setattr(choice, key, value)
    return choice


def test_stock_state(tmp_path):
    state = C.read_state(game(tmp_path))
    assert state.known and not state.laa
    assert (state.vehicle, state.spectator, state.grass) == (False, False, False)
    assert state.spectator_room and state.grass_room
    choice = C.default_choice(state)
    assert choice.laa and choice.vehicle and choice.spectator and not choice.grass   # grass is opt-in


def test_everything_on_any_exe_name(tmp_path):
    path = game(tmp_path, "My JO Game.exe")
    before = open(path, "rb").read()
    result = C.build(path, all_on())
    assert open(path, "rb").read() == before                        # never touched
    assert result.exe_path == str(tmp_path / "My JO Game_wolfrat.exe")
    assert result.changed == ["4 GB fix (Large Address Aware)", "Vehicle weapon fix", "Spectator mode",
                              "Longer grass (128 m)"]
    assert result.steps == ["Close the game.",
                            "Rename My JO Game.exe to My JO Game_before_wolfrat.exe (your backup).",
                            "Rename My JO Game_wolfrat.exe to My JO Game.exe.",
                            "Start the game."]
    after = C.read_state(result.exe_path)
    assert after.laa and after.vehicle and after.spectator and after.grass


def test_building_again_changes_nothing(tmp_path):
    first = C.build(game(tmp_path), all_on())
    folder = tmp_path / "again"
    folder.mkdir()
    path = game(folder, data=open(first.exe_path, "rb").read())
    with pytest.raises(P.PatchError, match="nothing to build"):
        C.build(path, all_on())
    assert not (folder / "jointops_wolfrat.exe").exists()


def test_grass_can_be_taken_out_again(tmp_path):
    """Dale 2026-09-27: players must be able to untick grass.  On the FMJ box
    grass v9 with grass unticked gave back the plain LAA exe byte for byte."""
    stock = fake_exe(CLIENT, laa=True)
    with_grass = C.build(game(tmp_path, data=stock), all_on(vehicle=False, spectator=False))
    assert with_grass.changed == ["Longer grass (128 m)"]
    folder = tmp_path / "grass"
    folder.mkdir()
    path = game(folder, data=open(with_grass.exe_path, "rb").read())
    result = C.build(path, all_on(vehicle=False, spectator=False, grass=False))
    assert result.changed == ["Longer grass removed"]
    assert open(result.exe_path, "rb").read() == stock
    assert C.default_choice(C.read_state(path)).grass                # stays ticked unless unticked


def test_grass_removal_refuses_bytes_it_did_not_write(tmp_path):
    with_grass = C.build(game(tmp_path, data=fake_exe(CLIENT, laa=True)), all_on(vehicle=False, spectator=False))
    data = bytearray(open(with_grass.exe_path, "rb").read())
    img = P.Image(bytes(data))
    data[img.offset(0x5FF965)] = 0x07                                # someone else's budget
    folder = tmp_path / "odd"
    folder.mkdir()
    with pytest.raises(P.PatchError, match="Removing longer grass"):
        C.build(game(folder, data=bytes(data)), all_on(vehicle=False, spectator=False, grass=False))
    assert not (folder / "jointops_wolfrat.exe").exists()


def test_a_server_exe_takes_the_client_patches_that_fit(tmp_path):
    """Dale: an admin may patch the server exe and then add client patches.
    Spectator mode and grass share space with server spectator chat and server
    spawn protection, so those two are explained, the rest still build."""
    server = P.build(game(tmp_path, data=fake_exe(CLIENT)), everything(spectator=True, spawn=True))
    folder = tmp_path / "srv"
    folder.mkdir()
    path = game(folder, data=open(server.exe_path, "rb").read())
    state = C.read_state(path)
    assert state.server_spectator_chat and state.server_spawn_config
    assert "server's spectator chat patch" in C.why_not(state, "spectator")
    assert "server's spawn protection patch" in C.why_not(state, "grass")
    choice = C.default_choice(state)
    assert not choice.spectator and not choice.grass
    result = C.build(path, choice)
    assert result.changed == ["Vehicle weapon fix"]
    with pytest.raises(P.PatchError, match="Spectator mode: This exe has the server's spectator chat"):
        C.build(path, all_on(grass=False))


def test_something_else_in_the_space_is_named_generally(tmp_path):
    data = bytearray(fake_exe(CLIENT))
    img = P.Image(bytes(data))
    data[img.offset(0x7BFB00)] = 0x12
    state = C.state_of("x", bytes(data))
    assert C.why_not(state, "spectator").startswith("Something else in this exe")


def test_an_exe_it_does_not_know_is_refused(tmp_path):
    path = game(tmp_path, data=fake_exe({}, laa=False))
    assert not C.read_state(path).known
    with pytest.raises(P.PatchError, match="does not recognise"):
        C.build(path, all_on())
    odd = {**CLIENT, 0x5FF973: "81f9fffe0000"}                       # one grass site differs
    folder = tmp_path / "odd"
    folder.mkdir()
    with pytest.raises(P.PatchError, match="Longer grass"):
        C.build(game(folder, data=fake_exe(odd, laa=False)), all_on())
    assert not (folder / "jointops_wolfrat.exe").exists()


def test_out_names():
    assert C.out_names(r"C:\Games\JO\jointops.exe") == ("jointops_wolfrat.exe", "jointops_before_wolfrat.exe")
    assert C.out_names("/x/JO Client.EXE") == ("JO Client_wolfrat.EXE", "JO Client_before_wolfrat.EXE")


# ---- the Client patcher page ------------------------------------------------------

pytest.importorskip("PyQt6")


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_page_starts_empty_and_remembers_the_exe(qapp, tmp_path):
    from wolfrat.client_patcher_panel import NO_EXE, ClientPatcherPanel
    settings = tmp_path / "wolfrat_client_patcher.json"
    panel = ClientPatcherPanel(settings)
    assert panel.status_lbl.text() == NO_EXE and not panel.build_btn.isEnabled()
    path = game(tmp_path, "Any Name.exe")
    panel.choose(path)
    assert json.loads(settings.read_text())["exe"] == path
    again = ClientPatcherPanel(settings)
    assert again.path == path and again.build_btn.isEnabled()


def test_page_ticks_and_build(qapp, tmp_path):
    from wolfrat.client_patcher_panel import ClientPatcherPanel
    panel = ClientPatcherPanel(None)
    panel.choose(game(tmp_path))
    boxes = panel.boxes
    assert boxes["laa"].isChecked() and boxes["vehicle"].isChecked() and boxes["spectator"].isChecked()
    assert not boxes["grass"].isChecked() and "disadvantage" in panel.notes["grass"].text()
    assert "TAC" not in "".join(n.text() for n in panel.notes.values())
    panel.build()
    assert panel.result is not None and (tmp_path / "jointops_wolfrat.exe").exists()
    assert "Close the game." in panel.result_lbl.text()


def test_page_lets_grass_be_unticked(qapp, tmp_path):
    from wolfrat.client_patcher_panel import ClientPatcherPanel
    first = C.build(game(tmp_path), all_on())
    panel = ClientPatcherPanel(None)
    panel.choose(first.exe_path)
    grass = panel.boxes["grass"]
    assert grass.isChecked() and grass.isEnabled() and grass.text().endswith("(already in - untick to remove)")
    assert not panel.boxes["spectator"].isEnabled()                  # the rest stay as they are
    grass.setChecked(False)
    panel.build()
    assert panel.result.changed == ["Longer grass removed"]


def test_page_explains_what_cannot_go_in(qapp, tmp_path):
    from wolfrat.client_patcher_panel import ClientPatcherPanel
    server = P.build(game(tmp_path, data=fake_exe(CLIENT)), everything(spectator=True, spawn=True))
    panel = ClientPatcherPanel(None)
    panel.choose(server.exe_path)
    for key in ("spectator", "grass"):
        assert not panel.boxes[key].isEnabled() and not panel.boxes[key].isChecked()
        assert "Patch a separate copy" in panel.notes[key].text()
    assert panel.boxes["vehicle"].isEnabled()


def test_page_fits_1024x768(qapp, tmp_path):
    from PyQt6.QtWidgets import QGroupBox, QTabWidget
    from wolfrat.client_patcher_panel import ClientPatcherPanel
    tabs = QTabWidget()
    panel = ClientPatcherPanel(None)
    panel.choose(game(tmp_path))
    tabs.addTab(panel, "Client patcher")
    tabs.resize(1004, 540)
    tabs.show()
    for _ in range(20):
        qapp.processEvents()
    for group in panel.findChildren(QGroupBox):
        assert group.height() >= group.minimumSizeHint().height()
    tabs.hide()
