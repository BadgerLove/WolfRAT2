"""!map / !add / !remove / !vote map lookup, with Dale's typos of 2026-10-15
against names from his server's map list."""

from __future__ import annotations

import pytest

from wolfrat import map_match as mm
from wolfrat.app import MissionsStore
from wolfrat.runtime import DesktopRuntime

AVAILABLE = [
    ("Training: Stinger", "00TRC.BMS"),
    ("DM - Sulphur Creek", "DM-SulphurCreek.bms"),
    ("TD - Stinger Test", "TD-StingerTest.bms"),
    ("TD - Sulphur Creek", "TD-SulphurCreek.bms"),
    ("TK - Sulphur Creek", "TK-SulphurCreek.bms"),
    ("AS - Sulawa Spaceport", "AS-SulawaSpaceport.bms"),
    ("AS - Kutu Tower Assault", "AS-KutuTowerAssault.BMS"),
    ("AS - Villa Valley", "AS-VillaValley.bms"),
    ("AS - Villa Valley TAC", "AS-VillaValleyTAC.npj"),
    ("AD - Dust and Bones B", "AD-DustandBones.bms"),
]
ROTATION = ["0: TD-SulphurCreek.bms - () () () <CURRENT MISSION> <>",
            "1: AS-VillaValleyTAC.npj - () () () <> <NEXT MISSION>"]


@pytest.fixture
def store(tmp_path):
    s = MissionsStore(DesktopRuntime.isolated(tmp_path))
    s._data = {"rotation": [], "available": [{"name": n, "file": f} for n, f in AVAILABLE]}
    s.update_rotation(ROTATION)
    return s


@pytest.mark.parametrize("typed, file", [
    # every one of these said "Map not found" on 2026-10-15
    ("td_sting", "TD-StingerTest.bms"),
    ("TD-Sting", "TD-StingerTest.bms"),
    ("td-stinger", "TD-StingerTest.bms"),
    ("td-sul", "TD-SulphurCreek.bms"),
    ("td-sulphur", "TD-SulphurCreek.bms"),
    ("td sulp", "TD-SulphurCreek.bms"),
    # these worked and still do
    ("td - sul", "TD-SulphurCreek.bms"),
    ("stinger test", "TD-StingerTest.bms"),
    ("tower", "AS-KutuTowerAssault.BMS"),
    # "stinger" loaded Training: Stinger - training maps now come last
    ("stinger", "TD-StingerTest.bms"),
    # the file name works too, with or without its extension, any case
    ("as-kututowerassault.bms", "AS-KutuTowerAssault.BMS"),
    ("DM-SULPHURCREEK", "DM-SulphurCreek.bms"),
    # a map in the rotation, and TAC (box ticked), win a tie as before
    ("sulphur", "TD-SulphurCreek.bms"),
    ("villa", "AS-VillaValleyTAC.npj"),
])
def test_tonights_typos_find_the_map(store, typed, file):
    result = store.match(typed)
    assert result.entry is not None and result.entry.file == file


def test_rotation_maps_keep_their_proper_names(store):
    name, file, source = store.find("td-sul")
    assert (name, file, source) == ("TD - Sulphur Creek", "TD-SulphurCreek.bms", "rotation")


def test_a_wrong_prefix_asks_instead_of_guessing(store):
    result = store.match("ts-sul")
    assert result.entry is None
    assert {e.file for e in result.choices} == {"DM-SulphurCreek.bms", "TD-SulphurCreek.bms",
                                               "TK-SulphurCreek.bms", "AS-SulawaSpaceport.bms"}
    assert store.find("ts-sul") == (None, None, None)          # nothing is loaded on a guess


def test_several_equally_good_maps_ask(tmp_path):
    s = MissionsStore(DesktopRuntime.isolated(tmp_path))
    s._data = {"rotation": [], "available": [{"name": n, "file": f} for n, f in AVAILABLE]}
    result = s.match("sulphur creek")                          # DM / TD / TK, none in rotation
    assert result.entry is None and len(result.choices) == 3


def test_nothing_like_it_is_not_found(store):
    result = store.match("xyzzy")
    assert result.entry is None and not result.choices
    assert store.match("").entry is None and store.match("   ").entry is None


def test_the_choices_fit_the_chat():
    many = [mm.MapEntry(f"AS - Some Long Map Name {i}", f"AS-Map{i}.bms", "available") for i in range(12)]
    lines = mm.choices_lines("map", many)
    assert 1 <= len(lines) <= 2 and all(len(line) <= 59 for line in lines)
    assert lines[0].startswith("Which one? ") and "more" in lines[-1]
    two = mm.choices_lines("sul", [mm.MapEntry("DM - Sulphur Creek", "a.bms", "available"),
                                  mm.MapEntry("TD - Sulphur Creek", "b.bms", "available")])
    assert two == ["Which one? DM - Sulphur Creek, TD - Sulphur Creek"]
