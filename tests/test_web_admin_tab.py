"""The Web Admin tab (2026-11-23): the one internet tick and its upgrade rule,
pairing a phone, the device list with Sign out, and Regenerate Token."""

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from wolfrat.app import WebAdminTab
from wolfrat.runtime import DesktopRuntime
from wolfrat.web_server import WolfWebServer

from tests.test_web_http_integration import FakeServerManager


def make_tab(qtbot, tmp_path, config=None):
    runtime = DesktopRuntime.isolated(tmp_path)
    if config is not None:
        runtime.path("wolfrat_web.json").write_text(json.dumps(config), encoding="utf-8")
    server = WolfWebServer(FakeServerManager(), host="127.0.0.1", port=0)
    tab = WebAdminTab(server, runtime)
    qtbot.addWidget(tab)
    return tab, server


def saved(tmp_path):
    return json.loads((tmp_path / "wolfrat_web.json").read_text(encoding="utf-8"))


def test_a_new_install_takes_phones_from_this_network_only(qtbot, tmp_path):
    tab, server = make_tab(qtbot, tmp_path)
    assert tab.internet_cb.isChecked() is False
    assert server.allow_internet is False
    config = saved(tmp_path)
    assert config["web_allow_internet"] is False
    assert config["web_token"]
    assert config["devices"] == []
    assert "Open port 8070 on your router" in tab.internet_hint.text()


def test_an_install_with_web_admin_on_keeps_internet_access(qtbot, tmp_path):
    tab, server = make_tab(qtbot, tmp_path, {
        "web_enabled": True, "web_port": 8071, "web_username": "admin",
        "web_token": "old-token", "login_ips": {"1.2.3.4": 1.0},
    })
    # Not started here (start_configured_server does that); the rule only.
    assert tab.internet_cb.isChecked() is True
    assert server.allow_internet is True
    config = saved(tmp_path)
    assert config["web_allow_internet"] is True
    assert config["web_token"] == "old-token"
    assert "login_ips" not in config
    assert "Open port 8071" in tab.internet_hint.text()


def test_an_install_with_web_admin_off_gets_this_network_only(qtbot, tmp_path):
    tab, server = make_tab(qtbot, tmp_path, {"web_enabled": False, "web_token": "t"})
    assert tab.internet_cb.isChecked() is False
    assert server.allow_internet is False


def test_a_saved_choice_is_kept_and_the_tick_saves(qtbot, tmp_path):
    tab, server = make_tab(qtbot, tmp_path, {
        "web_enabled": True, "web_token": "t", "web_allow_internet": False})
    assert tab.internet_cb.isChecked() is False
    tab.internet_cb.setChecked(True)
    assert server.allow_internet is True
    assert saved(tmp_path)["web_allow_internet"] is True
    tab.port_spin.setValue(9001)
    assert "Open port 9001" in tab.internet_hint.text()


def test_the_token_is_masked_until_shown(qtbot, tmp_path):
    from PyQt6.QtWidgets import QLineEdit
    tab, _server = make_tab(qtbot, tmp_path, {"web_token": "t"})
    assert tab.token_display.echoMode() == QLineEdit.EchoMode.Password
    tab.show_token_btn.setChecked(True)
    assert tab.token_display.echoMode() == QLineEdit.EchoMode.Normal
    assert tab.token_display.text() == "t"


def test_devices_are_saved_listed_and_signed_out_from_the_desktop(qtbot, tmp_path):
    tab, server = make_tab(qtbot, tmp_path, {"web_token": "t", "web_username": "admin"})
    _key, device_id = server.create_device("Dale's phone", "192.168.1.30")
    qtbot.waitUntil(lambda: device_id in tab._device_rows, timeout=2000)
    assert [d["id"] for d in saved(tmp_path)["devices"]] == [device_id]
    label, button = tab._device_rows[device_id]
    assert "Dale's phone" in label.text() and "192.168.1.30" in label.text()
    assert tab.devices_empty.isHidden()

    button.click()
    assert server.devices_for_config() == []
    assert saved(tmp_path)["devices"] == []
    assert tab._device_rows == {}
    assert not tab.devices_empty.isHidden()


def test_saved_devices_come_back_after_a_restart(qtbot, tmp_path):
    tab, server = make_tab(qtbot, tmp_path, {"web_token": "t"})
    key, device_id = server.create_device("phone")
    qtbot.waitUntil(lambda: bool(saved(tmp_path)["devices"]), timeout=2000)

    again, server_again = make_tab(qtbot, tmp_path)
    assert server_again._device_for_key(key)["id"] == device_id
    assert device_id in again._device_rows


def test_regenerate_token_signs_every_phone_out(qtbot, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    tab, server = make_tab(qtbot, tmp_path, {"web_token": "t"})
    server.create_device("phone")
    qtbot.waitUntil(lambda: bool(saved(tmp_path)["devices"]), timeout=2000)
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    tab._regen_token()
    config = saved(tmp_path)
    assert config["web_token"] != "t"
    assert config["devices"] == []
    assert server.devices_for_config() == []
    assert tab._device_rows == {}


def test_pair_a_phone_shows_a_code_and_a_qr_code(qtbot, tmp_path, monkeypatch):
    import wolfrat.app as app_module
    monkeypatch.setattr(app_module, "_lan_ips", lambda: ["192.168.1.20"])
    tab, server = make_tab(qtbot, tmp_path, {"web_token": "t", "web_port": 8070})
    tab.pair_btn.click()
    dialog = tab._pair_dialog
    assert dialog is not None
    code = dialog.code
    assert server.pairing_code()[0] == code
    assert dialog.code_label.text().replace(" ", "") == code
    assert dialog.lan_url() == f"http://192.168.1.20:8070/#pair={code}"
    assert dialog.url_label.text() == dialog.lan_url()
    assert not dialog.qr_label.pixmap().isNull()
    assert dialog.internet_url() is None
    assert "Valid for 5:00" in dialog.countdown_label.text() or \
        "Valid for 4:5" in dialog.countdown_label.text()

    # A phone uses the code: the window says so.
    async def pair():
        client = TestClient(TestServer(server.application))
        await client.start_server()
        try:
            response = await client.post("/api/pair", json={"code": code, "device_name": "Pixel"})
            return response.status, await response.json()
        finally:
            await client.close()

    status, body = asyncio.run(pair())
    assert status == 200
    qtbot.waitUntil(lambda: "Pixel is paired" in dialog.status_label.text(), timeout=2000)
    assert body["device_id"] in tab._device_rows
    dialog.close()
    assert tab._pair_dialog is None


def test_closing_the_pair_window_ends_an_unused_code(qtbot, tmp_path, monkeypatch):
    import wolfrat.app as app_module
    monkeypatch.setattr(app_module, "_lan_ips", lambda: [])
    tab, server = make_tab(qtbot, tmp_path, {"web_token": "t"})
    tab.pair_btn.click()
    dialog = tab._pair_dialog
    assert "No network address found" in dialog.url_label.text()
    first = dialog.code
    dialog.new_code_btn.click()
    assert dialog.code != first
    assert server.pairing_code()[0] == dialog.code
    dialog.close()
    assert server.pairing_code() is None


def test_the_internet_address_is_offered_only_when_internet_is_allowed(qtbot, tmp_path, monkeypatch):
    import wolfrat.app as app_module
    monkeypatch.setattr(app_module, "_lan_ips", lambda: ["192.168.1.20"])
    tab, server = make_tab(qtbot, tmp_path, {
        "web_token": "t", "web_allow_internet": False,
        "web_internet_address": "203.0.113.7:8070"})
    assert server.internet_address == "203.0.113.7:8070"
    tab.pair_btn.click()
    assert tab._pair_dialog.internet_url() is None
    tab._pair_dialog.close()

    tab.internet_cb.setChecked(True)
    tab.pair_btn.click()
    dialog = tab._pair_dialog
    assert dialog.internet_url() == f"http://203.0.113.7:8070/#pair={dialog.code}"
    assert not dialog.internet_qr_label.pixmap().isNull()
    dialog.close()
    assert saved(tmp_path)["web_internet_address"] == "203.0.113.7:8070"


def test_pair_window_text_stays_short(qtbot, tmp_path, monkeypatch):
    import wolfrat.app as app_module
    # A rented server: its own address is public, so its QR works from anywhere.
    monkeypatch.setattr(app_module, "_lan_ips", lambda: ["203.0.113.9"])
    tab, _server = make_tab(qtbot, tmp_path, {"web_token": "t", "web_allow_internet": True})
    tab.pair_btn.click()
    dialog = tab._pair_dialog
    assert dialog.qr_label.heading.text() == "Scan with the phone"
    assert dialog.away_label.text() == ""
    dialog.close()
    # A home PC behind a router, internet allowed: one short line for away from home.
    monkeypatch.setattr(app_module, "_lan_ips", lambda: ["192.168.1.20"])
    tab.pair_btn.click()
    dialog = tab._pair_dialog
    assert dialog.qr_label.heading.text() == "On this network"
    assert dialog.away_label.text().startswith("Away from home: open this PC's internet address")
    dialog.close()
    # This network only: nothing about away from home.
    tab.internet_cb.setChecked(False)
    tab.pair_btn.click()
    assert tab._pair_dialog.away_label.text() == ""
    tab._pair_dialog.close()


# --- contract v2: the address for phones, notification settings ---------------------


def type_phone_address(tab, text):
    tab.phone_address_input.setText(text)
    tab.phone_address_input.editingFinished.emit()


def test_the_address_for_phones_is_saved_and_used_by_the_pair_window(qtbot, tmp_path,
                                                                      monkeypatch):
    import wolfrat.app as app_module
    monkeypatch.setattr(app_module, "_lan_ips", lambda: ["192.168.1.20"])
    tab, server = make_tab(qtbot, tmp_path, {"web_token": "t", "web_allow_internet": True})
    assert tab.phone_address_input.text() == ""
    assert "web_phone_address" not in saved(tmp_path)

    type_phone_address(tab, "wolfrat.example.com")
    assert tab.phone_address_input.text() == "https://wolfrat.example.com"
    assert server.phone_address == "https://wolfrat.example.com"
    assert saved(tmp_path)["web_phone_address"] == "https://wolfrat.example.com"

    tab.pair_btn.click()
    dialog = tab._pair_dialog
    code = dialog.code
    assert dialog.main_url() == f"https://wolfrat.example.com/#pair={code}"
    assert dialog.url_label.text() == dialog.main_url()
    assert dialog.lan_url() == f"http://192.168.1.20:8070/#pair={code}"
    assert not dialog.qr_label.pixmap().isNull()
    assert dialog.qr_label.heading.text() == "Scan with the phone"
    assert dialog.away_label.text() == ""
    dialog.close()

    again, server_again = make_tab(qtbot, tmp_path)
    assert again.phone_address_input.text() == "https://wolfrat.example.com"
    assert server_again.phone_address == "https://wolfrat.example.com"


def test_a_wrong_address_for_phones_is_not_used(qtbot, tmp_path):
    tab, server = make_tab(qtbot, tmp_path, {"web_token": "t",
                                             "web_phone_address": "https://old.example.com"})
    messages = []
    tab._show_msg = messages.append
    type_phone_address(tab, "https://wolfrat.example.com/admin")
    assert messages and "doesn't look right" in messages[0]
    assert server.phone_address == "https://old.example.com"
    tab.internet_cb.setChecked(True)                   # a later save keeps the good one
    assert saved(tmp_path)["web_phone_address"] == "https://old.example.com"
    type_phone_address(tab, "")
    assert server.phone_address is None
    assert "web_phone_address" not in saved(tmp_path)


def test_an_https_internet_address_reaches_the_pair_window_with_its_scheme(qtbot, tmp_path,
                                                                           monkeypatch):
    import wolfrat.app as app_module
    monkeypatch.setattr(app_module, "_lan_ips", lambda: ["192.168.1.20"])
    tab, _server = make_tab(qtbot, tmp_path, {
        "web_token": "t", "web_allow_internet": True,
        "web_internet_address": "https://wolfrat.example.com"})
    tab.pair_btn.click()
    dialog = tab._pair_dialog
    assert dialog.internet_url() == f"https://wolfrat.example.com/#pair={dialog.code}"
    assert dialog.main_url() == dialog.lan_url()
    dialog.close()
    # The same address typed in the box: one QR code, not two.
    type_phone_address(tab, "https://wolfrat.example.com")
    tab.pair_btn.click()
    dialog = tab._pair_dialog
    assert dialog.main_url() == f"https://wolfrat.example.com/#pair={dialog.code}"
    assert dialog.internet_url() is None
    assert dialog.internet_qr_label.isHidden()
    dialog.close()
    assert saved(tmp_path)["web_internet_address"] == "https://wolfrat.example.com"


def test_notification_settings_and_subscriptions_stay_in_wolfrat_web_json(qtbot, tmp_path):
    push = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "p256dh": "p", "auth": "a"}
    tab, server = make_tab(qtbot, tmp_path, {
        "web_token": "t", "notify_first_joins": 4, "web_push_vapid": "v" * 43,
        "devices": [{"id": "abcdef01", "name": "Pixel", "key_sha256": "0" * 64,
                     "created": 1, "last_seen": 2, "last_ip": None, "push": push}],
    })
    assert server.notify_first_joins == 4
    assert server.push_config()["web_push_vapid"] == "v" * 43
    tab.internet_cb.setChecked(True)                   # any save writes them all back
    config = saved(tmp_path)
    assert config["notify_first_joins"] == 4
    assert config["web_push_vapid"] == "v" * 43
    assert config["devices"][0]["push"] == push


def test_a_new_install_saves_the_default_first_joins(qtbot, tmp_path):
    _tab, _server = make_tab(qtbot, tmp_path)
    config = saved(tmp_path)
    assert config["notify_first_joins"] == 2
    assert "web_push_vapid" not in config                # made on first need, not here


@pytest.mark.parametrize("seconds, text", [
    (None, "never"), (10, "just now"), (300, "5m ago"), (7200, "2h ago"), (172800, "2d ago"),
])
def test_last_used_text(seconds, text):
    now = 1_000_000
    stamp = None if seconds is None else now - seconds
    assert WebAdminTab._ago(stamp, now) == text
