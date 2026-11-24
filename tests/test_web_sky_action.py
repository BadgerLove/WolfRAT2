"""Sky scenes from the phone (2026-11-24).

The Sky page's methods for the phone, the bridge's "sky" action and snapshot,
and the web server's route.  The phone's scene button does what the PC's
scene button does: same announcement, same log line, sent again to players
who join later.
"""

import os
import types

import pytest
from aiohttp.test_utils import TestClient, TestServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from wolfrat import manual_mode  # noqa: E402
from wolfrat import sky  # noqa: E402
from wolfrat.web_server import WolfWebServer  # noqa: E402
from tests.test_sky import FakeSkyServer, make_page, settle  # noqa: E402
from tests.test_web_bridge import make_bridge, run  # noqa: E402
from tests.test_web_phone_actions import Desktop, Manager  # noqa: E402

BLOOD_MOON = {
    "id": "bloodmoon", "label": "Blood Moon", "own": False,
    "colours": {"fog": "#b41414", "horizon": "#dc1e1e", "sun": "#641e1e", "sky": "#3c0a0a",
                "ground": "#280505", "ceiling": "#6e0f0f", "floor": "#2d0505",
                "lightning": "#ff0000"},
}


@pytest.fixture(autouse=True)
def clean_manual_mode():
    manual_mode.state.reset()
    yield
    manual_mode.state.reset()


def ready_page(qtbot, tmp_path, **kwargs):
    """A Sky page on a server that can do sky colours, its script answering."""
    page, server, said, logged, now = make_page(qtbot, tmp_path, **kwargs)
    settle(page, server, now)
    return page, server, said, logged, now


def bridge_on(page):
    weather_tab = types.SimpleNamespace(sky_page=page, _controller=None)
    bridge, _window, web = make_bridge(weather_tab=weather_tab)
    return bridge, web


# ---- the Sky page's methods for the phone -----------------------------------------

def test_phone_scenes_are_the_pages_scenes_in_its_order_with_their_colours(qtbot, tmp_path):
    page, *_ = ready_page(qtbot, tmp_path)
    scenes = page.phone_scenes()
    assert [item["id"] for item in scenes] == [scene.key for scene in sky.BUILTIN_SCENES]
    assert len(scenes) == 27 and scenes[1] == BLOOD_MOON
    arctic = next(item for item in scenes if item["id"] == "arctic")
    assert set(arctic["colours"]) == {"fog", "horizon", "sun", "sky", "ground"}   # rooms: the map's own
    assert all(item["own"] is False for item in scenes)


def test_a_scene_the_admin_saved_is_listed_as_his_own(qtbot, tmp_path):
    page, *_ = ready_page(qtbot, tmp_path)
    for key in sky.ELEMENT_KEYS:
        page._use[key].setChecked(False)
    page.set_colour("fog", (0, 255, 0))
    page.name_edit.setText("Swamp Gas")
    page._save_scene()
    last = page.phone_scenes()[-1]
    assert last == {"id": "swampgas", "label": "Swamp Gas", "own": True,
                    "colours": {"fog": "#00ff00"}}
    assert len(page.phone_scenes()) == 28


def test_phone_state_says_what_is_showing(qtbot, tmp_path):
    page, server, _said, _logged, now = ready_page(qtbot, tmp_path)
    assert page.phone_state() == {"summary": "The map's own sky.", "scene": None, "problem": None}
    page.phone_show("bloodmoon", None, "Web Admin")
    assert page.phone_state() == {"summary": "Blood Moon - until the map changes",
                                  "scene": "bloodmoon", "problem": None}
    page.phone_show("toxic", 10, "Web Admin")
    assert page.phone_state() == {"summary": "Toxic - 10:00 left", "scene": "toxic",
                                  "problem": None}
    now[0] += 61
    assert page.phone_state()["summary"] == "Toxic - 8:59 left"
    page.phone_off("Web Admin")
    assert page.phone_state()["scene"] is None
    assert page.phone_state()["summary"] == "The map's own sky."


def test_a_try_it_look_is_no_scene_of_the_list(qtbot, tmp_path):
    page, *_ = ready_page(qtbot, tmp_path)
    page.set_colour("fog", (1, 2, 3))
    page._try_clicked()
    state = page.phone_state()
    assert state["scene"] is None and state["summary"] == "Custom sky - until the map changes"


def test_phone_state_is_nothing_where_sky_colours_cannot_work(qtbot, tmp_path):
    page, *_ = ready_page(qtbot, tmp_path)
    page.no_server("gone")
    assert page.phone_state() is None
    (tmp_path / "b").mkdir()
    stock, *_ = make_page(qtbot, tmp_path / "b", server=FakeSkyServer(capable=False))
    assert stock.phone_state() is None


def test_phone_state_carries_why_a_sky_cannot_be_sent_yet(qtbot, tmp_path):
    page, *_ = make_page(qtbot, tmp_path, installed=False)
    assert page.phone_state() == {"summary": "The map's own sky.", "scene": None,
                                  "problem": "Sky: press Install on WolfRAT's Sky page first."}
    (tmp_path / "b").mkdir()
    deaf, server, _said, _logged, now = make_page(qtbot, tmp_path / "b",
                                                  server=FakeSkyServer(script=False))
    settle(deaf, server, now, seconds=8)
    assert deaf.phone_state()["problem"] == "Sky: ready after the next map change."
    deaf.chat_problem = lambda: "x" * 200
    assert len(deaf.phone_state()["problem"]) == 80


def test_phone_show_is_the_scene_button_with_the_phones_name(qtbot, tmp_path):
    page, server, said, logged, now = ready_page(qtbot, tmp_path)
    assert not page.mods_cb.isChecked()                 # the mods' !sky box does not gate it
    page.minutes_spin.setValue(30)
    assert page.phone_show("toxic", None, "Web Admin (Pixel)") == (
        True, "Sky: Toxic until the map changes.")
    assert said[-1] == "Sky: Toxic until the map changes."
    assert logged[-1] == "Web Admin (Pixel) set the sky to Toxic until the map changes."
    assert page.keeper.active.until is None and page.keeper.active.source == "page"
    assert page.minutes_spin.value() == 30              # the page's own box is left alone
    settle(page, server, now)
    assert server.held["fogcolor"] == (30, 90, 20)
    assert "Toxic" in page.now_lbl.text()


def test_phone_show_for_some_minutes_ends_by_itself(qtbot, tmp_path):
    page, server, said, logged, now = ready_page(qtbot, tmp_path)
    assert page.phone_show("bloodmoon", 10, "Web Admin") == (True, "Sky: Blood Moon for 10 min.")
    assert said[-1] == "Sky: Blood Moon for 10 min."
    assert logged[-1] == "Web Admin set the sky to Blood Moon for 10 min."
    assert page.keeper.active.until == now[0] + 600
    settle(page, server, now)
    assert server.lightning() == (255, 0, 0)
    now[0] += 600
    settle(page, server, now)
    assert page.keeper.active is None and server.held["sun"] is None
    assert said[-1] == "Sky: back to normal."


def test_players_who_join_later_get_the_phones_sky_again(qtbot, tmp_path):
    page, server, _said, _logged, now = ready_page(qtbot, tmp_path)
    page.phone_show("toxic", None, "Web Admin")
    settle(page, server, now)
    sent = len(server.applied)
    page.server_tick(server, True, "Map One", 2, True)          # someone joined
    settle(page, server, now, seconds=2)
    assert len(server.applied) > sent
    code, colours, fade = server.applied[-1]
    assert code == sky.CMD_LOOK and fade == 0 and colours["fogcolor"] == (30, 90, 20)


def test_the_announce_box_decides_the_chat_line(qtbot, tmp_path):
    page, server, said, logged, now = ready_page(qtbot, tmp_path)
    page.announce_cb.setChecked(False)
    before = list(said)
    assert page.phone_show("toxic", None, "Web Admin")[0] is True
    assert said == before and "set the sky to Toxic" in logged[-1]
    settle(page, server, now)
    assert server.held["fogcolor"] == (30, 90, 20)


def test_phone_off_is_the_maps_own_sky_button(qtbot, tmp_path):
    page, server, said, logged, now = ready_page(qtbot, tmp_path)
    page.phone_show("bloodmoon", None, "Web Admin")
    settle(page, server, now)
    assert page.phone_off("Web Admin (Pixel)") == (True, "Sky: back to the map's own.")
    assert said[-1] == "Sky: back to normal."
    assert logged[-1] == "Web Admin (Pixel) put the map's own sky back."
    settle(page, server, now)
    assert all(value is None for value in server.held.values())
    assert server.lightning() == (85, 85, 90)
    assert page.keeper.active is None and page.keeper.off_for_this_map is True


def test_the_phone_is_refused_with_the_reason_when_a_sky_cannot_be_sent(qtbot, tmp_path):
    page, server, said, logged, now = make_page(qtbot, tmp_path, installed=False)
    reason = "Sky: press Install on WolfRAT's Sky page first."
    assert page.phone_show("toxic", None, "Web Admin") == (False, reason)
    assert page.phone_off("Web Admin") == (False, reason)
    settle(page, server, now)
    assert said == [] and logged == [] and not server.applied and page.keeper.active is None
    page.no_server("gone")
    assert page.phone_show("toxic", None, "Web Admin") == (
        False, "Sky: WolfRAT is not running on the server PC.")
    (tmp_path / "b").mkdir()
    deaf, server2, said2, _logged, now2 = make_page(qtbot, tmp_path / "b",
                                                    server=FakeSkyServer(script=False))
    settle(deaf, server2, now2, seconds=8)
    assert deaf.phone_show("toxic", None, "Web Admin") == (
        False, "Sky: ready after the next map change.")
    assert said2 == [] and deaf.keeper.active is None


def test_phone_show_with_a_name_that_is_no_scene(qtbot, tmp_path):
    page, _server, said, _logged, _now = ready_page(qtbot, tmp_path)
    done, text = page.phone_show("lava", None, "Web Admin")
    assert done is False and text and said == [] and page.keeper.active is None


# ---- the bridge's action --------------------------------------------------------------

def test_the_sky_action_shows_a_scene_and_answers_the_phone(qtbot, tmp_path):
    page, server, said, logged, now = ready_page(qtbot, tmp_path)
    bridge, _web = bridge_on(page)
    result = run(bridge, "sky", {"scene": "bloodmoon", "minutes": 10, "device_name": "Pixel"})
    assert result == {"ok": True, "accepted": True, "replies": ["Sky: Blood Moon for 10 min."]}
    assert logged[-1] == "Web Admin (Pixel) set the sky to Blood Moon for 10 min."
    assert said[-1] == "Sky: Blood Moon for 10 min."
    for payload in ({"scene": "toxic"}, {"scene": "toxic", "minutes": None}):
        assert run(bridge, "sky", payload)["replies"] == ["Sky: Toxic until the map changes."]
    assert logged[-1] == "Web Admin set the sky to Toxic until the map changes."
    settle(page, server, now)
    assert server.held["fogcolor"] == (30, 90, 20)


def test_the_sky_action_off_ignores_the_minutes(qtbot, tmp_path):
    page, server, said, _logged, now = ready_page(qtbot, tmp_path)
    bridge, _web = bridge_on(page)
    run(bridge, "sky", {"scene": "bloodmoon"})
    settle(page, server, now)
    result = run(bridge, "sky", {"scene": "off", "minutes": "whatever", "device_name": "Pixel"})
    assert result == {"ok": True, "accepted": True, "replies": ["Sky: back to the map's own."]}
    settle(page, server, now)
    assert server.held["sun"] is None and page.keeper.active is None


@pytest.mark.parametrize("payload", [
    {}, {"scene": None}, {"scene": 5}, {"scene": "lava"}, {"scene": "Blood Moon"},
    {"scene": "BLOODMOON"}, {"scene": ""}, {"scene": ["bloodmoon"]},
    {"scene": "bloodmoon", "minutes": 0}, {"scene": "bloodmoon", "minutes": 241},
    {"scene": "bloodmoon", "minutes": -5}, {"scene": "bloodmoon", "minutes": 2.5},
    {"scene": "bloodmoon", "minutes": "10"}, {"scene": "bloodmoon", "minutes": True},
])
def test_an_unknown_scene_or_bad_minutes_is_bad_input(qtbot, tmp_path, payload):
    page, _server, said, logged, _now = ready_page(qtbot, tmp_path)
    bridge, _web = bridge_on(page)
    result = run(bridge, "sky", payload)
    assert result["ok"] is False and result["status"] == 400, payload
    assert said == [] and logged == [] and page.keeper.active is None


def test_the_edges_of_the_minutes_are_taken(qtbot, tmp_path):
    page, *_ = ready_page(qtbot, tmp_path)
    bridge, _web = bridge_on(page)
    assert run(bridge, "sky", {"scene": "toxic", "minutes": 1})["replies"] == [
        "Sky: Toxic for 1 min."]
    assert run(bridge, "sky", {"scene": "toxic", "minutes": 240})["replies"] == [
        "Sky: Toxic for 240 min."]


def test_the_sky_action_says_why_when_it_cannot_be_sent(qtbot, tmp_path):
    page, server, said, _logged, now = make_page(qtbot, tmp_path, installed=False)
    bridge, _web = bridge_on(page)
    for payload in ({"scene": "toxic"}, {"scene": "off"}):
        result = run(bridge, "sky", payload)
        assert result["ok"] is False and result["status"] == 409
        assert result["error"] == "Sky: press Install on WolfRAT's Sky page first."
    assert said == [] and page.keeper.active is None
    assert run(bridge, "sky", {"scene": "lava"})["status"] == 400      # bad input first
    nothing, _window, _web2 = make_bridge()                            # no Weather tab at all
    assert run(nothing, "sky", {"scene": "toxic"})["status"] == 409


# ---- the snapshot -----------------------------------------------------------------------

def test_the_snapshot_carries_the_sky_and_its_scenes(qtbot, tmp_path):
    page, *_ = ready_page(qtbot, tmp_path)
    bridge, web = bridge_on(page)
    run(bridge, "sky", {"scene": "bloodmoon"})
    bridge.publish()
    top = web.desktop_state["top"]
    assert top["sky"] == {"summary": "Blood Moon - until the map changes", "scene": "bloodmoon",
                          "problem": None}
    assert top["features"]["sky"] is True
    assert top["features"]["sky_scenes"] == page.phone_scenes()
    assert top["features"]["sky_scenes"][1] == BLOOD_MOON


def test_the_snapshot_without_sky_colours_has_no_sky(qtbot, tmp_path):
    stock, *_ = make_page(qtbot, tmp_path, server=FakeSkyServer(capable=False))
    bridge, web = bridge_on(stock)
    bridge.publish()
    top = web.desktop_state["top"]
    assert top["sky"] is None
    assert top["features"]["sky"] is False and top["features"]["sky_scenes"] == []
    plain, _window, web2 = make_bridge()
    plain.publish()
    assert web2.desktop_state["top"]["sky"] is None
    assert web2.desktop_state["top"]["features"]["sky"] is False


def test_the_snapshot_shows_the_problem_while_the_sky_cannot_be_sent(qtbot, tmp_path):
    page, *_ = make_page(qtbot, tmp_path, installed=False)
    bridge, web = bridge_on(page)
    bridge.publish()
    top = web.desktop_state["top"]
    assert top["sky"]["problem"] == "Sky: press Install on WolfRAT's Sky page first."
    assert top["features"]["sky"] is True and len(top["features"]["sky_scenes"]) == 27


# ---- the web server's side -----------------------------------------------------------------

@pytest.fixture
async def rig():
    manager = Manager()
    service = WolfWebServer(manager, host="127.0.0.1", port=0)
    service.set_auth("operator", "web-access-token")
    key, _device = service.create_device("Pixel 8")
    client = TestClient(TestServer(service.application))
    await client.start_server()
    try:
        yield service, client, {"Authorization": f"Bearer {key}"}
    finally:
        await client.close()


async def test_the_sky_route_hands_its_fields_to_the_window(rig):
    service, client, auth = rig
    desktop = service.on_desktop = Desktop()
    desktop.answers["sky"] = {"ok": True, "replies": ["Sky: Blood Moon for 10 min."]}
    response = await client.post("/api/sky", headers=auth,
                                 json={"scene": "bloodmoon", "minutes": 10, "junk": 1})
    assert response.status == 200
    assert await response.json() == {"ok": True, "accepted": True,
                                     "replies": ["Sky: Blood Moon for 10 min."]}
    assert desktop.asked == [("sky", {"scene": "bloodmoon", "minutes": 10,
                                      "device_name": "Pixel 8"})]


async def test_the_sky_route_passes_the_windows_refusal_on(rig):
    service, client, auth = rig
    desktop = service.on_desktop = Desktop()
    desktop.answers["sky"] = {"ok": False, "error": "Sky: ready after the next map change.",
                              "status": 409}
    response = await client.post("/api/sky", headers=auth, json={"scene": "toxic"})
    payload = await response.json()
    assert response.status == 409 and payload["ok"] is False
    assert payload["error"] == "Sky: ready after the next map change."
    desktop.answers["sky"] = {"ok": False, "error": "Unknown sky", "status": 400}
    assert (await client.post("/api/sky", headers=auth, json={"scene": "lava"})).status == 400


async def test_the_sky_route_needs_a_key_a_json_object_and_the_window(rig):
    service, client, auth = rig
    assert (await client.post("/api/sky", json={"scene": "toxic"})).status == 401
    assert (await client.post("/api/sky", headers=auth, json={"scene": "toxic"})).status == 503
    service.on_desktop = Desktop()
    assert (await client.post("/api/sky", headers=auth, json=["toxic"])).status == 400


async def test_the_state_has_the_sky_keys_with_and_without_the_window(rig):
    service, client, auth = rig
    state = await (await client.get("/api/status", headers=auth)).json()
    assert state["sky"] is None
    assert state["features"]["sky"] is False and state["features"]["sky_scenes"] == []
    service.on_desktop = Desktop()
    service.desktop_state = {"top": {
        "sky": {"summary": "Blood Moon - until the map changes", "scene": "bloodmoon",
                "problem": None},
        "features": {"sky": True, "sky_scenes": [BLOOD_MOON]},
    }, "players": {}}
    state = await (await client.get("/api/status", headers=auth)).json()
    assert state["sky"] == {"summary": "Blood Moon - until the map changes",
                            "scene": "bloodmoon", "problem": None}
    assert state["features"]["sky"] is True and state["features"]["sky_scenes"] == [BLOOD_MOON]
