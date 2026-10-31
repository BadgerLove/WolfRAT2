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
    # Version 2 appends: 250 target rows of 32 bytes, 3 band totals + 3 band
    # histograms, 3 band rows (checked against payload.c's offsetof, 2026-10-31).
    assert lc._OFF["targets"] == lc._OFF["players"] + 64 * lc.SLOTS == 16700
    assert lc._OFF["band_cal_total"] == 16700 + 32 * lc.SLOTS
    assert lc._OFF["band_cal"] == lc._OFF["band_cal_total"] + 4 * lc.BANDS
    assert lc._OFF["band_totals"] == lc._OFF["band_cal"] + 4 * lc.BINS * lc.BANDS
    assert lc._V2_SIZE == lc._OFF["band_totals"] + 32 * lc.BANDS == 26344
    assert lc._OFF["tune_mode"] == 26344
    assert lc._OFF["tune_log"] == 26344 + 28
    assert lc.SHARED_SIZE == lc._V3_SIZE == 26344 + 28 + 32 * lc.TUNE_LOG == 28420


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


def _v2(**kw):
    b = _block(**kw)
    b.version, b.size = 2, lc.SHARED_SIZE
    return b


def test_targets_and_bands_from_a_version_2_block():
    b = _v2(stats_since=1000)
    for slot, name, ping in ((0, b"Low", 30), (5, b"OscarMike247", 125), (9, b"Far", 210)):
        b.players[slot].entity, b.players[slot].ping, b.players[slot].name = 1 + slot, ping, name
    t = b.targets[9]
    t.close_low, t.hits_low, t.close_lag, t.hits_lag = 200, 50, 100, 30
    t.close_lag_plain, t.plain_lag, t.gained, t.lost = 100, 25, 9, 4
    band = b.band_totals[2]                     # the helper's own totals, incl. leavers
    band.close_low, band.hits_low = 400, 90
    zero = -lc.BIN_MIN // lc.BIN_MS
    b.band_cal[2][zero + 10] = 7                # +80..+88
    b.band_cal_total[2] = 7
    _, s = lc.parse_stats(bytes(b), now_ticks=2000)
    assert s.has_targets
    far = next(x for x in s.targets if x.name == "Far")
    assert (far.ping, far.close_low, far.hits_low, far.gained, far.lost) == (210, 200, 50, 9, 4)
    assert [x.label for x in s.bands] == ["under 80", "80-149", "150+"]
    assert [x.players for x in s.bands] == [1, 1, 1]
    assert (s.bands[2].close_low, s.bands[2].hits_low) == (400, 90)      # not the row sum
    assert s.bands[2].cal_median == 84 and s.bands[2].cal_total == 7
    assert s.bands[0].cal_median is None


def test_a_version_1_block_has_no_targets():
    b = _block(stats_since=1000)                # version 1, as the released helper
    b.players[0].entity, b.players[0].ping = 1, 100
    raw = bytes(b)[:lc._OFF["targets"]]          # all a v1 block holds
    _, s = lc.parse_stats(raw, now_ticks=2000)
    assert not s.has_targets and s.targets == [] and s.bands == []
    assert len(s.players) == 1


def test_reading_an_older_smaller_block_uses_its_size():
    """A version 1 block is 16,700 bytes: reading 26,344 can run off the old
    helper's image.  All 250 players must still come through."""
    b = _block()
    b.size = lc._OFF["targets"]
    b.players[200].entity = 7
    data = bytes(b)[:b.size]

    class Proc:
        def read(self, address, size):
            if address + size > len(data):
                raise lc.LagCompError("past the end")
            return data[address:address + size]

    raw = lc._read_block(Proc(), 0)
    assert len(raw) == lc.SHARED_SIZE
    assert lc._Shared.from_buffer_copy(raw).players[200].entity == 7


def test_pending_reset_clears_the_target_numbers_too():
    b = _v2(reset_seq=2, seen_reset_seq=1, stats_since=1000)
    b.players[1].entity = 2
    b.targets[1].close_low = 50
    b.band_totals[0].close_low = 50
    b.band_cal_total[1] = 3
    _, s = lc.parse_stats(bytes(b), now_ticks=5000)
    assert s.targets[0].close_low == 0
    assert s.bands[0].close_low == 0 and s.bands[1].cal_total == 0


def test_history_keeps_bands_and_players_who_left(tmp_path):
    book = lc.HistoryBook(str(tmp_path / "h.json"))
    settings = lc.Settings(False, 40, 400, 80)

    def stats(names):
        s = lc.Stats(tests=10, hits=2, since=0x10, has_targets=True,
                     bands=[lc.BandStats(label) for label in lc.BAND_LABELS])
        s.targets = [lc.TargetStats(i, n, 100, 5, 1, 0, 0, 0, 0, 0, 0) for i, n in enumerate(names)]
        return s

    book.record(1, stats(["A", "B"]), settings, now=1_000_000)
    book.record(1, stats(["A"]), settings, now=1_000_030)           # B left
    row = book.rows[0]
    assert sorted(t["name"] for t in row["targets"]) == ["A", "B"]
    assert [b["label"] for b in row["bands"]] == list(lc.BAND_LABELS)


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


def test_v3_auto_tune_and_event_logger(tmp_path):
    b = _block(stats_since=1000, tests=400, hits=110, plain_hits=90, gained=30, lost=10,
               extra_ms=40, tune_mode=lc.TUNE_APPLY, tune_value_ms=65,
               tune_median_ms=68, tune_have_median=1, tune_samples=340,
               tune_window_mins=8, tune_decisions=3)
    b.version, b.size = 3, lc.SHARED_SIZE
    step = lc._TuneStep(minute=3, tick_count=200000, before_ms=60, after_ms=65,
                        median_ms=68, have_median=1, samples=340, window_mins=8)
    raw_step = (lc.ctypes.c_uint32 * 8).from_buffer_copy(bytes(step))
    b.tune_log[2] = raw_step
    _, s = lc.parse_stats(bytes(b), now_ticks=201000)
    assert s.has_tuner and s.tune_mode == lc.TUNE_APPLY
    assert (s.tune_value_ms, s.tune_median_ms, s.tune_samples, s.tune_decisions) == (65, 68, 340, 3)
    assert len(s.tune_log) == 1 and s.tune_log[0].after_ms == 65

    logger = lc.EventLogger(str(tmp_path))
    settings = lc.Settings(False, 40, 400, 80, lc.TUNE_APPLY)
    added = logger.record_poll(999, s, settings, include_snapshot=True, now=1_700_000_000)
    assert len(added) == 2
    assert "Auto-tune min #3 [apply]: Extra +60 -> +65 ms" in added[0]
    assert "Snapshot" in added[1] and "active Extra +65 ms" in added[1]
    # Calling again with same minute does not duplicate the tune step
    assert logger.record_poll(999, s, settings, include_snapshot=False, now=1_700_000_005) == []
    assert (tmp_path / "wolfrat_lag_comp_events.log").exists()
    assert (tmp_path / "wolfrat_lag_comp_events.jsonl").exists()
