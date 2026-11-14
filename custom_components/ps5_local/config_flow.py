"""Discover consoles, configure read-only monitoring and pair on demand."""

import asyncio
import base64
import binascii
import re
from urllib.parse import urlsplit

import voluptuous as vol
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
    account_id_from_redirect,
    login_url,
    register,
)
from ._remoteplay.const import OAUTH_REDIRECT_URI

from .const import (
    CONF_ACCOUNT_ID,
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


def validate_account_id(value: str) -> str:
    """Normalize a Remote Play account ID without logging the input."""
    try:
        raw = base64.b64decode(value.strip(), validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Invalid account ID") from None
    if len(raw) != 8:
        raise ValueError("Invalid account ID")
    return base64.b64encode(raw).decode("ascii")


def validate_redirect(value: str) -> str:
    """Only accept the exact Sony redirect origin and path."""
    value = value.strip()
    actual = urlsplit(value)
    expected = urlsplit(OAUTH_REDIRECT_URI)
    if (
        (actual.scheme, actual.netloc, actual.path)
        != (expected.scheme, expected.netloc, expected.path)
        or not actual.query
        or actual.fragment
        or len(value) > 4096
    ):
        raise ValueError("Invalid redirect")
    return value


class PS5ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Use the console's DDP ID for new entries and all reconfiguration."""

    VERSION = 1

    def __init__(self) -> None:
        self._console: Console | None = None
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
        entry = (
            self._get_reauth_entry()
            if self.source == config_entries.SOURCE_REAUTH
            else None
        )
        errors = {}
        if user_input is not None:
            try:
                credentials = await self._pair(user_input, entry)
            except PairingError as error:
                errors[error.field] = error.key
            else:
                # Persist only the fields used by wake and session setup.
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
        return self.async_show_form(
            step_id="pair",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_ACCOUNT_ID): _PASSWORD,
                    vol.Optional(CONF_REDIRECT_URL): _PASSWORD,
                    vol.Required(CONF_PIN): _PASSWORD,
                }
            ),
            description_placeholders={"login_url": login_url()},
            errors=errors,
        )

    async def _pair(self, user_input, entry):
        account = user_input.get(CONF_ACCOUNT_ID, "").strip()
        redirect = user_input.get(CONF_REDIRECT_URL, "").strip()
        pin = user_input[CONF_PIN].strip()
        if not re.fullmatch(r"[0-9]{8}", pin):
            raise PairingError(CONF_PIN, "invalid_pin")
        if bool(account) == bool(redirect):
            raise PairingError("base", "choose_account_method")
        host = entry.data[CONF_HOST] if entry else self._console.host
        identifier = entry.unique_id if entry else self._console.identifier
        try:
            console = await read_console(host, identifier)
        except (ConsoleUnavailable, OSError, TimeoutError):
            raise PairingError("base", "cannot_connect") from None
        if console.status != DeviceStatus.AWAKE:
            raise PairingError("base", "console_asleep")
        try:
            if account:
                account_id = validate_account_id(account)
            else:
                url = validate_redirect(redirect)
                async with asyncio.timeout(12):
                    account_id = await account_id_from_redirect(
                        url, async_get_clientsession(self.hass)
                    )
        except (PS5Error, ValueError, OSError, TimeoutError):
            raise PairingError("base", "invalid_account") from None
        try:
            async with asyncio.timeout(25):
                return await register(host, account_id, pin, timeout=20)
        except (PS5Error, ValueError, OSError, TimeoutError):
            raise PairingError("base", "pairing_failed") from None

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
