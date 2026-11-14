"""Coordinate polling and confirmed power commands for one console."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from ._remoteplay import (
    Credentials,
    DeviceStatus,
    PasscodeMismatch,
    PasscodeRequired,
    PS5Error,
    RemotePlaySession,
    wake,
)

from .const import CONF_CONSOLE_ID, CONF_PAIRING, CONF_PASSCODE, DOMAIN, POLL_INTERVAL
from .transport import Console, ConsoleUnavailable, read_console

_LOGGER = logging.getLogger(__name__)


class CommandError(Exception):
    """A safe error key for the Home Assistant UI."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


async def set_power(
    target: DeviceStatus,
    read: Callable[[], Awaitable[Console]],
    send: Callable[[], Awaitable[None]],
    *,
    timeout: float = 40,
    interval: float = 1,
) -> Console:
    """Send once after verifying identity and wait for the actual state."""
    try:
        async with asyncio.timeout(timeout):
            current = await read()
            if current.status == target:
                return current
            await send()
            while True:
                try:
                    current = await read()
                    if current.status == target:
                        return current
                except ConsoleUnavailable:
                    # A console may disappear briefly during the transition.
                    pass
                await asyncio.sleep(interval)
    except TimeoutError:
        raise CommandError("power_unconfirmed") from None
    except (PasscodeRequired, PasscodeMismatch):
        raise CommandError("passcode_required") from None
    except (ConsoleUnavailable, OSError):
        raise CommandError("cannot_connect") from None
    except (PS5Error, KeyError, ValueError):
        raise CommandError("command_failed") from None


class PS5Coordinator(DataUpdateCoordinator[Console]):
    """Serialize polls and commands and never route commands to another PS5."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=POLL_INTERVAL,
            always_update=False,
        )
        self.host = entry.data[CONF_HOST]
        self.identifier = entry.data[CONF_CONSOLE_ID]
        self.credentials = (
            Credentials.from_dict(entry.data[CONF_PAIRING])
            if entry.data.get(CONF_PAIRING)
            else None
        )
        self.passcode = entry.options.get(CONF_PASSCODE) or None
        self._lock = asyncio.Lock()

    async def _read(self) -> Console:
        return await read_console(self.host, self.identifier)

    async def _async_update_data(self) -> Console:
        async with self._lock:
            try:
                return await self._read()
            except (ConsoleUnavailable, OSError, TimeoutError):
                raise UpdateFailed("No verified PS5 discovery reply") from None

    async def async_set_power(self, target: DeviceStatus) -> None:
        credentials = self.credentials
        if credentials is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="pairing_required"
            )

        async def send() -> None:
            if target == DeviceStatus.AWAKE:
                await wake(self.host, credentials)
            else:
                await self._send_session_command("standby")

        try:
            async with self._lock:
                self.async_set_updated_data(await set_power(target, self._read, send))
        except CommandError as error:
            # Refresh after failure because a command may already have reached the PS5.
            await self.async_request_refresh()
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key=error.key
            ) from None

    async def _send_session_command(self, command: str) -> None:
        """Open a short-lived control session on the verified console."""
        session = await RemotePlaySession.open(
            self.host, self.credentials, passcode=self.passcode, timeout=12
        )
        try:
            if command == "standby":
                await session.standby(timeout=6)
            else:
                # The protocol library has no public Home operation. The
                # pinned session handles authentication and frame encryption.
                # Frame 0x0014 carries 00 ff and fourteen zero bytes.
                await session._send(0x0014, b"\x00\xff" + bytes(14))
        finally:
            try:
                async with asyncio.timeout(2):
                    await session.close()
            except TimeoutError:
                await session.close(abort=True)

    async def async_go_home(self) -> None:
        """Send Home only to the awake, paired console."""
        if self.credentials is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="pairing_required"
            )
        try:
            async with self._lock, asyncio.timeout(20):
                current = await self._read()
                if current.status != DeviceStatus.AWAKE:
                    raise CommandError("console_asleep")
                await self._send_session_command("home")
        except CommandError as error:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key=error.key
            ) from None
        except (PasscodeRequired, PasscodeMismatch):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="passcode_required"
            ) from None
        except (
            ConsoleUnavailable,
            OSError,
            TimeoutError,
            PS5Error,
            KeyError,
            ValueError,
        ):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="command_failed"
            ) from None
