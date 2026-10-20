"""The Bans tab, driven offscreen: ban from the online table, punt on sight with
cooldown, rejoin caught by IP, whitelist wins, firewall copy, save + reload,
name bans still work when IPs cannot be read (2026-09-22)."""
import os
import sys

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication

from wolfrat import ban_rules as br
from wolfrat.bans_tab import BansTab
from wolfrat.jo_players import SlotInfo

_app = QApplication.instance() or QApplication(sys.argv[:1])
NOW = 1_800_000_000.0


class FakeReader:
    def __init__(self):
        self.slots, self.status, self.available = [], "fake", True

    def read(self):
        return self.slots


class Rig:
    def __init__(self, folder):
        self.now = NOW
        self.punts, self.said, self.logged, self.punted_records = [], [], [], []
        self.reader = FakeReader()
        self.folder = str(folder)
        self.tab = self.build()

    def build(self):
        return BansTab(self.folder,
                       punt=lambda p, why: (self.punts.append((p["name"], why)), self.punted_records.append(p)),
                       announce=self.said.append, log=self.logged.append, reader=self.reader,
                       clock=lambda: self.now, admin_name="Dale")

    def poll(self, *players, seconds=5):
        self.now += seconds
        self.reader.slots = [SlotInfo(0, "Host", "")] + [SlotInfo(i + 1, n, ip) for i, (n, ip) in enumerate(players)]
        self.tab.on_players([{"id": str(i + 1), "name": n, "team": "1", "class": "R",
                              "kills": "0", "deaths": "0", "ping": "20"}
                             for i, (n, _ip) in enumerate(players)])


def test_ban_from_the_online_table_then_punt_on_sight_with_cooldown(tmp_path):
    rig = Rig(tmp_path)
    rig.poll(("Dale", "82.68.58.92"), ("Troll", "5.6.7.8"))
    assert rig.punts == [] and rig.tab.online_table.item(1, 1).text() == "5.6.7.8"
    rig.tab.online_table.selectRow(1)
    rig.tab.online_reason.setText("griefing")
    rig.tab.online_expiry.setCurrentIndex(3)          # 7 days
    rig.tab._ban_selected("both")
    assert [(e.kind, e.value) for e in rig.tab.bans.entries] == [("name", "Troll"), ("ip", "5.6.7.8")]
    assert rig.tab.bans.entries[0].expires_at == rig.now + 7 * 86400
    assert rig.punts == [("Troll", "Banned: griefing")]
    rig.punts.clear()
    rig.poll(("Dale", "82.68.58.92"), ("Troll", "5.6.7.8"))            # 5 s on, still leaving: no double punt
    assert rig.punts == []
    rig.poll(("Dale", "82.68.58.92"))                                  # gone - the punt worked
    rig.poll(("Dale", "82.68.58.92"), ("Troll", "5.6.7.8"))            # back: out again straight away
    assert rig.punts == [("Troll", "Banned: griefing")] and rig.said == ["Troll removed - banned: griefing"]
    rig.poll(("Dale", "82.68.58.92"), ("Troll", "5.6.7.8"))            # still there 5 s later: cooldown holds
    assert len(rig.punts) == 1
    assert rig.tab.bans.entries[0].hits == 1 and rig.tab.bans.entries[0].last_hit_ip == "5.6.7.8"


def test_rejoin_under_a_new_name_is_caught_by_ip_and_shown_as_an_alias(tmp_path):
    rig = Rig(tmp_path)
    rig.poll(("Troll", "5.6.7.8"))
    rig.tab.ban_now("Troll", reason="griefing", kind="both")
    rig.punts.clear()
    rig.poll(("NewName", "5.6.7.8"), seconds=60)
    assert rig.punts == [("NewName", "Banned: griefing")]
    assert rig.tab.online_table.item(0, 5).text() == "Troll"


def test_whitelist_beats_a_range_and_the_tab_says_so_in_its_log(tmp_path):
    rig = Rig(tmp_path)
    rig.tab.add_value.setText("5.6.*"); rig.tab.add_reason.setText("bad range"); rig.tab._add_typed()
    rig.tab.white_value.setText("5.6.7.8"); rig.tab.white_note.setText("Ham's home"); rig.tab._add_white()
    rig.poll(("Ham", "5.6.7.8"), ("Other", "5.6.9.9"))
    assert rig.punts == [("Other", "Banned: bad range")]
    assert any("Whitelisted ip 5.6.7.8" in line for line in rig.logged)


def test_firewall_copy_needs_an_ip_row_and_puts_the_powershell_line_on_the_clipboard(tmp_path):
    rig = Rig(tmp_path)
    rig.tab.add_value.setText("Troll"); rig.tab._add_typed()
    rig.tab.add_value.setText("82.68.*"); rig.tab._add_typed()
    rows = {rig.tab.ban_table.item(r, 0).text(): r for r in range(rig.tab.ban_table.rowCount())}
    rig.tab.ban_table.selectRow(rows["name"])
    assert not rig.tab.copy_block_btn.isEnabled()
    rig.tab.ban_table.selectRow(rows["ip"])
    assert rig.tab.copy_block_btn.isEnabled()
    rig.tab._copy_firewall(True)
    assert _app.clipboard().text() == br.firewall_block_command("82.68.*")
    rig.tab._copy_firewall(False)
    assert _app.clipboard().text() == br.firewall_unblock_command("82.68.*")


def test_bad_typed_value_is_refused_with_the_reason(tmp_path):
    rig = Rig(tmp_path)
    rig.tab.add_value.setText("999.1.1.1"); rig.tab._add_typed()
    assert rig.tab.bans.entries == [] and rig.logged[-1] == "Not added: not a valid IP address"


def test_chat_and_history_survive_a_restart_and_unban_works(tmp_path):
    rig = Rig(tmp_path)
    rig.poll(("Troll", "5.6.7.8"))
    rig.tab.on_chat([{"id": 1, "text": "Troll: old"}])                      # first batch skipped
    rig.tab.on_chat([{"id": 1, "text": "Troll: old"}, {"id": 2, "text": "Troll: u all suck"}])
    rig.tab.ban_now("Troll", reason="mouth", kind="name", added_by="Ham")
    again = rig.build()
    assert [(e.value, e.reason, e.added_by) for e in again.bans.entries] == [("Troll", "mouth", "Ham")]
    assert list(again.history.get("Troll").chat) == [[rig.now, "u all suck"]]
    assert again.history.get("troll").ips == {"5.6.7.8": [1, rig.now]}
    assert again.unban("troll") == 1 and again.unban("troll") == 0


def test_without_the_server_on_this_pc_name_bans_still_work_and_status_says_why(tmp_path):
    rig = Rig(tmp_path)
    rig.reader.available = False
    rig.reader.status = "jointops.exe is not running on this PC - IPs need the server on the same machine as WolfRAT."
    rig.tab.bans.add(br.BanEntry("name", "Troll", added_at=NOW))
    rig.now += 5
    rig.reader.slots = []
    rig.tab.on_players([{"id": "9", "name": "troll"}])
    assert rig.punts == [("troll", "Banned: Troll")]
    assert rig.tab.status_lbl.text().startswith("IPs: jointops.exe is not running on this PC")
    assert rig.tab.ban_now("troll", kind="ip") == [] and "cannot IP-ban" in rig.logged[-1]


def test_expired_entries_drop_off_in_housekeeping(tmp_path):
    rig = Rig(tmp_path)
    rig.tab.bans.add(br.BanEntry("name", "Troll", added_at=NOW, expires_at=NOW + 10))
    rig.now = NOW + 11
    rig.tab._housekeeping()
    assert rig.tab.bans.entries == [] and "expired" in rig.logged[-1]


def test_punt_on_sight_hands_over_the_whole_player_record(tmp_path):
    """Live 2026-09-22: a cut-down {id, name} record made the dispatcher refuse with
    "displayed player is missing 'team'" and a banned player stayed on."""
    rig = Rig(tmp_path)
    rig.tab.bans.add(br.BanEntry("name", "Troll", added_at=NOW))
    rig.poll(("Troll", "5.6.7.8"))
    assert rig.punts == [("Troll", "Banned: Troll")]
    assert rig.punted_records[-1] == {"id": "1", "name": "Troll", "team": "1", "class": "R",
                                      "kills": "0", "deaths": "0", "ping": "20"}


def test_pages_scroll_instead_of_squashing(tmp_path):
    from PyQt6.QtWidgets import QScrollArea
    rig = Rig(tmp_path)
    assert [rig.tab.pages.tabText(i) for i in range(rig.tab.pages.count())] ==         ["Ban list", "Whitelist", "Player history", "Connection checks", "Idle kick"]
    assert all(isinstance(rig.tab.pages.widget(i), QScrollArea) for i in range(5))
    # a 1024x768 desktop leaves the tab about 560 px: every page must fit without the page scrolling
    rig.tab.resize(1000, 560); rig.tab.show(); _app.processEvents()
    for i in range(rig.tab.pages.count()):
        rig.tab.pages.setCurrentIndex(i); _app.processEvents()
        scroll = rig.tab.pages.widget(i)
        assert scroll.widget().sizeHint().height() <= scroll.viewport().height(), rig.tab.pages.tabText(i)


def test_name_and_ip_rows_say_what_they_were_banned_with(tmp_path):
    """Dale, live 2026-09-22: a name row gave no clue which IP went with it."""
    rig = Rig(tmp_path)
    rig.poll(("Troll", "5.6.7.8"))
    rig.tab.ban_now("Troll", reason="griefing", kind="both")
    by_kind = {e.kind: e for e in rig.tab.bans.entries}
    assert by_kind["name"].linked == "5.6.7.8" and by_kind["ip"].linked == "Troll"
    headers = [rig.tab.ban_table.horizontalHeaderItem(c).text() for c in range(rig.tab.ban_table.columnCount())]
    col = headers.index("Banned with")
    shown = {rig.tab.ban_table.item(r, 1).text(): rig.tab.ban_table.item(r, col).text()
             for r in range(rig.tab.ban_table.rowCount())}
    assert shown == {"Troll": "5.6.7.8", "5.6.7.8": "Troll"}
    again = rig.build()
    assert {e.kind: e.linked for e in again.bans.entries} == {"name": "5.6.7.8", "ip": "Troll"}


def test_remove_button_sits_above_the_table_and_removes_the_selected_row(tmp_path):
    """Dale, live 2026-09-22: the buttons under the table scrolled out of sight."""
    rig = Rig(tmp_path)
    rig.tab.add_value.setText("Troll"); rig.tab._add_typed()
    rig.tab.resize(1000, 600); rig.tab.show(); _app.processEvents()
    assert rig.tab.remove_btn.text() == "Remove from list"
    assert rig.tab.remove_btn.mapTo(rig.tab, rig.tab.remove_btn.rect().topLeft()).y() \
        < rig.tab.ban_table.mapTo(rig.tab, rig.tab.ban_table.rect().topLeft()).y()
    rig.tab.ban_table.selectRow(0)
    assert rig.tab.remove_btn.isEnabled()
    rig.tab._remove_selected()
    assert rig.tab.bans.entries == [] and rig.tab.ban_table.rowCount() == 0


# ---- connection checks -------------------------------------------------------------

from wolfrat import ip_checks as ic


class FakeChecker:
    def __init__(self):
        self.submitted = []

    def submit(self, ip, provider, key):
        if any(ip == done[0] for done in self.submitted):      # the real one dedupes pending lookups too
            return False
        self.submitted.append((ip, provider, key)); return True

    def pending(self):
        return len(self.submitted)


def checks_rig(tmp_path):
    rig = Rig(tmp_path)
    rig.checker = FakeChecker()
    rig.tab = BansTab(rig.folder, punt=lambda p, why: (rig.punts.append((p["name"], why)), rig.punted_records.append(p)),
                      announce=rig.said.append, log=rig.logged.append, reader=rig.reader,
                      clock=lambda: rig.now, admin_name="Dale", checker=rig.checker)
    return rig


def test_checks_off_by_default_and_nothing_is_looked_up(tmp_path):
    rig = checks_rig(tmp_path)
    assert not rig.tab.checks.enabled and rig.tab.checks_status.text() == "Off."
    rig.poll(("Troll", "5.6.7.8"))
    assert rig.checker.submitted == [] and rig.tab.online_table.item(0, 2).text() == "-"


def test_vpn_is_kicked_once_per_visit_and_the_verdict_shows_in_the_table(tmp_path):
    rig = checks_rig(tmp_path)
    rig.tab.checks_cb.setChecked(True)
    rig.poll(("Troll", "5.6.7.8"), ("Dale", "82.68.58.92"))
    assert sorted(x[0] for x in rig.checker.submitted) == ["5.6.7.8", "82.68.58.92"]
    assert rig.tab.online_table.item(0, 2).text() == "checking..."
    rig.tab._on_verdict(ic.Verdict("82.68.58.92", rig.now, country="GB", provider="Zen"))
    rig.tab._on_verdict(ic.Verdict("5.6.7.8", rig.now, proxy=True, kind="VPN", country="DE", provider="NordVPN"))
    assert rig.punts == [("Troll", "Kicked: VPN connection (NordVPN)")]
    assert rig.said == ["Troll removed - VPN connection (NordVPN)"]
    assert rig.tab.online_table.item(0, 2).text() == "VPN (DE, NordVPN)"
    assert rig.tab.online_table.item(1, 2).text() == "clear (GB, Zen)"
    rig.poll(("Troll", "5.6.7.8"), ("Dale", "82.68.58.92"))          # still there 5 s later: no repeat
    assert len(rig.punts) == 1
    rig.poll(("Dale", "82.68.58.92"))                                  # gone
    rig.poll(("Troll", "5.6.7.8"), ("Dale", "82.68.58.92"))          # back: kicked again, from the cache
    assert len(rig.punts) == 2 and len(rig.checker.submitted) == 2


def test_ban_action_puts_the_ip_on_the_list_and_whitelist_still_wins(tmp_path):
    rig = checks_rig(tmp_path)
    rig.tab.checks_cb.setChecked(True); rig.tab.proxy_action.setCurrentIndex(ic.ACTION_BAN)
    rig.tab.white_value.setText("Regular"); rig.tab._add_white()
    rig.poll(("Troll", "5.6.7.8"), ("Regular", "5.6.7.9"))
    rig.tab._on_verdict(ic.Verdict("5.6.7.8", rig.now, proxy=True, kind="VPN"))
    rig.tab._on_verdict(ic.Verdict("5.6.7.9", rig.now, proxy=True, kind="VPN"))
    entries = [(e.kind, e.value, e.added_by, e.linked) for e in rig.tab.bans.entries]
    assert entries == [("ip", "5.6.7.8", "connection check", "Troll")]
    assert rig.punts == [("Troll", "Banned: connection check: VPN connection")]   # the ban list removed him
    assert all(name != "Regular" for name, _ in rig.punts)


def test_country_block_and_settings_survive_a_restart(tmp_path):
    rig = checks_rig(tmp_path)
    rig.tab.checks_cb.setChecked(True)
    rig.tab.country_mode.setCurrentIndex(1); rig.tab.countries_edit.setText("ru, cn"); rig.tab.countries_edit.editingFinished.emit()
    rig.tab.provider_combo.setCurrentIndex(1); rig.tab.key_edit.setText("k"); rig.tab.key_edit.editingFinished.emit()
    rig.poll(("Bot", "5.6.7.8"))
    assert rig.checker.submitted[-1] == ("5.6.7.8", "proxycheck", "k")
    rig.tab._on_verdict(ic.Verdict("5.6.7.8", rig.now, country="RU", country_name="Russia"))
    assert rig.punts == [("Bot", "Kicked: country Russia is blocked")]
    again = BansTab(rig.folder, punt=lambda *a: None, announce=lambda *a: None, reader=rig.reader,
                    clock=lambda: rig.now, checker=FakeChecker())
    assert again.checks.enabled and again.checks.countries == ["RU", "CN"] and again.checks.provider == "proxycheck"
    assert again.checks.api_key == "k" and again.verdicts.get("5.6.7.8", rig.now).country == "RU"
    assert not again.key_edit.isHidden() and again.countries_edit.isEnabled()


def test_lookup_errors_never_kick(tmp_path):
    rig = checks_rig(tmp_path)
    rig.tab.checks_cb.setChecked(True)
    rig.poll(("Troll", "5.6.7.8"))
    rig.tab._on_verdict(ic.Verdict("5.6.7.8", rig.now, proxy=True, error="HTTP 429 (rate limit)"))
    assert rig.punts == [] and rig.tab.online_table.item(0, 2).text() == "check failed: HTTP 429 (rate limit)"


def test_country_letters_sit_in_front_of_the_ip_once_known(tmp_path):
    rig = checks_rig(tmp_path)
    rig.tab.checks_cb.setChecked(True)
    rig.poll(("Dale", "82.68.58.92"))
    rig.tab.ban_now("Dale", kind="ip")
    assert rig.tab.online_table.item(0, 1).text() == "82.68.58.92"
    rig.tab._on_verdict(ic.Verdict("82.68.58.92", rig.now, country="GB", provider="Zen"))
    assert rig.tab.online_table.item(0, 1).text() == "GB 82.68.58.92"
    assert rig.tab.ban_table.item(0, 1).text() == "GB 82.68.58.92"
    rig.tab.pages.setCurrentIndex(2); rig.tab._refresh_history()
    assert rig.tab.hist_table.item(0, 1).text() == "GB 82.68.58.92"


# ---- idle kick ------------------------------------------------------------------

def idle_rig(tmp_path, mods=("Ham",)):
    rig = Rig(tmp_path)
    rig.tab = BansTab(rig.folder, punt=lambda p, why: (rig.punts.append((p["name"], why)), rig.punted_records.append(p)),
                      announce=rig.said.append, log=rig.logged.append, reader=rig.reader,
                      clock=lambda: rig.now, admin_name="Dale", checker=FakeChecker(), mods=lambda: mods)
    return rig


def poll_pos(rig, *players, seconds=5):
    rig.now += seconds
    rig.reader.slots = [SlotInfo(0, "Host", "", (1, 1, 1))] + [SlotInfo(i + 1, n, ip, pos) for i, (n, ip, pos) in enumerate(players)]
    rig.tab.on_players([{"id": str(i + 1), "name": n, "team": "1", "class": "R", "kills": "0", "deaths": "0", "ping": "20"}
                        for i, (n, _ip, _pos) in enumerate(players)])


def test_idle_page_exists_off_by_default_and_the_column_counts(tmp_path):
    rig = idle_rig(tmp_path)
    assert rig.tab.pages.tabText(4) == "Idle kick" and not rig.tab.idle_cfg.enabled
    poll_pos(rig, ("Camper", "5.6.7.8", (100, 100, 5)))
    poll_pos(rig, ("Camper", "5.6.7.8", (100, 100, 5)), seconds=90)
    assert rig.tab.online_table.item(0, 3).text() == "1:30" and rig.punts == []


def test_idle_warns_then_kicks_but_never_mods_or_whitelist(tmp_path):
    rig = idle_rig(tmp_path, mods=("Ham",))
    rig.tab.idle_cb.setChecked(True); rig.tab.idle_minutes.setValue(2)
    rig.tab.white_value.setText("Regular"); rig.tab._add_white()
    still = lambda n: (n, "5.6.7.8", (100, 100, 5))
    poll_pos(rig, still("Camper"), still("Ham"), still("Regular"))
    poll_pos(rig, still("Camper"), still("Ham"), still("Regular"), seconds=61)
    assert rig.said == ["Camper: you have not moved for 1 min", "Move now or you will be kicked in 60 seconds"] and rig.punts == []
    poll_pos(rig, still("Camper"), still("Ham"), still("Regular"), seconds=60)
    assert rig.punts == [("Camper", "Idle for 2 min")]
    assert rig.said[-1] == "Camper kicked - idle 2 min"
    assert all(n == "Camper" for n, _ in rig.punts)
    rig.tab.idle_mods_cb.setChecked(False)
    poll_pos(rig, still("Ham"), seconds=5)
    assert ("Ham", "Idle for 2 min") in rig.punts               # mods only exempt while the box is ticked


def test_map_change_resets_idle_clocks_and_settings_persist(tmp_path):
    rig = idle_rig(tmp_path)
    rig.tab.idle_cb.setChecked(True); rig.tab.idle_minutes.setValue(3); rig.tab.idle_mods_cb.setChecked(False)
    poll_pos(rig, ("Camper", "5.6.7.8", (100, 100, 5)))
    poll_pos(rig, ("Camper", "5.6.7.8", (100, 100, 5)), seconds=100)
    assert rig.tab.idle.idle_seconds("Camper", rig.now) == 100
    rig.tab.on_missions_updated(["3: TD-A.bms - () () () <CURRENT MISSION> <>"])
    assert rig.tab.idle.idle_seconds("Camper", rig.now) is None
    rig.tab.on_missions_updated(["3: TD-A.bms - () () () <CURRENT MISSION> <>"])   # same map again: no reset spam
    again = BansTab(rig.folder, punt=lambda *a: None, announce=lambda *a: None, reader=rig.reader,
                    clock=lambda: rig.now, checker=FakeChecker())
    assert (again.idle_cfg.enabled, again.idle_cfg.minutes, again.idle_cfg.exempt_mods) == (True, 3, False)


# ---- AAS zones -------------------------------------------------------------------

def test_zone_flip_is_reported_to_the_sprees_hook_and_zones_left_feeds_the_vote(tmp_path):
    from wolfrat.jo_players import ZoneInfo
    rig = idle_rig(tmp_path)
    got = []
    rig.tab.zone_capture = got.append
    start = [ZoneInfo(3, 0, (-16900178, 52104356, 0)), ZoneInfo(4, 2, (-18569420, 56731684, 0)),
             ZoneInfo(5, 2, (-12567544, 54416764, 0)), ZoneInfo(2, 1, (-18556912, 47465764, 0)),
             ZoneInfo(1, 1, (-24908850, 52048208, 0))]
    rig.reader.read_zones = lambda: start
    rig.reader.slots = [SlotInfo(1, "Dale", "82.68.58.92", (-16900178 + 100000, 52104356, 0))]
    rig.tab.on_players([{"id": "1", "name": "Dale", "team": "2"}])
    assert rig.tab.zones_left() == (1, 3) and got == []
    charlie = [ZoneInfo(z.tier, 2 if z.tier == 3 else z.team, z.pos) for z in start]
    rig.reader.read_zones = lambda: charlie
    rig.now += 5; rig.tab.on_players([{"id": "1", "name": "Dale", "team": "2"}])
    assert len(got) == 2 and (got[0].team, got[0].name, got[0].player, got[0].first) == (2, "Charlie", "Dale", True)
    assert type(got[1]).__name__ == "LeadEvent" and (got[1].team, got[1].owned, got[1].total) == (2, 3, 5)
    assert rig.tab.zones_left() == (2, 2)
    assert any("Rebels took Charlie (Dale) - first zone of the map" in line for line in rig.logged)
    assert any("Rebels now lead 3-2" in line for line in rig.logged)
    rig.tab.on_missions_updated(["3: AS-Other.bms - () () () <CURRENT MISSION> <>"])
    rig.now += 5; rig.tab.on_players([{"id": "1", "name": "Dale", "team": "2"}])   # after a map change: first sight again
    assert len(got) == 2


# ---- saving while a file is busy (JustAPlayer's crash, 2026-10-20) ----------
# WinError 32 at start-up: the checks page saved while the tab was still being built,
# the save failed (file held by OneDrive / antivirus / a second WolfRAT) and logging
# the failure hit the not-yet-made log box -> AttributeError, WolfRAT never opened.

def test_start_up_does_not_write_and_survives_a_locked_file(tmp_path):
    import json
    from wolfrat import bans_tab as bt
    checks = tmp_path / bt.CHECKS_FILE
    checks.write_text(json.dumps({"config": {"enabled": True}}), encoding="utf-8")
    before = checks.stat().st_mtime_ns
    with open(checks, "r", encoding="utf-8"):          # held open = os.replace onto it is refused
        rig = Rig(tmp_path)
    assert rig.tab.checks.enabled
    assert checks.stat().st_mtime_ns == before
    assert not any(name.endswith(".json") and name != bt.CHECKS_FILE for name in os.listdir(tmp_path))
    assert rig.logged == []


def test_busy_file_is_retried_then_saved(tmp_path, monkeypatch):
    from wolfrat import bans_tab as bt
    rig = Rig(tmp_path)
    real, calls = os.replace, []

    def busy_twice(src, dst):
        calls.append(dst)
        if len(calls) <= 2:
            raise PermissionError(13, "The process cannot access the file", None, 32)
        return real(src, dst)

    monkeypatch.setattr(bt.os, "replace", busy_twice)
    monkeypatch.setattr(bt.time, "sleep", lambda _s: None)
    rig.tab.checks_cb.setChecked(not rig.tab.checks_cb.isChecked())
    assert len(calls) == 6                               # 2 refusals, then all four files
    assert os.path.exists(tmp_path / bt.CHECKS_FILE)
    assert not any("Could not save" in line for line in rig.logged)


def test_file_that_stays_busy_warns_and_carries_on(tmp_path, monkeypatch):
    from wolfrat import bans_tab as bt
    rig = Rig(tmp_path)

    def always_busy(src, dst):
        raise PermissionError(13, "The process cannot access the file", None, 32)

    monkeypatch.setattr(bt.os, "replace", always_busy)
    monkeypatch.setattr(bt.time, "sleep", lambda _s: None)
    rig.tab.idle_cb.setChecked(not rig.tab.idle_cb.isChecked())
    warned = [line for line in rig.logged if "Could not save" in line]
    assert len(warned) == 4 and "Could not save " + bt.BANS_FILE in warned[0]
    assert "Could not save" in rig.tab.log_text.toPlainText()


def test_log_before_the_log_box_exists_goes_to_the_console(tmp_path):
    rig = Rig(tmp_path)
    del rig.tab.log_text
    rig.tab.log("early line")
    assert rig.logged[-1] == "early line"