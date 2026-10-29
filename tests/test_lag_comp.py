"""Lag compensation: the helper's block, the numbers and the session history.

The helper itself is proven against real servers (test copies on the FMJ box,
a host plus a lagged joiner); these tests cover what WolfRAT does with it."""

import json
import struct

import pytest

from wolfrat import lag_comp as lc


def _block(**kw):
    b = lc._Shared()
    b.magic = lc.MAGIC
    b.version = lc.VERSION
    b.status = 1
    b.tick_us = 15800
    for k, v in kw.items():
        setattr(b, k, v)
    return b


def test_block_layout_matches_the_helper():
    # payload.c: header 76 bytes before the settings, 250 player rows of 64 bytes.
    assert lc.SLOTS == 250
    assert lc._OFF["enabled"] == 116
    assert lc._OFF["reset_seq"] == 136
    assert lc._OFF["cal"] == 188
    assert lc._OFF["players"] == 188 + 4 * lc.BINS
    assert lc.SHARED_SIZE == lc._OFF["players"] + 64 * lc.SLOTS


def test_bundled_helper_exports_its_block():
    path = lc.bundled_dll_path()
    assert path, "wolfrat_lagcomp.dll must ship beside lag_comp.py"
    with open(path, "rb") as fh:
        data = fh.read()
    assert data[:2] == b"MZ"
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    assert struct.unpack_from("<H", data, pe + 4)[0] == 0x14C      # 32-bit, like the game
    assert lc.export_rva(data, lc.EXPORT_NAME)


def test_helper_survives_its_temp_folder_being_cleaned(tmp_path, monkeypatch):
    """2026-10-23: a temp clean-up deleted the helper out of a running WolfRAT's
    _MEI folder.  Read once, it is kept in memory and its data-folder copy is
    rewritten if that goes too."""
    monkeypatch.setattr(lc, "_bundled_bytes", None)
    path, name, data = lc.materialize_dll(str(tmp_path))
    monkeypatch.setattr(lc, "bundled_dll_path", lambda: None)       # temp folder gone
    (tmp_path / name).unlink()                                     # and the copy
    again = lc.materialize_dll(str(tmp_path))
    assert again == (path, name, data) and (tmp_path / name).read_bytes() == data
    monkeypatch.setattr(lc, "_bundled_bytes", None)                # never read: says so
    with pytest.raises(lc.LagCompError, match="missing from this build"):
        lc.materialize_dll(str(tmp_path))


def test_quantiles_from_the_histogram():
    cal = [0] * lc.BINS
    zero = -lc.BIN_MIN // lc.BIN_MS            # bin holding 0..+8 ms
    cal[zero] = 10
    cal[zero + 5] = 10                          # +40..+48
    cal[zero + 20] = 10                         # +160..+168
    low, mid, high = lc.histogram_quantiles(cal)
    assert (low, mid, high) == (4, 44, 164)
    assert lc.histogram_quantiles([0] * lc.BINS) == (None, None, None)


def test_parse_stats_totals_and_players():
    b = _block(tests=500, hits=120, plain_hits=100, gained=40, lost=20, stats_since=1000)
    p = b.players[3]
    p.entity, p.ping, p.rewind_ms, p.tests, p.hits, p.plain_hits = 0x1234, 125, 165, 300, 80, 60
    p.name = b"OscarMike247"
    _, s = lc.parse_stats(bytes(b), now_ticks=61000)
    assert (s.hits, s.plain_hits, s.gained, s.lost) == (120, 100, 40, 20)
    assert s.seconds == 60
    assert round(s.gain_percent) == 20
    assert len(s.players) == 1 and s.players[0].name == "OscarMike247"
    assert s.players[0].slot == 3 and s.players[0].rewind_ms == 165


def test_a_pending_reset_reads_as_cleared():
    # An empty server does not tick, so the helper has not seen the reset yet.
    b = _block(tests=500, hits=120, plain_hits=100, reset_seq=2, seen_reset_seq=1,
               stats_since=1000, cal_total=50)
    b.cal[60] = 50
    b.players[0].entity = 1
    b.players[0].tests = 9
    _, s = lc.parse_stats(bytes(b), now_ticks=90000)
    assert s.tests == 0 and s.hits == 0 and s.cal_total == 0 and s.cal_median is None
    assert s.seconds == 0
    assert s.players[0].tests == 0


def test_suggestion_needs_enough_close_shots():
    s = lc.Stats(cal_total=299, cal_median=37)
    assert s.suggested_extra() is None
    s.cal_total = 300
    assert s.suggested_extra() == 40
    assert lc.Stats(cal_total=900, cal_median=-14).suggested_extra() == -10


def test_settings_are_kept_in_range():
    s = lc.Settings(True, 999, 5, -3).clamp()
    assert (s.observe, s.extra_ms, s.max_ms, s.min_ping_ms) == (True, 300, 50, 0)


def test_history_updates_one_row_per_session(tmp_path):
    book = lc.HistoryBook(str(tmp_path / "h.json"))
    settings = lc.Settings(False, 40, 400, 80)
    assert not book.record(10, lc.Stats(), settings, now=1_000_000)       # nothing yet
    s = lc.Stats(tests=50, hits=12, plain_hits=10, seconds=600, since=0xABC)
    assert book.record(10, s, settings, now=1_000_000)
    s.hits = 30
    book.record(10, s, settings, now=1_000_100)
    assert len(book.rows) == 1 and book.rows[0]["hits"] == 30
    book.record(10, lc.Stats(tests=5, hits=1, since=0xDEF), settings, now=1_000_200)
    assert len(book.rows) == 2
    book.save()
    again = lc.HistoryBook(str(tmp_path / "h.json"))
    assert [r["key"] for r in again.rows] == ["10-00000abc", "10-00000def"]
    assert json.loads((tmp_path / "h.json").read_text())[0]["setting"] == \
        "ping +40 ms, none under 80, max 400"


def test_describe_setting():
    assert lc.describe_setting(lc.Settings(True, -20, 300, 60)) == \
        "watch only, ping -20 ms, none under 60, max 300"


def test_preload_foreign_detection():
    class FakeProc:
        def __init__(self, hooked: bool):
            self.hooked = hooked

        def modules(self):
            return {}

        def read(self, address: int, size: int) -> bytes:
            if address == lc._LEG_TAIL[0]:
                return lc._LEG_TAIL[1]
            for site, func in lc._RETAIL_SITES:
                if address == site:
                    target = (0x67200000 + site) if self.hooked else func
                    return lc._call_bytes(site, target)
            raise lc.LagCompError("unmapped")

    assert lc.read_state(FakeProc(hooked=False)).kind == lc.UNKNOWN
    assert lc.read_state(FakeProc(hooked=True)).kind == lc.FOREIGN
