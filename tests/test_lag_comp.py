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
    assert lc._V3_SIZE == 26344 + 28 + 32 * lc.TUNE_LOG == 28420


def test_version_4_block_layout_matches_the_spec():
    # tmp/lagreview_2026-11-19/v4/SPEC.md: 528 bytes appended, nothing before them moves.
    assert lc.ctypes.sizeof(lc._WhatIf) == 68
    assert lc._OFF["bot_totals"] == lc._V3_SIZE == 28420
    assert [lc._OFF[k] - 28420 for k in (
        "dead_hits", "dead_plain_hits", "near_steps", "tracked_persons", "tracked_bots",
        "low_tests", "owner_pid", "owner_heartbeat", "what_if_offset_ms",
        "what_if_band_min_ping", "what_if")] == [32, 36, 40, 44, 48, 52, 56, 60, 64, 96, 120]
    # What a version 4 helper's block measures, for good: version 5 comes after it.
    assert lc._V4_SIZE == 28420 + 528 == 28948


def test_version_5_block_layout_matches_the_spec():
    # tmp/lagcomp-v5/SPEC.md: 16 bytes appended to the version 4 block, which must not move.
    assert [lc._OFF[k] for k in ("device_steps", "device_hits", "device_rule", "reserved5")] == [
        28948, 28952, 28956, 28960]
    assert lc._OFF["device_steps"] == lc._V4_SIZE == 28948
    # What a version 5 helper's block measures, for good: version 6 comes after it.
    assert lc._V5_SIZE == 28948 + 16 == 28964


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


# ------------------------------------------------- helper version 4 (2026-11-20)
# Bots are rewound and counted in their own row, dead bodies are counted apart,
# and every bullet is also judged at seven other rewinds (the what-if table).

# What a version 4 helper's block measures (the size assert in its payload.c).  A
# number here on purpose, not lc._V4_SIZE: these blocks must stay what that
# helper really writes even if lag_comp's own idea of the size is ever wrong.
V4_BYTES = 28948


def _v4(**kw):
    b = _block(**kw)
    b.version, b.size = 4, V4_BYTES
    for i, off in enumerate(lc.WHATIF_OFFSETS):        # the helper writes these at load
        b.what_if_offset_ms[i] = off
    for i, ping in enumerate(lc.WHATIF_BAND_MIN_PING):
        b.what_if_band_min_ping[i] = ping
    return b


def _what_if(b, band, steps, hits, human=None):
    w = b.what_if[band]
    w.steps = steps
    for i, n in enumerate(hits):
        w.hits[i] = n
        w.hits_human[i] = (human or hits)[i]


def test_version_4_bots_dead_bodies_and_what_if():
    b = _v4(tests=900, hits=200, plain_hits=150, stats_since=1000,
            dead_hits=7, dead_plain_hits=5, near_steps=640, low_tests=310,
            tracked_persons=48, tracked_bots=40, owner_pid=4242, owner_heartbeat=99)
    bots = b.bot_totals
    bots.close_low, bots.hits_low, bots.close_lag, bots.hits_lag = 80, 20, 300, 120
    bots.close_lag_plain, bots.plain_lag, bots.gained, bots.lost = 300, 90, 40, 10
    _what_if(b, 2, 500, [100, 90, 110, 130, 150, 140, 120, 80], [10, 9, 11, 13, 15, 14, 12, 8])
    _what_if(b, 5, 60, [5, 1, 2, 3, 9, 4, 4, 4])
    _, s = lc.parse_stats(bytes(b), now_ticks=2000)
    assert s.has_v4 and s.has_tuner and s.has_targets
    assert s.bot_totals.label == "Bots (AI)" and s.bot_totals.players == 40
    assert (s.bot_totals.close_lag, s.bot_totals.hits_lag, s.bot_totals.plain_lag,
            s.bot_totals.gained, s.bot_totals.lost) == (300, 120, 90, 40, 10)
    assert (s.dead_hits, s.dead_plain_hits, s.near_steps, s.low_tests) == (7, 5, 640, 310)
    assert (s.tracked_persons, s.tracked_bots) == (48, 40)
    assert s.what_if_offsets == [0x7FFFFFFF, -80, -40, 0, 40, 80, 120, 160]
    assert [r.label for r in s.what_if] == ["under 50", "50-79", "80-99", "100-149",
                                           "150-199", "200+"]
    assert [r.min_ping for r in s.what_if] == [0, 50, 80, 100, 150, 200]
    row = s.what_if[2]
    assert (row.steps, row.hits[4], row.hits_human[4]) == (500, 150, 15)
    assert [lc.what_if_column_label(o) for o in s.what_if_offsets] == [
        "Plain JO", "ping -80", "ping -40", "ping +0", "ping +40", "ping +80",
        "ping +120", "ping +160"]


def test_older_helpers_blocks_still_parse_without_version_4():
    full = _v4(tests=5, dead_hits=3, stats_since=1000)
    full.players[0].entity, full.players[0].ping = 1, 100
    # A version 3 helper: its block ends at 28,420 bytes.
    v3 = _block(tests=5, stats_since=1000)
    v3.version, v3.size = 3, lc._V3_SIZE
    v3.players[0].entity, v3.players[0].ping = 1, 100
    for raw in (bytes(v3)[:lc._V3_SIZE], bytes(v3)):
        _, s = lc.parse_stats(raw, now_ticks=2000)
        assert s.has_tuner and s.has_targets and not s.has_v4
        assert s.bot_totals is None and s.what_if == [] and s.dead_hits == 0
    # Version 2 and version 1 sizes.
    v2 = _block(stats_since=1000)
    v2.version, v2.size = 2, lc._V2_SIZE
    _, s = lc.parse_stats(bytes(v2)[:lc._V2_SIZE], now_ticks=2000)
    assert s.has_targets and not s.has_tuner and not s.has_v4
    _, s = lc.parse_stats(bytes(_block(stats_since=1000))[:lc._OFF["targets"]], now_ticks=2000)
    assert not s.has_targets and not s.has_tuner and not s.has_v4
    # A block that says 4 but is not that big is not trusted to have the new part.
    full.size = lc._V3_SIZE
    _, s = lc.parse_stats(bytes(full), now_ticks=2000)
    assert not s.has_v4 and s.dead_hits == 0


def test_a_pending_reset_clears_the_version_4_counters_and_keeps_the_rest():
    b = _v4(reset_seq=2, seen_reset_seq=1, stats_since=1000, dead_hits=7, dead_plain_hits=5,
            near_steps=640, low_tests=310, tracked_persons=48, tracked_bots=40,
            owner_pid=4242, owner_heartbeat=123456)
    b.bot_totals.close_lag = 300
    _what_if(b, 2, 500, [100, 90, 110, 130, 150, 140, 120, 80])
    block, s = lc.parse_stats(bytes(b), now_ticks=5000)
    assert s.bot_totals.close_lag == 0
    assert (s.dead_hits, s.dead_plain_hits, s.near_steps, s.low_tests) == (0, 0, 0, 0)
    assert all(r.steps == 0 and not any(r.hits) for r in s.what_if)
    # Not counters: who runs it, the column offsets, the bands, who is tracked now.
    assert (block.owner_pid, block.owner_heartbeat) == (4242, 123456)
    assert (s.tracked_persons, s.tracked_bots) == (48, 40)
    assert s.what_if_offsets == list(lc.WHATIF_OFFSETS)
    assert [r.label for r in s.what_if][0] == "under 50"


def test_most_hits_overall_counts_the_bands_at_or_above_the_minimum_ping():
    b = _v4(stats_since=1000)
    _what_if(b, 0, 5000, [900, 0, 0, 0, 0, 0, 0, 0])                 # under 50: plain JO wins
    _what_if(b, 2, 200, [40, 30, 35, 50, 70, 60, 45, 20])            # 80-99
    _what_if(b, 4, 99, [20, 10, 15, 25, 30, 41, 22, 10])             # 150-199
    _, s = lc.parse_stats(bytes(b), now_ticks=2000)
    assert s.what_if_best(80) == (299, [])                           # under 300 bullets
    b.what_if[4].steps = 100
    _, s = lc.parse_stats(bytes(b), now_ticks=2000)
    assert s.what_if_best(80) == (300, [5])                          # 60+41 beats 70+30: ping +80
    assert s.what_if_best(0) == (5300, [0])                          # everyone: plain JO
    assert s.what_if_best(150) == (100, [])
    assert s.what_if_best(999)[0] == 0                               # above every band: the top one
    b.what_if[4].hits[4] = 31                                        # 70+31 = 60+41: a tie
    _, s = lc.parse_stats(bytes(b), now_ticks=2000)
    assert s.what_if_best(80) == (300, [4, 5])


def test_band_names_come_from_the_blocks_own_pings():
    assert lc.what_if_band_labels([0, 40, 90]) == ["under 40", "40-89", "90+"]
    # Never filled in (all zero): the helper's own bands.
    assert lc.what_if_band_labels([0] * 6) == ["under 50", "50-79", "80-99", "100-149",
                                               "150-199", "200+"]


def _v4_stats():
    b = _v4(tests=900, hits=200, plain_hits=150, stats_since=1000, dead_hits=7,
            dead_plain_hits=5, near_steps=640, low_tests=310, tracked_persons=48, tracked_bots=40)
    b.bot_totals.close_lag, b.bot_totals.hits_lag = 300, 120
    b.bot_totals.close_lag_plain, b.bot_totals.plain_lag = 300, 90
    _what_if(b, 2, 500, [100, 90, 110, 130, 150, 140, 120, 80], [10, 9, 11, 13, 15, 14, 12, 8])
    return lc.parse_stats(bytes(b), now_ticks=61000)[1]


def test_history_row_and_snapshot_keep_the_version_4_numbers(tmp_path):
    s = _v4_stats()
    settings = lc.Settings(False, 70, 400, 80)
    # An old history file (rows without the new keys) still loads and is kept.
    old_row = {"key": "1-00000001", "started": "2026-11-01 20:00", "minutes": 30,
               "setting": "ping +70 ms, none under 80, max 400", "hits": 5, "plain_hits": 4,
               "gained": 2, "lost": 1, "cal_total": 9, "cal_median": 44}
    (tmp_path / "h.json").write_text(json.dumps([old_row]), encoding="utf-8")
    book = lc.HistoryBook(str(tmp_path / "h.json"))
    assert book.rows == [old_row]
    assert book.record(77, s, settings, now=1_700_000_000)
    book.save()
    rows = json.loads((tmp_path / "h.json").read_text(encoding="utf-8"))
    assert rows[0] == old_row
    row = rows[1]
    assert row["bots"]["label"] == "Bots (AI)" and row["bots"]["hits_lag"] == 120
    assert (row["dead_hits"], row["dead_plain_hits"], row["near_steps"]) == (7, 5, 640)
    assert row["what_if"]["offsets"] == [None, -80, -40, 0, 40, 80, 120, 160]
    assert row["what_if"]["rows"] == [{
        "band": "80-99", "min_ping": 80, "steps": 500,
        "hits": [100, 90, 110, 130, 150, 140, 120, 80],
        "human": [10, 9, 11, 13, 15, 14, 12, 8]}]                    # bands with bullets only

    logger = lc.EventLogger(str(tmp_path))
    added = logger.record_poll(77, s, settings, include_snapshot=True, now=1_700_000_000)
    assert "| bots: lag 40%/plain 30%" in added[-1]
    snap = json.loads((tmp_path / "wolfrat_lag_comp_events.jsonl").read_text(
        encoding="utf-8").splitlines()[-1])
    assert snap["kind"] == "snapshot" and snap["dead_hits"] == 7 and snap["near_steps"] == 640
    assert snap["bots"]["close_lag"] == 300 and snap["tracked_bots"] == 40
    assert snap["what_if"]["rows"][0]["hits"][4] == 150


def test_a_version_3_snapshot_has_none_of_the_new_keys(tmp_path):
    b = _block(stats_since=1000, tests=400, hits=110, plain_hits=90)
    b.version, b.size = 3, lc._V3_SIZE
    s = lc.parse_stats(bytes(b), now_ticks=61000)[1]
    logger = lc.EventLogger(str(tmp_path))
    line = logger.record_poll(5, s, lc.Settings(), include_snapshot=True, now=1_700_000_000)[-1]
    assert "bots:" not in line
    snap = json.loads((tmp_path / "wolfrat_lag_comp_events.jsonl").read_text(
        encoding="utf-8").splitlines()[-1])
    assert "what_if" not in snap and "bots" not in snap and "dead_hits" not in snap
    book = lc.HistoryBook(str(tmp_path / "h.json"))
    book.record(5, s, lc.Settings(), now=1_700_000_000)
    assert "what_if" not in book.rows[0] and "bots" not in book.rows[0]


# ------------------------------------------------- helper version 5 (2026-11-22)
# Claymore shrapnel is never rewound (a claymore fires by itself, nobody aimed
# it: plain JO decides) and is counted apart.  16 bytes after the version 4 block.

CLAYMORE_KEYS = ("device_steps", "device_hits", "device_rule")


# What a version 5 helper's block measures (its payload.c size assert).
V5_BYTES = 28964


def _v5(**kw):
    b = _v4(**kw)
    b.version, b.size = 5, V5_BYTES
    return b


def test_version_5_counts_claymore_shrapnel_apart():
    b = _v5(tests=900, hits=200, plain_hits=150, stats_since=1000, dead_hits=7,
            tracked_bots=40, device_steps=288, device_hits=3, device_rule=1)
    _what_if(b, 2, 500, [100, 90, 110, 130, 150, 140, 120, 80])
    _, s = lc.parse_stats(bytes(b), now_ticks=2000)
    assert s.has_v5 and s.has_v4 and s.has_tuner and s.has_targets
    assert (s.device_steps, s.device_hits, s.device_rule) == (288, 3, 1)
    # The version 4 part reads as before; the pellets are in none of it.
    assert (s.tests, s.hits, s.plain_hits, s.dead_hits) == (900, 200, 150, 7)
    assert s.bot_totals.players == 40 and s.what_if[2].hits[4] == 150
    # An exe the helper fits, but where it cannot tell a claymore's rounds apart.
    b.device_rule = 0
    _, s = lc.parse_stats(bytes(b), now_ticks=2000)
    assert s.has_v5 and s.device_rule == 0


def test_a_version_4_helpers_block_still_parses_as_version_4():
    """The trap (claymore design check, risk 5): _V4_SIZE was SHARED_SIZE.  Left
    like that when the block grew, a version 4 helper's block (28,948 bytes)
    stopped counting as version 4: no bots row, no what-if table, and no owner
    stamp, so two WolfRATs would fight over the calls again."""
    b = _v4(tests=900, hits=200, plain_hits=150, stats_since=1000, dead_hits=7,
            near_steps=640, tracked_bots=40, owner_pid=4242, owner_heartbeat=99)
    b.bot_totals.close_lag = 300
    _what_if(b, 2, 500, [100, 90, 110, 130, 150, 140, 120, 80])
    # Reading 28,964 bytes of a 28,948-byte block can work, and then the last
    # 16 are whatever that helper keeps after its block: never claymore counts.
    b.device_steps, b.device_hits, b.device_rule, b.reserved5 = 0xDEAD, 0xBEEF, 0x77, 0x55
    its_own = bytes(b)[:V4_BYTES]
    for raw in (its_own, its_own + b"\0" * 16, bytes(b)):
        block, s = lc.parse_stats(raw, now_ticks=2000)
        assert s.has_v4 and s.has_tuner and s.has_targets and not s.has_v5
        assert (s.device_steps, s.device_hits, s.device_rule) == (0, 0, 0)
        assert s.bot_totals.close_lag == 300 and s.bot_totals.players == 40
        assert (s.dead_hits, s.near_steps) == (7, 640) and s.what_if[2].hits[4] == 150
        assert (block.owner_pid, block.owner_heartbeat) == (4242, 99)


def test_reading_a_version_4_helpers_block_falls_back_to_its_own_size():
    """This WolfRAT with a version 4 helper in the server (another install's, or
    its own older one before the takeover): the whole block (50,980 bytes since
    version 6) cannot be read, so the block's own 28,948 are, and it is still a
    version 4 block."""
    b = _v4(stats_since=1000, dead_hits=7, owner_pid=4242)
    _what_if(b, 5, 60, [5, 1, 2, 3, 9, 4, 4, 4])
    data = bytes(b)[:V4_BYTES]
    asked = []

    class Proc:
        def read(self, address, size):
            asked.append(size)
            if address + size > len(data):
                raise lc.LagCompError("past the end")
            return data[address:address + size]

    raw = lc._read_block(Proc(), 0)
    assert asked == [50980, 4, 28948]                  # all of it, its size, then that much
    assert len(raw) == lc.SHARED_SIZE and raw[V4_BYTES:] == b"\0" * (50980 - V4_BYTES)
    block, s = lc.parse_stats(raw, now_ticks=2000)
    assert s.has_v4 and not s.has_v5 and s.dead_hits == 7 and s.what_if[5].steps == 60
    assert block.owner_pid == 4242


def test_a_block_that_says_5_but_is_not_that_big_is_read_as_version_4():
    b = _v5(stats_since=1000, dead_hits=7, device_steps=288, device_hits=3, device_rule=1)
    b.size = V4_BYTES
    _, s = lc.parse_stats(bytes(b), now_ticks=2000)
    assert s.has_v4 and not s.has_v5 and s.dead_hits == 7
    assert (s.device_steps, s.device_hits, s.device_rule) == (0, 0, 0)
    # The same for a read that came back short of the new part.
    b.size = lc.SHARED_SIZE
    _, s = lc.parse_stats(bytes(b)[:V4_BYTES], now_ticks=2000)
    assert s.has_v4 and not s.has_v5 and s.device_steps == 0


def test_a_pending_reset_clears_the_claymore_counters_and_keeps_the_rule():
    b = _v5(reset_seq=2, seen_reset_seq=1, stats_since=1000, dead_hits=7,
            device_steps=288, device_hits=3, device_rule=1)
    block, s = lc.parse_stats(bytes(b), now_ticks=5000)
    assert s.has_v5 and (s.device_steps, s.device_hits, s.dead_hits) == (0, 0, 0)
    # Not a count: what the helper found in this exe (as the helper's own reset).
    assert s.device_rule == 1
    assert (block.device_steps, block.device_hits, block.device_rule) == (0, 0, 1)
    b.seen_reset_seq = 2                               # nothing pending: the numbers stand
    _, s = lc.parse_stats(bytes(b), now_ticks=5000)
    assert (s.device_steps, s.device_hits, s.device_rule) == (288, 3, 1)


def test_history_row_and_snapshot_keep_the_claymore_numbers(tmp_path):
    b = _v5(tests=900, hits=200, plain_hits=150, stats_since=1000, dead_hits=7,
            device_steps=288, device_hits=3, device_rule=1)
    s = lc.parse_stats(bytes(b), now_ticks=61000)[1]
    settings = lc.Settings(False, 70, 300, 0)
    book = lc.HistoryBook(str(tmp_path / "h.json"))
    assert book.record(77, s, settings, now=1_700_000_000)
    book.save()
    row = json.loads((tmp_path / "h.json").read_text(encoding="utf-8"))[0]
    assert [row[k] for k in CLAYMORE_KEYS] == [288, 3, 1]
    assert row["dead_hits"] == 7 and "what_if" in row          # beside the version 4 keys

    logger = lc.EventLogger(str(tmp_path))
    line = logger.record_poll(77, s, settings, include_snapshot=True, now=1_700_000_000)[-1]
    assert line.split("] ", 1)[1].startswith("Snapshot (1m): 200 hits vs 150 plain (+33%)")
    snap = json.loads((tmp_path / "wolfrat_lag_comp_events.jsonl").read_text(
        encoding="utf-8").splitlines()[-1])
    assert snap["kind"] == "snapshot" and [snap[k] for k in CLAYMORE_KEYS] == [288, 3, 1]
    assert snap["dead_hits"] == 7 and snap["tests"] == 900


def test_a_version_4_snapshot_has_none_of_the_claymore_keys(tmp_path):
    s = _v4_stats()
    assert s.has_v4 and not s.has_v5
    logger = lc.EventLogger(str(tmp_path))
    logger.record_poll(5, s, lc.Settings(), include_snapshot=True, now=1_700_000_000)
    snap = json.loads((tmp_path / "wolfrat_lag_comp_events.jsonl").read_text(
        encoding="utf-8").splitlines()[-1])
    assert "dead_hits" in snap and "what_if" in snap             # version 4's own are there
    assert not any(k in snap for k in CLAYMORE_KEYS)
    book = lc.HistoryBook(str(tmp_path / "h.json"))
    book.record(5, s, lc.Settings(), now=1_700_000_000)
    assert "bots" in book.rows[0] and not any(k in book.rows[0] for k in CLAYMORE_KEYS)


# ------------------------------------- the event log across restarts (review B4)
TUNED = lc.Settings(False, 70, 400, 80, lc.TUNE_APPLY)


def _tuner_stats(minutes, since=0x10, first_tick=100000):
    s = lc.Stats(tests=10, since=since, has_tuner=True, tune_mode=lc.TUNE_APPLY, tune_value_ms=70)
    s.tune_log = [lc.TuneStep(m, first_tick + m * 60000, 70, 70, None, 100 + m, m)
                  for m in minutes]
    return s


def _tune_lines(tmp_path):
    rows = [json.loads(line) for line in (tmp_path / "wolfrat_lag_comp_events.jsonl").read_text(
        encoding="utf-8").splitlines()]
    return [(r["pid"], r["step"]["minute"]) for r in rows if r["kind"] == "tune_step"]


def test_a_wolfrat_restart_does_not_log_the_kept_decisions_again(tmp_path):
    """The helper keeps its last 64 decisions.  A WolfRAT that had just started
    logged every one of them again, stamped with the time it started."""
    first = lc.EventLogger(str(tmp_path))
    assert len(first.record_poll(999, _tuner_stats([1, 2, 3]), TUNED)) == 3
    # WolfRAT restarts.  The helper still holds minutes 1-3 and has decided a 4th.
    second = lc.EventLogger(str(tmp_path))
    added = second.record_poll(999, _tuner_stats([1, 2, 3, 4]), TUNED)
    assert len(added) == 1 and "Auto-tune min #4" in added[0]
    assert second.record_poll(999, _tuner_stats([1, 2, 3, 4]), TUNED) == []
    third = lc.EventLogger(str(tmp_path))                      # once more, nothing new
    assert third.record_poll(999, _tuner_stats([1, 2, 3, 4]), TUNED) == []
    log = (tmp_path / "wolfrat_lag_comp_events.log").read_text(encoding="utf-8")
    assert log.count("Auto-tune min #") == 4
    assert _tune_lines(tmp_path) == [(999, 1), (999, 2), (999, 3), (999, 4)]


def test_a_restart_still_logs_a_new_session_and_another_server(tmp_path):
    lc.EventLogger(str(tmp_path)).record_poll(999, _tuner_stats([1, 2]), TUNED)
    # The counters were started again: minute 1 once more, but a later decision.
    again = lc.EventLogger(str(tmp_path))
    assert len(again.record_poll(999, _tuner_stats([1], since=0x99, first_tick=900000),
                                 TUNED)) == 1
    # Another server process whose helper happens to hold the same minutes.
    other = lc.EventLogger(str(tmp_path))
    assert len(other.record_poll(555, _tuner_stats([1, 2]), TUNED)) == 2
    assert _tune_lines(tmp_path) == [(999, 1), (999, 2), (999, 1), (555, 1), (555, 2)]


def test_reading_the_log_back_survives_broken_lines_and_a_missing_file(tmp_path):
    assert lc.EventLogger(str(tmp_path))._logged_steps(999) == set()          # no file yet
    lc.EventLogger(str(tmp_path)).record_poll(999, _tuner_stats([1, 2, 3]), TUNED)
    with open(tmp_path / "wolfrat_lag_comp_events.jsonl", "ab") as fh:
        fh.write(b'{"kind": "tune_step", "pid": 999, "step": {"minu\n')       # cut short
        fh.write(b'\xff\xfe "tune_step" not json\n')
        fh.write(b'{"kind": "tune_step", "pid": 999}\n')                      # no step in it
    logger = lc.EventLogger(str(tmp_path))
    assert logger._logged_steps(999) == {(1, 160000), (2, 220000), (3, 280000)}
    assert logger.record_poll(999, _tuner_stats([1, 2, 3]), TUNED) == []
    # Only the end of a long file is read: a line the read starts inside is skipped.
    logger.TAIL_BYTES = 40
    assert logger._logged_steps(999) == set()


# ------------------------------------- a player who leaves and rejoins (review N5)
def _targets(**close_shots):
    """Stats with one target row per name; every number follows the close shots."""
    s = lc.Stats(tests=10, hits=2, since=0x10, has_targets=True,
                 bands=[lc.BandStats(label) for label in lc.BAND_LABELS])
    s.targets = [lc.TargetStats(i, name, 100, c, c // 5, c * 2, c // 2, c * 2, c // 4, c // 10, 0)
                 for i, (name, c) in enumerate(close_shots.items())]
    return s


def _row(book, name):
    return next(t for t in book.rows[0]["targets"] if t["name"] == name)


def test_history_adds_up_a_player_who_left_and_came_back(tmp_path):
    """The helper starts a rejoining player's row from zero.  The session row
    used to take the new, smaller numbers over the old ones."""
    settings = lc.Settings(False, 70, 400, 80)
    book = lc.HistoryBook(str(tmp_path / "h.json"))
    book.record(1, _targets(A=10, B=20), settings, now=1_000_000)
    book.record(1, _targets(A=12), settings, now=1_000_030)               # B left
    assert _row(book, "B")["close_low"] == 20
    book.record(1, _targets(A=15, B=3), settings, now=1_000_060)          # B is back, from zero
    assert _row(book, "B")["close_low"] == 23 and _row(book, "B")["close_lag"] == 46
    assert _row(book, "B")["hits_low"] == 20 // 5 + 3 // 5
    book.record(1, _targets(A=15, B=8), settings, now=1_000_090)          # and plays on
    assert _row(book, "B")["close_low"] == 28
    # Left and came back between two looks: seen by their numbers going down.
    book.record(1, _targets(A=16, B=2), settings, now=1_000_120)
    assert _row(book, "B")["close_low"] == 30
    book.record(1, _targets(A=16, B=2), settings, now=1_000_150)          # nothing new: no double count
    assert _row(book, "B")["close_low"] == 30
    assert _row(book, "A")["close_low"] == 16 and "earlier" not in _row(book, "A")
    assert len(book.rows) == 1 and len(book.rows[0]["targets"]) == 2

    book.save()                                                           # WolfRAT restarts
    again = lc.HistoryBook(str(tmp_path / "h.json"))
    again.record(1, _targets(A=16, B=5), settings, now=1_000_180)
    assert _row(again, "B")["close_low"] == 33


def test_history_rows_from_before_this_still_update(tmp_path):
    """A session row saved by an older WolfRAT has no 'away' or 'earlier' keys."""
    settings = lc.Settings(False, 70, 400, 80)
    old = {"key": "1-00000010", "started": "2026-11-19 20:00", "minutes": 5, "setting": "x",
           "hits": 2, "plain_hits": 1, "gained": 1, "lost": 0, "cal_total": 0, "cal_median": None,
           "bands": [], "targets": [dict(lc.TargetStats(0, "A", 100, 10, 2, 20, 5, 20, 2, 1, 0).__dict__),
                                    dict(lc.TargetStats(1, "Gone", 90, 7, 1, 0, 0, 0, 0, 0, 0).__dict__)]}
    (tmp_path / "h.json").write_text(json.dumps([old]), encoding="utf-8")
    book = lc.HistoryBook(str(tmp_path / "h.json"))
    book.record(1, _targets(A=14), settings, now=1_000_000)
    assert _row(book, "A")["close_low"] == 14 and _row(book, "Gone")["close_low"] == 7


# ------------------------------------------------- helper version 6 (2026-11-23)
# Auto-tune per player (tmp/autotune2/SPEC.md): one tune row of 88 bytes per
# slot after the version 5 block, which must not move.  WolfRAT writes each
# player's own Extra (want*), the helper takes it on (use*) and tests it.

V6_BYTES = 50980
TUNE_FIELDS = ("want_hash", "want_extra_ms", "want_seq", "use_extra_ms", "use_seq", "use_on",
               "use_entity", "center_ms", "steps", "center_hits", "down_wins", "down_losses",
               "up_wins", "up_losses", "what_if_hits")


def _v6(**kw):
    b = _v4(**kw)
    b.version, b.size = 6, V6_BYTES
    b.pt_spread_ms = 40
    return b


def _seat(b, slot, name, ping, entity=None, **tune):
    p = b.players[slot]
    p.entity, p.ping, p.name = entity or 0x100 + slot, ping, name
    for k, v in tune.items():
        if k == "what_if_hits":
            for i, n in enumerate(v):
                b.player_tune[slot].what_if_hits[i] = n
        else:
            setattr(b.player_tune[slot], k, v)


def test_version_6_block_layout_matches_the_spec():
    assert lc.VERSION == 6
    assert [lc._OFF[k] for k in ("pt_apply", "pt_spread_ms", "reserved6", "player_tune")] == [
        28964, 28968, 28972, 28980]
    assert lc._OFF["pt_apply"] == lc._V5_SIZE == 28964             # nothing before it moves
    assert lc.ctypes.sizeof(lc._PlayerTune) == 88
    assert [getattr(lc._PlayerTune, k).offset for k in TUNE_FIELDS] == [
        0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56]
    assert lc.SHARED_SIZE == lc._V6_SIZE == 28980 + 250 * 88 == V6_BYTES
    assert lc.ctypes.sizeof(lc._Shared) == 50980
    # Every older part where it was.
    assert (lc._V2_SIZE, lc._V3_SIZE, lc._V4_SIZE) == (26344, 28420, 28948)


def test_version_6_tune_rows_and_ptapply_are_read():
    b = _v6(stats_since=1000, pt_apply=1)
    _seat(b, 3, b"ez", 45, want_hash=lc.name_hash("ez"), want_extra_ms=50, want_seq=7,
          use_extra_ms=50, use_seq=7, use_on=1, use_entity=0x103, center_ms=50, steps=900,
          center_hits=300, down_wins=79, down_losses=39, up_wins=20, up_losses=31,
          what_if_hits=[1, 2, 3, 4, 5, 6, 7, 8])
    _seat(b, 9, b"A-99", 150, center_ms=70)
    b.player_tune[20].want_hash, b.player_tune[20].want_seq = 1234, 3     # left: still held
    b.player_tune[30].steps = 99                                         # empty, nothing held
    _, s = lc.parse_stats(bytes(b), now_ticks=2000)
    assert s.has_v6 and s.has_v5 and s.has_v4 and s.pt_apply == 1 and s.pt_spread_ms == 40
    assert [r.slot for r in s.player_tune] == [3, 9, 20]
    ez = s.player_tune[0]
    assert (ez.name, ez.ping, ez.present, ez.want_extra_ms, ez.use_on, ez.center_ms) == (
        "ez", 45, True, 50, 1, 50)
    assert ez.counters() == (900, 300, 79, 39, 20, 31) and ez.adopted
    assert ez.what_if_hits == [1, 2, 3, 4, 5, 6, 7, 8]
    left = s.player_tune[2]
    assert not left.present and left.name == "" and left.want_hash == 1234


def test_older_blocks_have_no_tune_rows():
    """Past the end of a version 5 helper's block is that helper's other memory,
    never want / use fields or counters."""
    b = _v6(stats_since=1000, pt_apply=1, device_steps=288, device_rule=1)
    _seat(b, 3, b"ez", 45, want_hash=99, steps=900, down_wins=5)
    b.version, b.size = 5, V5_BYTES
    for raw in (bytes(b)[:V5_BYTES], bytes(b)):
        _, s = lc.parse_stats(raw, now_ticks=2000)
        assert s.has_v5 and not s.has_v6 and s.device_steps == 288
        assert s.player_tune == [] and s.pt_apply == 0 and s.pt_spread_ms == 0
    # Says 6 but is not that big (or a short read): version 5.
    b.version, b.size = 6, V5_BYTES
    _, s = lc.parse_stats(bytes(b), now_ticks=2000)
    assert s.has_v5 and not s.has_v6 and s.player_tune == []
    b.size = V6_BYTES
    _, s = lc.parse_stats(bytes(b)[:V5_BYTES], now_ticks=2000)
    assert s.has_v5 and not s.has_v6
    # And a version 4 block is still version 4.
    b.version, b.size = 4, V4_BYTES
    _, s = lc.parse_stats(bytes(b), now_ticks=2000)
    assert s.has_v4 and not s.has_v5 and not s.has_v6


def test_a_pending_reset_clears_the_tune_counters_and_keeps_the_values():
    b = _v6(reset_seq=2, seen_reset_seq=1, stats_since=1000)
    _seat(b, 3, b"ez", 45, want_hash=5, want_extra_ms=50, want_seq=7, use_extra_ms=50,
          use_seq=7, use_on=1, use_entity=0x103, center_ms=50, steps=900, center_hits=300,
          down_wins=79, down_losses=39, up_wins=20, up_losses=31, what_if_hits=[9] * 8)
    block, s = lc.parse_stats(bytes(b), now_ticks=5000)
    ez = s.player_tune[0]
    assert ez.counters() == (0, 0, 0, 0, 0, 0) and ez.what_if_hits == [0] * 8
    assert (ez.want_hash, ez.want_extra_ms, ez.want_seq, ez.use_extra_ms, ez.use_seq, ez.use_on,
            ez.use_entity, ez.center_ms) == (5, 50, 7, 50, 7, 1, 0x103, 50)


def test_name_hash_is_fnv1a_of_the_name_bytes(monkeypatch):
    # The FNV-1a 32 test vectors.
    assert lc.fnv1a32(b"") == 0x811C9DC5
    assert lc.fnv1a32(b"a") == 0xE40C292C
    assert lc.fnv1a32(b"foobar") == 0xBF9CF968
    assert lc.name_hash("foobar") == lc.name_hash(b"foobar") == 0xBF9CF968
    # As the helper holds it: up to the first NUL, at most 31 bytes, latin-1.
    assert lc.name_hash(b"foobar\0junk") == 0xBF9CF968
    long = "x" * 40
    assert lc.name_hash(long) == lc.fnv1a32(b"x" * 31)
    assert lc.name_hash("René") == lc.fnv1a32(b"Ren\xe9")
    # A name read from a block hashes to the same as the bytes the helper hashes.
    b = _v6()
    _seat(b, 0, b"\xc6sir 2", 50)
    _, s = lc.parse_stats(bytes(b), now_ticks=1)
    assert lc.name_hash(s.player_tune[0].name) == lc.fnv1a32(b"\xc6sir 2")
    # 0 means "no own value": a name that hashes to 0 gets 1.
    monkeypatch.setattr(lc, "fnv1a32", lambda data: 0)
    assert lc.name_hash("anyone") == 1


def test_personal_extra_is_the_helpers_rule():
    g = 70
    assert lc.personal_extra(50, g, 45) == 50                  # below the server Extra: as it is
    assert lc.personal_extra(110, g, 60) == 110                # +40, 60 + 110 = 170
    assert lc.personal_extra(130, g, 60) == 110                # at most the server Extra + 40
    assert lc.personal_extra(110, g, 160) == 90                # 250 ms of ping + Extra
    assert lc.personal_extra(110, g, 200) == 70                # past that already: no increase
    assert lc.personal_extra(-20, g, 200) == -20               # decreases always allowed
    assert lc.personal_extra(-50, g, 35) == -35                # down to no rewind at all
    assert lc.personal_extra(-500, g, 0) == 0


class _Recorder:
    def __init__(self):
        self.writes = []

    def write(self, address, data):
        self.writes.append((address, bytes(data)))


def test_the_writer_puts_the_value_first_then_the_hash_then_the_number():
    mem = _Recorder()
    row = 0x5000 + 28980 + 7 * 88
    lc.write_want(mem, 0x5000, 7, 0xDEADBEEF, -35, 12)
    assert mem.writes == [(row + 4, struct.pack("<i", -35)),
                          (row + 0, struct.pack("<I", 0xDEADBEEF)),
                          (row + 8, struct.pack("<I", 12))]
    assert mem.writes[0][1] == b"\xdd\xff\xff\xff"            # little-endian
    lc.write_pt_apply(mem, 0x5000, True)
    lc.write_pt_apply(mem, 0x5000, False)
    assert mem.writes[3:] == [(0x5000 + 28964, b"\x01\0\0\0"), (0x5000 + 28964, b"\0\0\0\0")]
    with pytest.raises(ValueError):
        lc.write_want(mem, 0x5000, 250, 1, 2, 3)
    assert lc.next_seq(0) == 1 and lc.next_seq(7) == 8 and lc.next_seq(0xFFFFFFFF) == 1


def _on(b):
    _, s = lc.parse_stats(bytes(b), now_ticks=2000)
    return lc.State(lc.ON, shared=0x5000, ready=True, settings=lc.Settings(extra_ms=70), stats=s)


def test_sync_writes_only_what_differs_and_bumps_the_number():
    b = _v6()
    h_ez, h_a = lc.name_hash("ez"), lc.name_hash("A-99")
    _seat(b, 3, b"ez", 45, want_hash=h_ez, want_extra_ms=50, want_seq=7)     # as wanted
    _seat(b, 9, b"A-99", 120, want_hash=h_a, want_extra_ms=90, want_seq=0xFFFFFFFF)
    _seat(b, 12, b"New", 80)                                                   # nothing yet
    b.player_tune[20].want_hash, b.player_tune[20].want_extra_ms = 555, 30     # left
    b.player_tune[20].want_seq = 4
    st = _on(b)
    wants = {3: (h_ez, 50), 9: (h_a, 110), 12: (lc.name_hash("New"), 70)}
    assert lc.tune_changes(st.stats, wants, True) == (True, [
        (9, h_a, 110, 1), (12, lc.name_hash("New"), 70, 1), (20, 0, 30, 5)])
    mem = _Recorder()
    assert lc.sync_player_tune(mem, st, wants, True) == [9, 12, 20]
    assert mem.writes[0] == (0x5000 + 28964, b"\x01\0\0\0")                 # ptApply first
    row20 = 0x5000 + 28980 + 20 * 88
    assert mem.writes[-3:] == [(row20 + 4, struct.pack("<i", 30)), (row20, b"\0\0\0\0"),
                               (row20 + 8, struct.pack("<I", 5))]           # cleared: hash 0
    assert len(mem.writes) == 1 + 3 * 3
    # Written: nothing more to do (the rows were brought up to date as well).
    assert lc.tune_changes(st.stats, wants, True) == (False, [])
    assert lc.sync_player_tune(mem, st, wants, True) == [] and len(mem.writes) == 10


def test_sync_leaves_older_blocks_and_other_helpers_alone():
    mem = _Recorder()
    b = _v6()
    _seat(b, 3, b"ez", 45)
    wants = {3: (lc.name_hash("ez"), 50)}
    st = _on(b)
    for kind in (lc.OLD, lc.STOCK, lc.PARTIAL):
        assert lc.sync_player_tune(mem, lc.State(kind, shared=0x5000, stats=st.stats),
                                   wants, True) == []
    b.version, b.size = 5, V5_BYTES
    assert lc.sync_player_tune(mem, _on(b), wants, True) == []
    assert lc.sync_player_tune(mem, None, wants, True) == [] and mem.writes == []


def test_the_old_server_wide_auto_tune_is_always_written_off():
    """Retired (2026-11-23): whatever the page's Auto-tune says, the helper's
    own tuneMode is 0.  The page's setting is the tuner per player now."""
    class Proc:
        def __init__(self, version, size):
            self.head = struct.pack("<II", version, size)
            self.writes = {}

        def read(self, address, size):
            assert (address, size) == (0x5000 + 4, 8)
            return self.head

        def write(self, address, data):
            self.writes[address - 0x5000] = bytes(data)

    for mode in (lc.TUNE_OFF, lc.TUNE_WATCH, lc.TUNE_APPLY):
        proc = Proc(5, V5_BYTES)
        lc._write_settings(proc, 0x5000, lc.Settings(False, 70, 300, 0, mode), True)
        assert proc.writes[lc._OFF["tune_mode"]] == b"\0\0\0\0"
        assert proc.writes[lc._OFF["enabled"]] == b"\x01\0\0\0"
        assert struct.unpack("<iiii", proc.writes[lc._OFF["observe"]]) == (0, 70, 300, 0)
    proc = Proc(2, lc._V2_SIZE)                                  # no tuner in it: never written
    lc._write_settings(proc, 0x5000, lc.Settings(tune_mode=lc.TUNE_APPLY), True)
    assert lc._OFF["tune_mode"] not in proc.writes


def test_describe_setting_says_the_auto_tune_per_player():
    assert lc.describe_setting(lc.Settings(False, 70, 300, 0, lc.TUNE_WATCH)) == \
        "ping +70 ms (auto-tune per player: watch), none under 0, max 300"
    assert lc.describe_setting(lc.Settings(False, 70, 300, 0, lc.TUNE_APPLY)) == \
        "ping +70 ms (auto-tune per player: on), none under 0, max 300"
    assert lc.describe_setting(lc.Settings(True, 70, 300, 0, lc.TUNE_OFF)) == \
        "watch only, ping +70 ms, none under 0, max 300"
    # An older WolfRAT's server-wide auto-tune still holding its own value.
    assert lc.describe_setting(lc.Settings(False, 70, 300, 0), 65) == \
        "ping +65 ms (server-wide auto from +70), none under 0, max 300"
    assert lc.describe_setting(lc.Settings(False, 70, 300, 0), 70) == \
        "ping +70 ms, none under 0, max 300"


def test_history_row_and_snapshot_keep_the_tune_rows_and_the_tuner(tmp_path):
    b = _v6(tests=900, hits=200, plain_hits=150, stats_since=1000, pt_apply=0)
    _seat(b, 3, b"ez", 45, want_hash=lc.name_hash("ez"), want_extra_ms=50, want_seq=2,
          center_ms=50, steps=900, center_hits=300, down_wins=79, down_losses=39,
          up_wins=20, up_losses=31, what_if_hits=[1, 2, 3, 4, 5, 6, 7, 8])
    s = lc.parse_stats(bytes(b), now_ticks=61000)[1]
    settings = lc.Settings(False, 70, 300, 0, lc.TUNE_WATCH)
    tuned = {"mode": "watch", "server_extra": 70, "differ": {"ez": 50}, "locked": [],
             "top_limit": [], "moves": []}
    book = lc.HistoryBook(str(tmp_path / "h.json"))
    assert book.record(77, s, settings, now=1_700_000_000, player_tune=tuned)
    book.save()
    row = json.loads((tmp_path / "h.json").read_text(encoding="utf-8"))[0]
    assert row["setting"] == "ping +70 ms (auto-tune per player: watch), none under 0, max 300"
    assert row["auto_tune"] == tuned and row["pt_apply"] == 0
    assert row["player_tune"] == [{"slot": 3, "name": "ez", "ping": 45, "want": 50, "use": None,
                                   "center": 50, "steps": 900, "center_hits": 300,
                                   "down": [79, 39], "up": [20, 31],
                                   "what_if": [1, 2, 3, 4, 5, 6, 7, 8]}]
    logger = lc.EventLogger(str(tmp_path))
    line = logger.record_poll(77, s, settings, include_snapshot=True, now=1_700_000_000)[-1]
    assert "active Extra +70 ms [per player: watch]" in line
    snap = json.loads((tmp_path / "wolfrat_lag_comp_events.jsonl").read_text(
        encoding="utf-8").splitlines()[-1])
    assert snap["player_tune"][0]["down"] == [79, 39] and snap["player_tune_mode"] == "watch"
    # A version 5 helper: none of the new keys.
    b.version, b.size = 5, V5_BYTES
    s5 = lc.parse_stats(bytes(b), now_ticks=61000)[1]
    book5 = lc.HistoryBook(str(tmp_path / "h5.json"))
    book5.record(5, s5, settings, now=1_700_000_000)
    assert "player_tune" not in book5.rows[0] and "pt_apply" not in book5.rows[0]
    assert "auto_tune" not in book5.rows[0]


def test_the_helpers_minutes_with_its_own_auto_tune_off_are_not_logged(tmp_path):
    """The helper notes a minute even with its server-wide auto-tune off, and
    WolfRAT keeps that off now: those minutes decided nothing."""
    logger = lc.EventLogger(str(tmp_path))
    off = _tuner_stats([1, 2, 3])
    off.tune_mode = lc.TUNE_OFF
    assert logger.record_poll(999, off, TUNED) == []
    # An older WolfRAT's helper with it still on is logged as before - from its
    # next minute, not again for the ones already passed.
    on = _tuner_stats([1, 2, 3, 4])
    added = logger.record_poll(999, on, TUNED)
    assert len(added) == 1 and "Auto-tune min #4 [apply]" in added[0]
