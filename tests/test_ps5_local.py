"""Exercise protocol parsing, state confirmation and real HA entry setup."""

import asyncio
import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from homeassistant import bootstrap, loader
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from custom_components.ps5_local._remoteplay import DeviceStatus

from custom_components.ps5_local import config_flow, coordinator
from custom_components.ps5_local.transport import ConsoleUnavailable, parse_reply

HOST = "192.0.2.25"
IDENTIFIER = "020000000001"
BASE = b"host-type:PS5\nhost-id:020000000001\nhost-name:Test Console\nsystem-version:1234\n"
AWAKE = parse_reply(b"HTTP/1.1 200 Ok\n" + BASE, HOST)
REST = parse_reply(b"HTTP/1.1 620 Server Standby\n" + BASE, HOST)


def test_reply_and_activity():
    reply = parse_reply(
        b"HTTP/1.1 200 Ok\r\n" + BASE + b"running-app-name:Game: Part II\n", HOST
    )
    assert reply.identifier == IDENTIFIER
    assert reply.activity == "Game: Part II"
    assert parse_reply(b"HTTP/1.1 500 Error\n" + BASE, HOST) is None
    assert (
        parse_reply(b"HTTP/1.1 200 Ok\n" + BASE.replace(b"PS5", b"PS4"), HOST) is None
    )
    assert (
        parse_reply(
            b"HTTP/1.1 620 Standby\n" + BASE + b"running-app-name:Stale", HOST
        ).activity
        is None
    )


@pytest.mark.asyncio
async def test_confirmed_power_and_idempotence():
    read = AsyncMock(side_effect=[AWAKE, ConsoleUnavailable(), REST])
    send = AsyncMock()
    assert (
        await coordinator.set_power(DeviceStatus.STANDBY, read, send, interval=0)
        == REST
    )
    send.assert_awaited_once()
    send.reset_mock()
    assert (
        await coordinator.set_power(
            DeviceStatus.STANDBY, AsyncMock(return_value=REST), send
        )
        == REST
    )
    send.assert_not_called()


@pytest.mark.asyncio
async def test_unverified_identity_or_timeout():
    send = AsyncMock()
    with pytest.raises(coordinator.CommandError, match="cannot_connect"):
        await coordinator.set_power(
            DeviceStatus.AWAKE, AsyncMock(side_effect=ConsoleUnavailable), send
        )
    send.assert_not_called()
    with pytest.raises(coordinator.CommandError, match="power_unconfirmed"):
        await coordinator.set_power(
            DeviceStatus.AWAKE,
            AsyncMock(return_value=REST),
            send,
            timeout=0.02,
            interval=0.005,
        )
    send.assert_awaited_once()


def test_account_inputs():
    assert config_flow.validate_account_id("AQAAAAAAAAA=") == "AQAAAAAAAAA="
    with pytest.raises(ValueError):
        config_flow.validate_account_id("123")
    uri = config_flow.OAUTH_REDIRECT_URI
    assert config_flow.sony_sign_in_code("  demo-code-123  ") == "demo-code-123"
    assert config_flow.sony_sign_in_code(f"{uri}?code=demo%2Babc%3D&cid=demo") == "demo+abc="
    for invalid in (
        "code=demo",
        "https://remoteplay.dl.playstation.net.example/remoteplay/redirect?code=demo",
        f"{uri}?cid=demo",
        f"{uri}?code=&code=demo",
        f"{uri}?code=demo#fragment",
    ):
        with pytest.raises(ValueError):
            config_flow.sony_sign_in_code(invalid)


@pytest.mark.asyncio
async def test_ha_translation_fallback(tmp_path):
    """Home Assistant selects Russian and falls back to English for other locales."""
    from homeassistant.helpers.translation import async_get_translations

    source = Path(__file__).resolve().parents[1] / "custom_components" / "ps5_local"
    shutil.copytree(
        source,
        tmp_path / "custom_components" / "ps5_local",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    hass = HomeAssistant(str(tmp_path))
    loader.async_setup(hass)
    try:
        key = "component.ps5_local.config.step.pair.title"
        for language, title in (
            ("ru", "Сопряжение Remote Play"),
            ("en", "Pair with Remote Play"),
            ("de", "Pair with Remote Play"),
        ):
            strings = await async_get_translations(
                hass, language, "config", ["ps5_local"]
            )
            assert strings[key] == title
    finally:
        await asyncio.wait_for(hass.async_stop(force=True), 10)


@pytest.mark.asyncio
async def test_ha_entry_setup(tmp_path, monkeypatch):
    """Drive the registered config flow and the actual sensor platform."""
    source = Path(__file__).resolve().parents[1] / "custom_components" / "ps5_local"
    shutil.copytree(
        source,
        tmp_path / "custom_components" / "ps5_local",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    hass = HomeAssistant(str(tmp_path))
    loader.async_setup(hass)
    hass.config_entries = ConfigEntries(hass, {})
    assert await bootstrap.async_load_base_functionality(hass)
    monkeypatch.setattr(config_flow, "read_console", AsyncMock(return_value=AWAKE))
    read = AsyncMock(return_value=AWAKE)
    monkeypatch.setattr(coordinator, "read_console", read)
    try:
        assert await async_setup_component(hass, "sensor", {})
        result = await hass.config_entries.flow.async_init(
            "ps5_local", context={"source": "user"}
        )
        flow_id = result["flow_id"]
        result = await hass.config_entries.flow.async_configure(
            flow_id, {"next_step_id": "manual"}
        )
        assert result["step_id"] == "manual"
        result = await hass.config_entries.flow.async_configure(flow_id, {"host": HOST})
        assert result["step_id"] == "setup"
        result = await hass.config_entries.flow.async_configure(
            flow_id, {"next_step_id": "monitor"}
        )
        assert result["type"] == "create_entry"
        await hass.async_block_till_done()
        entry = result["result"]
        assert entry.unique_id == IDENTIFIER
        assert hass.states.get("sensor.test_console_power_state").state == "awake"
        assert hass.states.get("sensor.test_console_game_or_app").state == "unknown"
        assert not hass.states.async_entity_ids("switch")

        read.side_effect = ConsoleUnavailable
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get("sensor.test_console_power_state").state == "unavailable"

        assert await hass.config_entries.async_unload(entry.entry_id)
        assert hass.states.get("sensor.test_console_power_state").state == "unavailable"
    finally:
        await asyncio.wait_for(hass.async_stop(force=True), 10)


@pytest.mark.asyncio
async def test_pairing_form_suggests_unambiguous_psn_account(tmp_path, monkeypatch):
    """Check the actual HA form schema without starting any Sony session."""
    from types import SimpleNamespace

    hass = HomeAssistant(str(tmp_path))
    flow = config_flow.PS5ConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    accounts = [SimpleNamespace(unique_id="258")]
    hass.config_entries = ConfigEntries(hass, {})

    def entries(domain, *, include_ignore, include_disabled):
        assert domain == "playstation_network"
        assert not include_ignore and not include_disabled
        return accounts

    monkeypatch.setattr(hass.config_entries, "async_entries", entries)
    try:
        choice = await flow.async_step_pair()
        assert choice["menu_options"] == ["pair_known", "pair_sony"]
        known = await flow.async_step_pair_known()
        assert list(map(str, known["data_schema"].schema)) == ["pin"]
        assert flow._pairing_account == "AgEAAAAAAAA="

        accounts.append(SimpleNamespace(unique_id="259"))
        form = await flow.async_step_pair()
        assert form["step_id"] == "pair_sony"
        for field, field_type, autocomplete in (
            ("redirect_url", "password", "off"),
            ("pin", "text", "one-time-code"),
        ):
            config = form["data_schema"].schema[field].serialize()["selector"]["text"]
            assert config.get("type", "text") == field_type
            assert config["autocomplete"] == autocomplete
        assert flow._pairing_account is None
    finally:
        await asyncio.wait_for(hass.async_stop(force=True), 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("use_url", [False, True])
async def test_sony_pairing_exchanges_only_the_code(tmp_path, monkeypatch, use_url):
    """Exercise both inputs without a live Sony account or console."""
    hass = HomeAssistant(str(tmp_path))
    flow = config_flow.PS5ConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    flow._console = AWAKE
    exchange = AsyncMock(return_value="AQAAAAAAAAA=")
    from custom_components.ps5_local._remoteplay import Credentials

    register = AsyncMock(
        return_value=Credentials(
            "AQAAAAAAAAA=", "123", {"PS5-RegistKey": "00" * 16, "RP-Key": "11" * 16}
        )
    )
    read = AsyncMock(return_value=REST)
    monkeypatch.setattr(config_flow, "read_console", read)
    monkeypatch.setattr(config_flow, "account_id_from_code", exchange)
    monkeypatch.setattr(config_flow, "register", register)
    monkeypatch.setattr(config_flow, "async_get_clientsession", lambda hass: None)
    code = "demo-code-123"
    value = f"{config_flow.OAUTH_REDIRECT_URI}?code={code}&cid=123" if use_url else code
    data = {"redirect_url": value, "pin": "01234567"}
    try:
        form = await flow.async_step_pair_sony(data)
        assert form["errors"] == {"base": "console_asleep"}
        exchange.assert_not_awaited()
        read.return_value = AWAKE
        await flow.async_step_pair_sony(data)
        exchange.assert_awaited_once_with(code, None)
        register.assert_awaited_once_with(HOST, "AQAAAAAAAAA=", "01234567", timeout=20)
    finally:
        await asyncio.wait_for(hass.async_stop(force=True), 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("use_known_account", [False, True])
async def test_ha_pairing_and_switch(tmp_path, monkeypatch, use_known_account):
    """Use a fake pairing at the physical boundary and the real HA switch service."""
    from homeassistant.helpers import entity_registry
    from custom_components.ps5_local._remoteplay import Credentials

    hass = HomeAssistant(str(tmp_path))
    loader.async_setup(hass)
    hass.config_entries = ConfigEntries(hass, {})
    assert await bootstrap.async_load_base_functionality(hass)
    read = AsyncMock(return_value=AWAKE)
    register = AsyncMock(
        return_value=Credentials(
            "AQAAAAAAAAA=", "123", {"PS5-RegistKey": "00" * 16, "RP-Key": "11" * 16}
        )
    )
    monkeypatch.setattr(config_flow, "read_console", read)
    monkeypatch.setattr(config_flow, "register", register)
    monkeypatch.setattr(coordinator, "read_console", read)
    if use_known_account:
        from types import SimpleNamespace

        original_entries = hass.config_entries.async_entries

        def entries(domain=None, **kwargs):
            if domain == "playstation_network":
                return [SimpleNamespace(unique_id="1")]
            return original_entries(domain, **kwargs)

        monkeypatch.setattr(hass.config_entries, "async_entries", entries)
    try:
        result = await hass.config_entries.flow.async_init(
            "ps5_local", context={"source": "user"}
        )
        flow_id = result["flow_id"]
        for data in (
            {"next_step_id": "manual"},
            {"host": HOST},
            {"next_step_id": "pair"},
        ):
            result = await hass.config_entries.flow.async_configure(flow_id, data)
        if use_known_account:
            assert result["step_id"] == "pair"
            result = await hass.config_entries.flow.async_configure(
                flow_id, {"next_step_id": "pair_known"}
            )
        assert result["step_id"] == ("pair_known" if use_known_account else "pair_sony")
        data = {"pin": "01234567"}
        if not use_known_account:
            data["redirect_url"] = "id:AQAAAAAAAAA="
        result = await hass.config_entries.flow.async_configure(flow_id, data)
        assert result["type"] == "create_entry"
        entry = result["result"]
        assert set(entry.data) == {"host", "console_id", "pairing"}
        assert register.await_args.args == (HOST, "AQAAAAAAAAA=", "01234567")
        await hass.async_block_till_done()
        registry = entity_registry.async_get(hass)
        power = registry.async_get_entity_id(
            "switch", "ps5_local", IDENTIFIER + "_power"
        )
        button = registry.async_get_entity_id(
            "button", "ps5_local", IDENTIFIER + "_home"
        )
        assert power and hass.states.get(power).state == "on"
        assert button and registry.async_get(button).disabled_by is not None

        standby = AsyncMock(
            side_effect=lambda **kwargs: setattr(read, "return_value", REST)
        )
        session = AsyncMock()
        session.standby = standby
        session_open = AsyncMock(return_value=session)
        monkeypatch.setattr(coordinator.RemotePlaySession, "open", session_open)
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": power}, blocking=True
        )
        assert hass.states.get(power).state == "off"
        standby.assert_awaited_once()
        session.close.assert_awaited_once()

        read.return_value = AWAKE
        await entry.runtime_data.async_go_home()
        session._send.assert_awaited_once_with(0x0014, bytes([0, 255]) + bytes(14))
        assert await hass.config_entries.async_unload(entry.entry_id)
    finally:
        await asyncio.wait_for(hass.async_stop(force=True), 10)


@pytest.mark.asyncio
async def test_udp_transport_ignores_other_console(monkeypatch):
    """Exercise the actual UDP transport with a mismatched reply first."""
    from custom_components.ps5_local import transport

    class Responder(asyncio.DatagramProtocol):
        def connection_made(self, udp):
            self.udp = udp

        def datagram_received(self, data, address):
            assert data.startswith(b"SRCH ")
            response = b"HTTP/1.1 200 Ok\n" + BASE
            self.udp.sendto(response.replace(b"020000000001", b"020000000002"), address)
            self.udp.sendto(response, address)

    loop = asyncio.get_running_loop()
    server, _ = await loop.create_datagram_endpoint(
        Responder, local_addr=("127.0.0.1", 0)
    )
    try:
        monkeypatch.setattr(transport, "_PORT", server.get_extra_info("sockname")[1])
        reply = await transport.read_console("127.0.0.1", IDENTIFIER)
        assert reply.identifier == IDENTIFIER
    finally:
        server.close()


@pytest.mark.asyncio
async def test_diagnostics_use_allowlist():
    from types import SimpleNamespace

    from custom_components.ps5_local.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = SimpleNamespace(
        data={"host": HOST, "pairing": {"sensitive": "private"}},
        options={"passcode": "private"},
        runtime_data=SimpleNamespace(
            credentials=object(), data=AWAKE, last_update_success=True
        ),
    )
    assert await async_get_config_entry_diagnostics(None, entry) == {
        "paired": True,
        "reachable": True,
        "power_state": "AWAKE",
        "firmware": "1234",
    }
