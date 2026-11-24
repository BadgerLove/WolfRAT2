"""WolfRAT's own ban list (2026-09-22): matching, punt-on-sight, import, firewall lines."""
import time

import pytest

from wolfrat import ban_rules as br
from wolfrat.ban_rules import BanEntry, BanList, Enforcer, KIND_IP, KIND_NAME

NOW = 1_800_000_000.0


def ip(value, **kw):
    return BanEntry(KIND_IP, value, added_at=NOW, **kw)


def name(value, **kw):
    return BanEntry(KIND_NAME, value, added_at=NOW, **kw)


# ---- matching -----------------------------------------------------------------

@pytest.mark.parametrize("pattern, addr, hit", [
    ("198.51.100.92", "198.51.100.92", True),
    ("198.51.100.92", "198.51.100.93", False),
    ("198.51.100.*", "198.51.100.7", True),
    ("198.51.*", "198.51.1.1", True),
    ("198.5.*", "198.51.1.1", False),          # "198.5." must not match "198.51."
    ("198.*", "83.0.0.1", False),
    ("198.51.0.0/16", "198.51.200.1", True),
    ("198.51.0.0/16", "198.52.0.1", False),
    ("10.0.0.1-10.0.0.9", "10.0.0.5", True),
    ("10.0.0.1-10.0.0.9", "10.0.0.10", False),
    ("198.51.100.92", "", False),
    ("198.51.100.92", "garbage", False),
])
def test_ip_patterns(pattern, addr, hit):
    assert br.ip_matches(pattern, addr) is hit


@pytest.mark.parametrize("pattern, who, hit", [
    ("BadgerLove", "badgerlove", True),
    ("BadgerLove", "BadgerLove2", False),
    ("FMJ-*", "FMJ-BadgerLove", True),
    ("FMJ-*", "fmj-x", True),
    ("*", "anyone", False),                  # a bare star bans nobody
    ("", "anyone", False),
])
def test_name_patterns(pattern, who, hit):
    assert br.name_matches(pattern, who) is hit


@pytest.mark.parametrize("pattern, problem", [
    ("198.51.100.92", None), ("198.51.*", None), ("198.51.0.0/16", None), ("1.1.1.1-1.1.1.9", None),
    ("999.1.1.1", "not a valid IP address"), ("198.999.*", "a number in the address is over 255"),
    ("1.1.1.9-1.1.1.1", "the range runs backwards"), ("hello", "not an IP address, wildcard, CIDR or range"),
])
def test_pattern_problems(pattern, problem):
    assert br.ip_pattern_problem(pattern) == problem


def test_whitelist_beats_a_ban_and_expiry_ends_one():
    bans = BanList()
    bans.add(ip("198.51.*", reason="range"))
    bans.add(name("Troll"))
    assert bans.check("Someone", "198.51.1.1", NOW).reason == "range"
    bans.add(ip("198.51.100.92"), whitelist=True)
    assert bans.check("Someone", "198.51.100.92", NOW) is None
    assert bans.check("Troll", "1.2.3.4", NOW).value == "Troll"
    bans.add(name("Troll", expires_at=NOW + 60))
    assert bans.check("troll", "", NOW + 59) is not None
    assert bans.check("troll", "", NOW + 61) is None
    assert bans.purge_expired(NOW + 61) == 1


def test_add_replaces_same_entry_and_keeps_its_hit_count():
    bans = BanList()
    first = ip("1.2.3.4", reason="old"); first.hits = 3
    assert bans.add(first) is True
    assert bans.add(ip("1.2.3.4", reason="new")) is False
    assert len(bans.entries) == 1 and bans.entries[0].reason == "new" and bans.entries[0].hits == 3
    assert bans.remove(KIND_IP, "1.2.3.4") is True and not bans.entries


# ---- punt on sight ---------------------------------------------------------------

def test_enforcer_punts_once_per_cooldown_and_records_the_hit():
    bans = BanList(); bans.add(name("Troll", reason="griefing"))
    enf = Enforcer(cooldown=30)
    players = [{"id": "3", "name": "Troll"}, {"id": "4", "name": "Nice"}]
    first = enf.decide(bans, players, {"Troll": "5.6.7.8"}, NOW)
    assert [r.player_id for r in first] == ["3"]
    assert first[0].why() == "Troll (5.6.7.8) matches banned name Troll - griefing"
    assert bans.entries[0].hits == 1 and bans.entries[0].last_hit_ip == "5.6.7.8"
    assert enf.decide(bans, players, {"Troll": "5.6.7.8"}, NOW + 10) == []      # still there: wait
    assert len(enf.decide(bans, players, {"Troll": "5.6.7.8"}, NOW + 31)) == 1  # still there after 30 s: again


def test_a_rejoin_after_a_successful_punt_is_punted_at_once():
    """Live 2026-09-22: Dale got back in and spammed chat for up to 30 s because
    the cooldown ignored that the punt had already worked."""
    bans = BanList(); bans.add(name("Troll"))
    enf = Enforcer(cooldown=30)
    troll = [{"id": "3", "name": "Troll"}]
    assert len(enf.decide(bans, troll, {"Troll": "5.6.7.8"}, NOW)) == 1
    assert enf.decide(bans, [], {}, NOW + 5) == []                    # gone - punt worked
    assert len(enf.decide(bans, troll, {"Troll": "5.6.7.8"}, NOW + 10)) == 1   # back 10 s later: out again
    enf.note_punted("Troll", "5.6.7.8", NOW + 20)                     # hand punt from the button
    assert enf.decide(bans, troll, {"Troll": "5.6.7.8"}, NOW + 21) == []       # no double punt this tick


def test_enforcer_ip_ban_works_without_a_name_match_and_says_ip_unknown_otherwise():
    bans = BanList(); bans.add(ip("5.6.7.*"))
    enf = Enforcer()
    assert enf.decide(bans, [{"id": "1", "name": "NewName"}], {"NewName": "5.6.7.8"}, NOW)[0].name == "NewName"
    assert enf.decide(bans, [{"id": "1", "name": "NewName"}], {}, NOW) == []       # no IP known = cannot match an IP ban
    bans.add(name("NewName"))
    r = enf.decide(bans, [{"id": "2", "name": "NewName"}], {}, NOW + 100)
    assert r and "IP unknown" in r[0].why()


# ---- import / export ----------------------------------------------------------------

def test_import_accepts_every_shape_we_promised():
    text = """
    # comment
    [Banned]
    198.51.100.92
    198.51.* | whole range
    Troll | griefing | 7d
    10.0.0.0/8, lab
    1.1.1.1-1.1.1.9\tvia tab
    5.5.5.5=babstats style reason
    BAN "0A1B2C3D" "OldJoName"
    999.1.1.1
    Someone | | 2026-12-31
    """
    rows = br.parse_import(text, now=NOW, added_by="test")
    good = [r for r in rows if r.entry]
    bad = [r for r in rows if not r.entry]
    assert [(r.entry.kind, r.entry.value) for r in good] == [
        (KIND_IP, "198.51.100.92"), (KIND_IP, "198.51.*"), (KIND_NAME, "Troll"), (KIND_IP, "10.0.0.0/8"),
        (KIND_IP, "1.1.1.1-1.1.1.9"), (KIND_IP, "5.5.5.5"), (KIND_NAME, "OldJoName"), (KIND_NAME, "Someone"),
    ]
    troll = good[2].entry
    assert troll.reason == "griefing" and troll.expires_at == NOW + 7 * 86400 and troll.added_by == "test"
    assert good[5].entry.reason == "babstats style reason"
    assert good[7].entry.expires_at == time.mktime(time.strptime("2026-12-31", "%Y-%m-%d"))
    assert len(bad) == 1 and bad[0].problem == "not a valid IP address"


def test_bad_expiry_is_reported_not_guessed():
    rows = br.parse_import("Troll | x | soon", now=NOW)
    assert rows[0].entry is None and "not an expiry" in rows[0].problem


def test_export_round_trips():
    bans = BanList()
    bans.add(ip("198.51.*", reason="range | with pipe"))
    bans.add(name("Troll", reason="griefing", expires_at=NOW + 86400))
    text = br.export_lines(bans.entries)
    back = [r.entry for r in br.parse_import(text, now=NOW)]
    assert [(e.kind, e.value, e.reason) for e in back] == [
        (KIND_IP, "198.51.*", "range / with pipe"), (KIND_NAME, "Troll", "griefing")]
    assert back[0].expires_at is None and back[1].expires_at is not None


def test_json_round_trip_survives_junk():
    bans = BanList(); bans.add(ip("1.2.3.4", reason="r", expires_at=NOW + 5)); bans.add(name("X"), whitelist=True)
    again = BanList.from_json(bans.to_json())
    assert again.entries[0].value == "1.2.3.4" and again.entries[0].expires_at == NOW + 5
    assert again.whitelist[0].kind == KIND_NAME
    assert BanList.from_json(None).entries == []
    assert BanList.from_json({"entries": [{"kind": "ip", "value": "9.9.9.9", "expires_at": "junk", "hits": "no"}]}).entries[0].hits == 0


# ---- firewall lines ----------------------------------------------------------------

@pytest.mark.parametrize("pattern, address", [
    ("198.51.100.92", "198.51.100.92"),
    ("198.51.*", "198.51.0.0-198.51.255.255"),
    ("198.*", "198.0.0.0-198.255.255.255"),
    ("198.51.0.0/16", "198.51.0.0/16"),
    ("1.1.1.1 - 1.1.1.9", "1.1.1.1-1.1.1.9"),
])
def test_firewall_address_shapes(pattern, address):
    assert br.firewall_address(pattern) == address


def test_firewall_commands_are_one_line_and_pair_up():
    block = br.firewall_block_command("198.51.*")
    unblock = br.firewall_unblock_command("198.51.*")
    assert "\n" not in block and "\n" not in unblock
    assert block.startswith('New-NetFirewallRule -DisplayName "WolfRAT ban 198.51.*" -Direction Inbound -Action Block -Protocol UDP -RemoteAddress 198.51.0.0-198.51.255.255')
    assert unblock.startswith('Remove-NetFirewallRule -DisplayName "WolfRAT ban 198.51.*"')
