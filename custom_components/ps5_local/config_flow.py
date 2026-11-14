"""Discover consoles, configure read-only monitoring and pair on demand."""

import asyncio
import base64
import binascii
import re
from urllib.parse import parse_qs, urlsplit

import voluptuous as vol
from aiohttp import ClientError
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from ._remoteplay import (
    DeviceStatus,
    PS5Error,
    login_url,
    register,
)
from ._remoteplay.const import OAUTH_REDIRECT_URI
from ._remoteplay.oauth import account_id_from_code

from .const import (
    CONF_CONSOLE_ID,
    CONF_PAIRING,
    CONF_PASSCODE,
    CONF_PIN,
    CONF_REDIRECT_URL,
    DOMAIN,
)
from .transport import (
    Console,
    ConsoleUnavailable,
    console_address,
    discover,
    read_console,
)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
# Sony's redirect carries a short-lived authorization code. Keep it masked
# and tell the browser to skip saved values. Pairing PINs are single use.
_REDIRECT = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="off")
)
_LINK_PIN = TextSelector(TextSelectorConfig(autocomplete="one-time-code"))


def validate_account_id(value: str) -> str:
    """Normalize a Remote Play account ID without logging the input."""
    try:
        raw = base64.b64decode(value.strip(), validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Invalid account ID") from None
    if len(raw) != 8:
        raise ValueError("Invalid account ID")
    return base64.b64encode(raw).decode("ascii")


def _suggest_psn_account_id(hass, entry) -> str | None:
    """Read account identifiers only, without making a Sony request."""
    if entry is not None:
        # Keep reauthentication tied to the original account when known.
        saved = entry.data.get(CONF_PAIRING) or {}
        account_id = saved.get("accountId")
        if isinstance(account_id, str):
            try:
                return validate_account_id(account_id)
            except ValueError:
                pass

    accounts = set()
    for psn_entry in hass.config_entries.async_entries(
        "playstation_network", include_ignore=False, include_disabled=False
    ):
        value = psn_entry.unique_id
        if value is None or not re.fullmatch(r"[0-9]{1,20}", value):
            continue
        number = int(value)
        if 0 < number < 2**64:
            # Sony stores the account ID as a decimal string. Remote Play
            # registration expects the same ID as eight little-endian bytes.
            accounts.add(number)
    if len(accounts) != 1:
        return None
    return base64.b64encode(accounts.pop().to_bytes(8, "little")).decode("ascii")


def sony_sign_in_code(value: str) -> str:
    """Read one authorization code from a full Sony redirect or bare input."""
    value = value.strip()
    if len(value) > 4096:
        raise ValueError("Invalid sign-in code")
    if "://" in value:
        actual = urlsplit(value)
        expected = urlsplit(OAUTH_REDIRECT_URI)
        if (
            (actual.scheme, actual.netloc, actual.path)
            != (expected.scheme, expected.netloc, expected.path)
            or actual.fragment
        ):
            raise ValueError("Invalid Sony redirect")
        codes = parse_qs(actual.query, keep_blank_values=True).get("code", [])
        if len(codes) != 1:
            raise ValueError("Expected one Sony sign-in code")
        value = codes[0]

    # Keep opaque codes intact and reject whole query strings and URL fragments.
    if not re.fullmatch(r"[A-Za-z0-9._~+/-]+={0,2}", value):
        raise ValueError("Invalid sign-in code")
    return value


class PS5ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Use the console's DDP ID for new entries and all reconfiguration."""

    VERSION = 1

    def __init__(self) -> None:
        self._console: Console | None = None
        self._pairing_account: str | None = None
        self._candidates: dict[str, Console] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PS5OptionsFlow()

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["discover", "manual"])

    async def async_step_discover(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            selected = self._candidates.get(user_input[CONF_CONSOLE_ID])
            if selected is not None:
                return await self._select(selected)
        try:
            configured = {entry.unique_id for entry in self._async_current_entries()}
            self._candidates = {
                console.identifier: console
                for console in await discover()
                if console.identifier not in configured
            }
        except OSError:
            return self.async_abort(reason="cannot_connect")
        if not self._candidates:
            return self.async_abort(reason="no_discovered_devices")
        return self.async_show_form(
            step_id="discover",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONSOLE_ID): SelectSelector(
                        {
                            "options": [
                                {
                                    "value": identifier,
                                    "label": f"{console.name} ({console.host})",
                                }
                                for identifier, console in self._candidates.items()
                            ]
                        }
                    )
                }
            ),
        )

    async def _select(self, console: Console) -> ConfigFlowResult:
        await self.async_set_unique_id(console.identifier)
        self._abort_if_unique_id_configured()
        self._console = console
        return await self.async_step_setup()

    async def async_step_setup(self, user_input=None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="setup", menu_options=["monitor", "pair"])

    async def async_step_manual(self, user_input=None) -> ConfigFlowResult:
        return await self._host_form("manual", user_input)

    async def async_step_reconfigure(self, user_input=None) -> ConfigFlowResult:
        return await self._host_form("reconfigure", user_input)

    async def _host_form(self, step: str, user_input) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry() if step == "reconfigure" else None
        errors = {}
        if user_input is not None:
            try:
                host = console_address(user_input[CONF_HOST])
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
            else:
                try:
                    console = await read_console(host)
                except (ConsoleUnavailable, OSError, TimeoutError):
                    errors["base"] = "cannot_connect"
                else:
                    if entry is None:
                        return await self._select(console)
                    if console.identifier != entry.unique_id:
                        errors["base"] = "wrong_console"
                    else:
                        return self.async_update_reload_and_abort(
                            entry, data_updates={CONF_HOST: host}
                        )
        schema = vol.Schema({vol.Required(CONF_HOST): str})
        if entry is not None:
            schema = self.add_suggested_values_to_schema(schema, entry.data)
        return self.async_show_form(step_id=step, data_schema=schema, errors=errors)

    async def async_step_monitor(self, user_input=None) -> ConfigFlowResult:
        return self._finish()

    async def async_step_reauth(self, entry_data) -> ConfigFlowResult:
        # The same flow also lets a monitor-only entry add controls on demand.
        return await self.async_step_pair()

    async def async_step_pair(self, user_input=None) -> ConfigFlowResult:
        self._pairing_account = _suggest_psn_account_id(self.hass, self._pairing_entry())
        if self._pairing_account is None:
            return await self.async_step_pair_sony()
        # Home Assistant's native form has one submit action. Use its menu to
        # offer a different account without placing hidden IDs in the form.
        return self.async_show_menu(
            step_id="pair", menu_options=["pair_known", "pair_sony"]
        )

    async def async_step_pair_known(self, user_input=None) -> ConfigFlowResult:
        return await self._pair_form("pair_known", user_input)

    async def async_step_pair_sony(self, user_input=None) -> ConfigFlowResult:
        return await self._pair_form("pair_sony", user_input)

    def _pairing_entry(self):
        if self.source == config_entries.SOURCE_REAUTH:
            return self._get_reauth_entry()
        return None

    async def _pair_form(self, step, user_input) -> ConfigFlowResult:
        entry = self._pairing_entry()
        errors = {}
        manual = step == "pair_sony"
        if user_input is not None:
            try:
                credentials = await self._pair(user_input, entry, manual=manual)
            except PairingError as error:
                errors[error.field] = error.key
            else:
                # Sign-in codes and PINs are single-use. Persist only the
                # fields needed for wake and subsequent Remote Play sessions.
                pairing = {
                    "accountId": credentials.account_id,
                    "user-credential": credentials.user_credential,
                    "registration": {
                        "PS5-RegistKey": credentials.regist_key,
                        "RP-Key": credentials.rp_key.hex(),
                    },
                }
                if entry is not None:
                    return self.async_update_reload_and_abort(
                        entry, data_updates={CONF_PAIRING: pairing}
                    )
                return self._finish(pairing)
        fields = {}
        placeholders = None
        if manual:
            fields[vol.Required(CONF_REDIRECT_URL)] = _REDIRECT
            placeholders = {"login_url": login_url(), "redirect_uri": OAUTH_REDIRECT_URI}
        fields[vol.Required(CONF_PIN)] = _LINK_PIN
        return self.async_show_form(
            step_id=step,
            data_schema=vol.Schema(fields),
            description_placeholders=placeholders,
            errors=errors,
        )

    async def _pair(self, user_input, entry, *, manual):
        pin = user_input[CONF_PIN].strip()
        if not re.fullmatch(r"[0-9]{8}", pin):
            raise PairingError(CONF_PIN, "invalid_pin")
        account_id = self._pairing_account
        code = None
        if manual:
            sign_in = user_input[CONF_REDIRECT_URL].strip()
            try:
                if sign_in.startswith("id:"):
                    # An explicit prefix keeps a direct Remote Play ID
                    # distinguishable from an opaque Sony authorization code.
                    account_id = validate_account_id(sign_in.removeprefix("id:"))
                else:
                    code = sony_sign_in_code(sign_in)
            except ValueError:
                raise PairingError(CONF_REDIRECT_URL, "invalid_sign_in") from None

        host = entry.data[CONF_HOST] if entry else self._console.host
        identifier = entry.unique_id if entry else self._console.identifier
        try:
            console = await read_console(host, identifier)
        except (ConsoleUnavailable, OSError, TimeoutError):
            raise PairingError("base", "cannot_connect") from None
        if console.status != DeviceStatus.AWAKE:
            raise PairingError("base", "console_asleep")

        if code is not None:
            # Check that the console is available before spending the one-use
            # code. The shared HA session talks only to Sony's OAuth API.
            try:
                async with asyncio.timeout(12):
                    account_id = await account_id_from_code(
                        code, async_get_clientsession(self.hass)
                    )
            except (ClientError, PS5Error, ValueError, OSError, TimeoutError):
                raise PairingError(CONF_REDIRECT_URL, "invalid_account") from None

        try:
            async with asyncio.timeout(25):
                return await register(host, account_id, pin, timeout=20)
        except (PS5Error, ValueError, OSError, TimeoutError):
            error = (
                "pairing_failed_after_sign_in" if code is not None else "pairing_failed"
            )
            raise PairingError("base", error) from None

    def _finish(self, pairing=None) -> ConfigFlowResult:
        console = self._console
        data = {CONF_HOST: console.host, CONF_CONSOLE_ID: console.identifier}
        if pairing is not None:
            data[CONF_PAIRING] = pairing
        return self.async_create_entry(title=console.name, data=data)


class PairingError(Exception):
    """Translate an error without propagating sign-in data."""

    def __init__(self, field: str, key: str) -> None:
        self.field = field
        self.key = key


class PS5OptionsFlow(config_entries.OptionsFlow):
    """Manage the console profile passcode or open the pairing flow."""

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init", menu_options=["passcode", "start_pairing"]
        )

    async def async_step_start_pairing(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self.config_entry.async_start_reauth(self.hass)
            return self.async_create_entry(data=self.config_entry.options)
        return self.async_show_form(step_id="start_pairing")

    async def async_step_passcode(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            value = user_input.get(CONF_PASSCODE, "").strip()
            if not value or re.fullmatch(r"[0-9]{4}", value):
                return self.async_create_entry(
                    data={**self.config_entry.options, CONF_PASSCODE: value}
                )
            errors[CONF_PASSCODE] = "invalid_passcode"
        # Never return the existing value in a form, even in a masked field.
        return self.async_show_form(
            step_id="passcode",
            data_schema=vol.Schema({vol.Optional(CONF_PASSCODE): _PASSWORD}),
            errors=errors,
        )
