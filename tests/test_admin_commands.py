import unittest

from wolfrat.admin_commands import (
    AdminCommands,
    AdminOperation,
    AdminSnapshot,
    CANONICAL_SETTING_KEYS,
    IdentityCollection,
    MAX_CHAT_LEN,
    MissionEntry,
    PlayerEntry,
    ReplyPolicy,
    WeaponEntry,
    WeaponMode,
    parse_available_missions,
    parse_game_settings,
    parse_missions,
    parse_players,
    parse_weapons,
    raw_command,
)


class CommandCatalogTests(unittest.TestCase):
    def test_query_catalog_uses_exact_retail_grammar_and_parsers(self):
        cases = (
            (AdminCommands.game_state(), "GET GAMESTATE", AdminOperation.GET_GAMESTATE),
            (AdminCommands.game_settings(), "GET GAMESETTINGS", AdminOperation.GET_GAMESETTINGS),
            (AdminCommands.players(), "PLAYER LIST", AdminOperation.PLAYER_LIST),
            (AdminCommands.missions(), "MISSION LIST", AdminOperation.MISSION_LIST),
            (AdminCommands.available_missions(), "MISSION AVAILABLE", AdminOperation.MISSION_AVAILABLE),
            (AdminCommands.weapons(), "WEAPON LIST", AdminOperation.WEAPON_LIST),
            (AdminCommands.chat(), "CHAT GET", AdminOperation.CHAT_GET),
        )
        for spec, text, operation in cases:
            with self.subTest(text=text):
                self.assertEqual(spec.text, text)
                self.assertEqual(spec.operation, operation)
                self.assertEqual(spec.reply_policy, ReplyPolicy.ONE)
                self.assertFalse(spec.mutating)

    def test_setting_schema_is_the_exact_29_key_retail_catalog(self):
        self.assertEqual(
            CANONICAL_SETTING_KEYS,
            (
                "AutoBalanceOnRecycle", "PuntVote", "VotePercent",
                "VoteNumPlayersReq", "ChangeTeam", "ChangeTeamInterval",
                "ChangeTeamPenalty", "ChangeTeamDelay", "StartDelay",
                "DoMinPingCheck", "MinPing", "DoMaxPingCheck", "MaxPing",
                "MaxFriendlyKills", "GameTime", "FriendlyFire",
                "FriendlyTags", "TeamTriggerClaymore", "Tracers", "KOTHLimit",
                "KillLimit", "MaxScore", "FatBullets", "OneShotKill",
                "ArmoryTimer", "ServerName", "ServerPassword",
                "SideAPassword", "SideBPassword",
            ),
        )
        self.assertEqual(
            AdminCommands.set_setting("kothlimit", 20).text,
            "SET KOTHLimit 20",
        )
        self.assertEqual(
            AdminCommands.set_setting("FriendlyFire", True).text,
            "SET FriendlyFire 1",
        )
        self.assertEqual(
            AdminCommands.set_setting("ServerPassword", "").text,
            "SET ServerPassword",
        )

    def test_unsafe_or_invented_settings_cannot_be_serialized(self):
        bad = (
            ("gamePass", "15"),
            ("weapon_m4", "1"),
            ("ServerName", ""),
            ("ServerPassword", "x" * 17),
            ("ServerPassword", "two words"),
            ("SideAPassword", "two\twords"),
            ("SideBPassword", "two words"),
            ("ServerName", "x" * 28),
            ("FriendlyFire", 2),
            ("VotePercent", "not-a-number"),
            ("VotePercent", 1.01),
        )
        for key, value in bad:
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    AdminCommands.set_setting(key, value)

    def test_mission_mutations_use_authoritative_queue_identity(self):
        current = MissionEntry(4, "CP08.BMS", revision=11)
        available = parse_available_missions(
            "7. CP08.BMS (Operation Copperhead)\n", revision=11
        )[0]

        self.assertEqual(AdminCommands.remove_mission(current).text, "MISSION REMOVE 4")
        self.assertEqual(AdminCommands.remove_mission(current).identity.revision, 11)
        self.assertEqual(AdminCommands.remove_mission(current).identity.key, 4)
        self.assertEqual(
            AdminCommands.remove_mission(current).identity.collection,
            IdentityCollection.MISSIONS,
        )
        self.assertEqual(AdminCommands.set_next_mission(current).text, "MISSION SETNEXT 4")
        self.assertEqual(
            AdminCommands.add_mission(available).text,
            "MISSION ADD CP08.BMS",
        )
        self.assertEqual(
            AdminCommands.add_mission(
                available, auto_switch_sides=False, insert_at=2, one_shot=True
            ).text,
            "MISSION ADD CP08.BMS 0 2 ONESHOT 1",
        )
        self.assertEqual(
            AdminCommands.add_mission(available, insert_at=2).text,
            "MISSION ADD CP08.BMS 0 2",
        )
        self.assertEqual(AdminCommands.clear_missions().text, "MISSION CLEAR")
        self.assertEqual(AdminCommands.cycle_mission().text, "MISSION CYCLE")

        with self.assertRaises(TypeError):
            AdminCommands.remove_mission(4)
        with self.assertRaises(ValueError):
            AdminCommands.add_mission(available, one_shot=True)

    def test_player_mutations_require_non_host_player_records(self):
        player = PlayerEntry(12, "biggy", 2, revision=9)
        self.assertEqual(AdminCommands.punt(player).text, "PLAYER PUNT 12")
        self.assertEqual(AdminCommands.punt(player).identity.revision, 9)
        self.assertEqual(AdminCommands.punt(player).identity.key, 12)
        self.assertEqual(AdminCommands.ban(player).text, "PLAYER BAN 12")
        self.assertEqual(AdminCommands.kill(player).text, "PLAYER KILL 12")
        self.assertEqual(AdminCommands.swap_team(player).text, "PLAYER SWAPTEAM 12")
        self.assertEqual(AdminCommands.zero_score(player).text, "PLAYER ZEROSCORE 12")
        self.assertEqual(AdminCommands.ban(player).reply_policy, ReplyPolicy.TERMINAL)

        host = PlayerEntry(0, "biggy", 0, revision=9)
        for build in (
            AdminCommands.punt,
            AdminCommands.ban,
            AdminCommands.kill,
            AdminCommands.swap_team,
            AdminCommands.zero_score,
        ):
            with self.subTest(build=build.__name__):
                with self.assertRaises(ValueError):
                    build(host)
        self.assertEqual(
            AdminCommands.kill(
                PlayerEntry(250, "Last slot", 1, revision=9)
            ).text,
            "PLAYER KILL 250",
        )
        with self.assertRaises(ValueError):
            AdminCommands.kill(
                PlayerEntry(251, "Outside roster", 1, revision=9)
            )

    def test_weapon_mutations_use_admdef_id_and_retail_modes(self):
        weapon = parse_weapons(" 37. ARMORY\tM4 Carbine\n", revision=5)[0]
        self.assertEqual(weapon.admdef_id, 37)
        self.assertEqual(weapon.mode, WeaponMode.ARMORY)
        self.assertEqual(
            AdminCommands.set_weapon(weapon, WeaponMode.NEVER).text,
            "WEAPON SET 37 NEVER",
        )
        self.assertEqual(
            AdminCommands.set_weapon(weapon, WeaponMode.NEVER).identity.revision,
            5,
        )
        self.assertEqual(
            AdminCommands.set_weapon(weapon, WeaponMode.NEVER).identity.key,
            37,
        )
        self.assertEqual(
            AdminCommands.set_all_weapons(WeaponMode.ALWAYS).text,
            "WEAPON SET ALL ALWAYS",
        )
        maximum = WeaponEntry(254, "Last retail weapon", WeaponMode.ARMORY, revision=5)
        self.assertEqual(
            AdminCommands.set_weapon(maximum, WeaponMode.NEVER).text,
            "WEAPON SET 254 NEVER",
        )
        with self.assertRaises(TypeError):
            AdminCommands.set_weapon("M4 Carbine", WeaponMode.ALWAYS)
        with self.assertRaises(ValueError):
            AdminCommands.set_weapon(
                WeaponEntry(255, "Out of bounds", WeaponMode.ARMORY, revision=5),
                WeaponMode.NEVER,
            )

    def test_chat_and_cmd_limits_are_validated_not_truncated(self):
        self.assertEqual(AdminCommands.send_chat("hello").text, "CHAT SEND hello")
        self.assertEqual(AdminCommands.tod("1730").text, "CMD TOD 1730")
        self.assertEqual(AdminCommands.tod_rate(5).text, "CMD TODRATE 5")
        for message in (
            "",
            "x" * (MAX_CHAT_LEN + 1),
            " ".join(["x"] * 24),
            "line\nbreak",
            "snowman \N{SNOWMAN}",
        ):
            with self.subTest(message=message):
                with self.assertRaises(ValueError):
                    AdminCommands.send_chat(message)
        for tod in ("2400", "1260", "noon"):
            with self.subTest(tod=tod):
                with self.assertRaises(ValueError):
                    AdminCommands.tod(tod)
        with self.assertRaises(ValueError):
            AdminCommands.tod_rate(0)

    def test_typed_cmd_specs_require_the_exact_retail_envelope_ack(self):
        for spec in (
            AdminCommands.tod("1730"),
            AdminCommands.tod_rate(5),
        ):
            with self.subTest(command=spec.text):
                self.assertTrue(
                    spec.accepts_replies(("OK - Command executed.",))
                )
                self.assertFalse(spec.accepts_replies(("OK",)))
                self.assertFalse(
                    spec.accepts_replies((" OK - Command executed. ",))
                )
                self.assertFalse(
                    spec.accepts_replies(
                        ("OK - Command executed.", "unexpected")
                    )
                )

    def test_reply_policies_encode_retail_multi_reply_behavior(self):
        self.assertTrue(ReplyPolicy.ONE.is_complete(("OK",)))
        self.assertFalse(ReplyPolicy.TWO.is_complete(("ERROR - In Game",)))
        self.assertTrue(ReplyPolicy.TWO.is_complete(("ERROR - In Game", "OK")))
        self.assertFalse(ReplyPolicy.TERMINAL.is_complete(("Banned biggy",)))
        self.assertFalse(
            ReplyPolicy.TERMINAL.is_complete(
                ('OK - Banned Player #1 "biggy".',)
            )
        )
        self.assertTrue(ReplyPolicy.TERMINAL.is_complete(("Banned biggy", "OK - Player Banned.")))

        with self.assertRaises(ValueError):
            raw_command("GOTO GAMESTATE")
        self.assertEqual(raw_command("PLAYER BAN 12").reply_policy, ReplyPolicy.TERMINAL)
        self.assertEqual(raw_command("GET GAMESTATE").reply_policy, ReplyPolicy.ONE)
        self.assertEqual(raw_command("SET").reply_policy, ReplyPolicy.ONE)
        self.assertEqual(
            raw_command("SET Unknown").reply_policy, ReplyPolicy.ONE
        )
        self.assertEqual(
            raw_command("SET Unknown value").reply_policy, ReplyPolicy.TWO
        )
        with self.assertRaises(ValueError):
            raw_command("QUIT")
        with self.assertRaises(ValueError):
            raw_command("PLAYER BAN ALL")
        with self.assertRaises(ValueError):
            raw_command("PETERRABBIT")

    def test_raw_console_enforces_known_retail_buffer_limits_and_host_guard(self):
        unsafe = (
            "CHAT SEND " + "x" * (MAX_CHAT_LEN + 1),
            "CHAT SEND " + " ".join(["x"] * 24),
            "CMD " + "x" * 99,
            "CMD " + " ".join(["x"] * 25),
            "SET ServerPassword " + "x" * 17,
            "SET SideAPassword " + "x" * 17,
            "SET SideBPassword " + "x" * 17,
            "SET ServerPassword two words",
            "SET SideAPassword two\twords",
            "SET SideBPassword two words",
            "SET ServerName " + "x" * 28,
            "PLAYER",
            "PLAYER FOO 1",
            "PLAYER CEASEFIRE 1",
            "PLAYER BAN ALL",
            "PLAYER SWAPTEAM ALL",
            "PLAYER KILL ALL",
            "PLAYER ZEROSCORE ALL",
            "PLAYER KILL 0",
            "PLAYER KILL 251",
            "PLAYER SWAPTEAM not-a-number",
            "PLAYER ZEROSCORE 256",
            "PLAYER KILL biggy ignored",
            "PLAYER SWAPTEAM 0 ignored",
            "PLAYER ZEROSCORE -1 ignored",
            "PLAYER KILL ALL ignored",
            "GOTO MENUSTATE ignored",
            "GOTO GAMESTATE ignored",
            "GET",
            "GET UNKNOWN",
            "GET GAMESTATE ignored",
            "GET GAMESETTINGS ignored",
            "MISSION",
            "MISSION UNKNOWN",
            "PLAYER LIST ignored",
            "MISSION LIST ignored",
            "MISSION AVAILABLE ignored",
            "MISSION CLEAR ignored",
            "MISSION CYCLE ignored",
            "MISSION REMOVE",
            "MISSION REMOVE not-a-number",
            "MISSION REMOVE -1",
            "MISSION REMOVE 2 ignored",
            "MISSION SETNEXT",
            "MISSION SETNEXT not-a-number",
            "MISSION SETNEXT 2 ignored",
            "MISSION ADD",
            "MISSION ADD bad.txt",
            "MISSION ADD CP08.BMS maybe",
            "MISSION ADD CP08.BMS 1 nope",
            "MISSION ADD CP08.BMS 1 2 ONESHOT",
            "MISSION ADD CP08.BMS 1 2 ONESHOT maybe",
            "MISSION ADD CP08.BMS 1 2 ONESHOT 0",
            "WEAPON",
            "WEAPON UNKNOWN",
            "WEAPON LIST ignored",
            "WEAPON SET",
            "WEAPON SET unknown NEVER",
            "WEAPON SET 255 NEVER",
            "WEAPON SET 256 NEVER",
            "WEAPON SET 3 SOMETIMES",
            "WEAPON SET 3 NEVER ignored",
            "CHAT",
            "CHAT UNKNOWN",
            "CHAT GET ignored",
        )
        for command in unsafe:
            with self.subTest(command=command[:40]):
                with self.assertRaises(ValueError):
                    raw_command(command)

        self.assertEqual(
            raw_command("CHAT SEND " + "x" * MAX_CHAT_LEN).text,
            "CHAT SEND " + "x" * MAX_CHAT_LEN,
        )
        self.assertEqual(
            raw_command("SET ServerPassword").text,
            "SET ServerPassword",
        )
        self.assertEqual(raw_command("PLAYER KILL 1").text, "PLAYER KILL 1")
        self.assertEqual(
            raw_command("PLAYER KILL 250").text,
            "PLAYER KILL 250",
        )
        self.assertEqual(
            raw_command("PLAYER PUNT ALL").text,
            "PLAYER PUNT ALL",
        )
        self.assertEqual(
            raw_command("MISSION ADD CP08.BMS 0 2 ONESHOT 1").text,
            "MISSION ADD CP08.BMS 0 2 ONESHOT 1",
        )
        self.assertEqual(
            raw_command("WEAPON SET ALL ARMORY").text,
            "WEAPON SET ALL ARMORY",
        )
        self.assertEqual(
            raw_command("WEAPON SET 254 NEVER").text,
            "WEAPON SET 254 NEVER",
        )


class RetailParserTests(unittest.TestCase):
    def test_player_parser_preserves_numeric_server_identity(self):
        payload = (
            "NAME            \t #\tTEAM\tClass\tKills\tDeaths\tPING\n"
            "biggy\t0\t0\tHost\t0\t0\t0\n"
            "Alice\t12\t1\tRifleman\t3\t2\t55\n"
        )
        players = parse_players(payload, revision=21)
        self.assertEqual([(p.server_id, p.name) for p in players], [(0, "biggy"), (12, "Alice")])
        self.assertTrue(all(p.revision == 21 for p in players))
        self.assertEqual(players[1].kills, 3)

    def test_mission_parser_preserves_queue_index_and_flags(self):
        payload = (
            "0: CP08.BMS - (2x) (IS FLIPPED) () <CURRENT MISSION> <>\n"
            "4: DM-DUST.NPZ - () () (ONE_SHOT) <> <NEXT MISSION>\n"
        )
        missions = parse_missions(payload, revision=17)
        self.assertEqual([m.queue_index for m in missions], [0, 4])
        self.assertTrue(missions[0].is_current)
        self.assertTrue(missions[0].double_time)
        self.assertTrue(missions[0].is_flipped)
        self.assertTrue(missions[1].is_next)
        self.assertTrue(missions[1].one_shot)

    def test_mission_parser_accepts_filenames_with_spaces(self):
        # Real rows from a TAC-mod server (v2.5.8 rejected these as malformed).
        payload = (
            "0: AS - Black Rock TAC.npj - () () () <CURRENT MISSION> <>\n"
            "1: AS - Dormant Volcano TAC.npj - (2x) () () <> <NEXT MISSION>\n"
            "2: DM-COD4Killhouse.npj - () () () <> <>\n"
        )
        missions = parse_missions(payload, revision=9)
        self.assertEqual(
            [m.filename for m in missions],
            ["AS - Black Rock TAC.npj", "AS - Dormant Volcano TAC.npj", "DM-COD4Killhouse.npj"],
        )
        self.assertTrue(missions[0].is_current)
        self.assertTrue(missions[1].double_time)
        self.assertTrue(missions[1].is_next)
        # Structure is still enforced: a row missing the " - " separator fails.
        with self.assertRaises(ValueError):
            parse_missions("1: AS - Black Rock TAC.npj () () () <> <>\n", revision=9)

    def test_available_weapon_and_settings_parsers_are_typed(self):
        available = parse_available_missions(
            "0. CP08.BMS (Operation Copperhead)\n1. DM-DUST.NPZ (Dust)\n",
            revision=3,
        )
        self.assertEqual((available[1].catalog_index, available[1].filename), (1, "DM-DUST.NPZ"))

        settings = parse_game_settings(
            "AutoBalanceOnRecycle = 1\nServerName = Dev Server\nKOTHLimit = 20\n",
            revision=4,
        )
        self.assertEqual(settings["ServerName"], "Dev Server")
        self.assertEqual(settings.revision, 4)

        weapons = parse_weapons(
            "  0.  NEVER \tKnife\n"
            " 37.  ARMORY\tM4 Carbine\n"
            " 99.  ALWAYS \tSmoke\n",
            revision=5,
        )
        self.assertEqual([w.admdef_id for w in weapons], [0, 37, 99])
        self.assertEqual(weapons[2].mode, WeaponMode.ALWAYS)

    def test_empty_chat_and_empty_collections_are_valid_replies(self):
        self.assertEqual(AdminCommands.chat().parse("", revision=1), ())
        self.assertEqual(parse_available_missions("", revision=1), ())
        self.assertEqual(parse_weapons("", revision=1), ())
        self.assertEqual(AdminSnapshot().revision, 0)

    def test_snapshot_evolves_only_the_domain_selected_by_the_operation(self):
        snapshot = AdminSnapshot()
        players = parse_players("Alice\t12\t1\tRifleman\t3\t2\t55\n", revision=1)
        updated = snapshot.apply(AdminOperation.PLAYER_LIST, players)
        self.assertEqual(updated.players, players)
        self.assertEqual(updated.revision, 1)
        self.assertEqual(updated.missions, ())

        settings = parse_game_settings("ServerName = Dev Server\n", revision=2)
        updated = updated.apply(AdminOperation.GET_GAMESETTINGS, settings)
        self.assertEqual(updated.settings["ServerName"], "Dev Server")
        self.assertEqual(updated.players, players)
        self.assertEqual(updated.revision, 2)

        # Mutation acknowledgements never overwrite an authoritative snapshot.
        self.assertIs(updated.apply(AdminOperation.MISSION_CYCLE, "OK"), updated)

    def test_malformed_identity_rows_fail_closed(self):
        with self.assertRaises(ValueError):
            parse_players("Alice\tbiggy\t1\tRifleman\t0\t0\t50\n", revision=1)
        with self.assertRaises(ValueError):
            parse_players("Alice\t251\t1\tRifleman\t0\t0\t50\n", revision=1)
        with self.assertRaises(ValueError):
            parse_missions("CP08.BMS <CURRENT MISSION>\n", revision=1)
        with self.assertRaises(ValueError):
            parse_weapons("M4 Carbine ARMORY\n", revision=1)
        with self.assertRaises(ValueError):
            parse_weapons("255. NEVER Out of bounds\n", revision=1)


if __name__ == "__main__":
    unittest.main()
