"""Import map lists saved by the original WolfRAT (Dale, 2026-11-21).

Format from SgtMudd's 1-coop.txt: a template first line, then
<title>[FILENAME]<file>[ROUNDS]<n> per map; ROUNDS 1 = once (x1),
2 = twice, swapping sides (x2) (Dale: "rounds is play once or twice").
"""

import json

import pytest

from wolfrat import app as app_module
from wolfrat import map_list_import
from wolfrat.admin_commands import AvailableMission
from wolfrat.app import MissionsStore, MissionsTab
from wolfrat.runtime import DesktopRuntime

OLD_LIST = (
    "FILE_TEXT[FILENAME]FILE_NAME.EXT[ROUNDS]0(1)\r\n"
    "jar of flies[FILENAME]jar_of_flies.bms[ROUNDS]1\r\n"
    "Operation: Supply & Demand (Nut[FILENAME]OperationSupply&Demand.npj[ROUNDS]1\r\n"
    "The tomb[FILENAME]The_tomb.bms[ROUNDS]1\r\n"
    "Mcubed arena[FILENAME]TD-mcubedarena.bms[ROUNDS]2\r\n"
    "HmS Medic!!TR[FILENAME]HmS_Medic!!TR.bms[ROUNDS]1\r\n"
    "SHADOWS RECALL[FILENAME]SHADOWS_OF_DEATH(RECALL).npj[ROUNDS]1\r\n"
    "The tomb[FILENAME]The_tomb.bms[ROUNDS]1\r\n"
)


def _files(imported):
    return [entry["file"] for entry in imported.maps]


def test_reads_the_old_wolfrat_format_in_order():
    imported = map_list_import.parse_map_list(OLD_LIST, "1-coop")
    assert _files(imported) == [
        "jar_of_flies.bms", "OperationSupply&Demand.npj", "The_tomb.bms",
        "TD-mcubedarena.bms", "HmS_Medic!!TR.bms",
        "SHADOWS_OF_DEATH(RECALL).npj", "The_tomb.bms",
    ]
    assert imported.left_out == []
    assert imported.twice == 1  # The_tomb, kept twice like the old list


def test_rounds_2_is_played_twice_everything_else_once():
    imported = map_list_import.parse_map_list(
        "a[FILENAME]A.bms[ROUNDS]1\nb[FILENAME]B.bms[ROUNDS]2\n"
        "c[FILENAME]C.npj[ROUNDS]0\nd[FILENAME]D.npz\n"
    )
    assert [e["count"] for e in imported.maps] == [1, 2, 1, 1]
    assert imported.x2 == 1


def test_plain_one_file_per_line_and_blank_lines():
    imported = map_list_import.parse_map_list('\nA.bms\n\n  "B.npj"  \nC.NPZ\n')
    assert _files(imported) == ["A.bms", "B.npj", "C.NPZ"]


def test_lines_no_admin_tool_could_add_are_left_out_with_the_reason():
    imported = map_list_import.parse_map_list(
        "Militia[FILENAME]TD-Militia Strongpoint.bms[ROUNDS]1\n"
        "readme: these are my maps\n"
        "Odd[FILENAME]map.txt[ROUNDS]1\n"
        "Café[FILENAME]Café.bms[ROUNDS]1\n"
        "Good[FILENAME]Good.bms[ROUNDS]1\n"
    )
    assert _files(imported) == ["Good.bms"]
    reasons = [why for _n, _line, why in imported.left_out]
    assert reasons == [
        "a space in the file name: no admin tool can add it",
        "not a map line",
        "the file name doesn't end in .bms, .npj or .npz",
        "letters the admin port can't send",
    ]
    assert [n for n, _line, _why in imported.left_out] == [1, 2, 3, 4]


def test_reads_windows_1252_files_and_names_the_list_after_the_file(tmp_path):
    path = tmp_path / "1-coop.txt"
    path.write_bytes("Caf\xe9 night[FILENAME]Cafe.bms[ROUNDS]1\r\n".encode("cp1252"))
    imported = map_list_import.parse_map_list(
        map_list_import.read_text(path), map_list_import.list_name(path)
    )
    assert imported.name == "1-coop"
    assert _files(imported) == ["Cafe.bms"]


def test_not_on_server_lists_each_missing_map_once():
    maps = [{"file": f, "count": 1} for f in ("A.bms", "gone.npj", "GONE.npj", "b.BMS")]
    assert map_list_import.not_on_server(maps, ["a.BMS", "B.bms"]) == ["gone.npj"]


# --------------------------------------------------------------------------
# The Missions tab button
# --------------------------------------------------------------------------

class FakeServer:
    def __init__(self, catalog=()):
        self.is_connected = True
        self.available_mission_entries = tuple(
            AvailableMission(catalog_index=i, filename=f, description=f, revision=1)
            for i, f in enumerate(catalog, start=1)
        )
        self.mission_entries = ()
        self.logs = []

    def _log(self, message, *args, **kwargs):
        self.logs.append(message)

    def refresh_missions(self, quiet=False):
        raise AssertionError("importing must not talk to the server")

    refresh_available_maps = refresh_game_state = refresh_missions


@pytest.fixture
def shown(monkeypatch):
    boxes = []
    monkeypatch.setattr(app_module.QMessageBox, "information",
                        lambda *args, **kwargs: boxes.append(args[2]))
    return boxes


def _tab(qtbot, tmp_path, catalog=(), presets=None):
    rt = DesktopRuntime.isolated(tmp_path / "data")
    if presets is not None:
        (tmp_path / "data" / "wolfrat_rotations.json").write_text(json.dumps(presets), encoding="utf-8")
    server = FakeServer(catalog)
    tab = MissionsTab(server, MissionsStore(rt), rt)
    qtbot.addWidget(tab)
    return tab, server


def _saved(tmp_path):
    with open(tmp_path / "data" / "wolfrat_rotations.json", encoding="utf-8") as handle:
        return json.load(handle)


def test_import_saves_the_list_selects_it_and_says_what_is_missing(qtbot, tmp_path, monkeypatch, shown):
    path = tmp_path / "1-coop.txt"
    path.write_text(OLD_LIST, encoding="ascii")
    tab, server = _tab(qtbot, tmp_path, catalog=["jar_of_flies.bms", "The_tomb.bms"])
    monkeypatch.setattr(tab, "_choose_import_files", lambda: [str(path)])

    tab._import_old_lists()

    saved = _saved(tmp_path)["1-coop"]
    assert [e["file"] for e in saved][:3] == ["jar_of_flies.bms", "OperationSupply&Demand.npj", "The_tomb.bms"]
    assert saved[3] == {"file": "TD-mcubedarena.bms", "count": 2}
    assert tab.preset_combo.currentText() == "1-coop"
    report = shown[-1]
    assert "'1-coop': 7 maps, 1 played twice (x2), 1 listed more than once." in report
    assert "Not on this server: OperationSupply&Demand.npj, TD-mcubedarena.bms" in report
    assert any(line.startswith("[IMPORT] '1-coop': 7 maps") for line in server.logs)


def test_an_existing_list_of_that_name_is_only_replaced_when_the_admin_says_so(qtbot, tmp_path, monkeypatch, shown):
    path = tmp_path / "1-coop.txt"
    path.write_text(OLD_LIST, encoding="ascii")
    old = [{"file": "OLD.bms", "count": 1}]
    tab, _server = _tab(qtbot, tmp_path, presets={"1-coop": old})
    monkeypatch.setattr(tab, "_choose_import_files", lambda: [str(path)])
    asked = []
    monkeypatch.setattr(tab, "_confirm_import_overwrite",
                        lambda name, a, b: asked.append((name, a, b)) or False)

    tab._import_old_lists()

    saved = _saved(tmp_path)
    assert asked == [("1-coop", 1, 7)]
    assert saved["1-coop"] == old
    assert len(saved["1-coop (imported)"]) == 7
    assert tab.preset_combo.currentText() == "1-coop (imported)"
    assert "Not checked against the server's maps (not connected yet)." in shown[-1]

    monkeypatch.setattr(tab, "_confirm_import_overwrite", lambda name, a, b: True)
    tab._import_old_lists()
    assert len(_saved(tmp_path)["1-coop"]) == 7


def test_several_files_at_once_and_files_with_no_maps(qtbot, tmp_path, monkeypatch, shown):
    first = tmp_path / "coop.txt"
    first.write_text("a[FILENAME]A.bms[ROUNDS]1\n", encoding="ascii")
    second = tmp_path / "tdm.txt"
    second.write_text("b[FILENAME]B.bms[ROUNDS]2\n", encoding="ascii")
    empty = tmp_path / "notes.txt"
    empty.write_text("just some notes\n", encoding="ascii")
    tab, _server = _tab(qtbot, tmp_path)
    monkeypatch.setattr(tab, "_choose_import_files", lambda: [str(first), str(empty), str(second)])

    tab._import_old_lists()

    saved = _saved(tmp_path)
    assert saved["coop"] == [{"file": "A.bms", "count": 1}]
    assert saved["tdm"] == [{"file": "B.bms", "count": 2}]
    assert "notes" not in saved
    assert "notes.txt: no maps found in it." in shown[-1]
    assert tab.preset_combo.currentText() == "tdm"


def test_the_button_is_in_the_far_right_column(qtbot, tmp_path):
    tab, _server = _tab(qtbot, tmp_path)
    assert tab.import_btn.text() == "Import Old\nWolfRAT List"
