"""Expose a PlayStation 5 as native Home Assistant entities."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MAJOR_VERSION, MINOR_VERSION, Platform
from homeassistant.core import HomeAssistant

from .coordinator import PS5Coordinator

type PS5Entry = ConfigEntry[PS5Coordinator]

# Core 2026.10 requires boolean setup and unload results. Starting with
# 2026.11, native success is None and an unload failure raises.
_LEGACY_ENTRY_RESULT = (MAJOR_VERSION, MINOR_VERSION) < (2026, 11)


def _platforms(coordinator: PS5Coordinator) -> list[Platform]:
    if coordinator.credentials is not None:
        return [Platform.SENSOR, Platform.SWITCH, Platform.BUTTON]
    return [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: PS5Entry) -> bool | None:
    """Restore entities even when the console is temporarily unreachable."""
    coordinator = PS5Coordinator(hass, entry)
    entry.runtime_data = coordinator
    await coordinator.async_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, _platforms(coordinator))
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True if _LEGACY_ENTRY_RESULT else None


async def _async_reload(hass: HomeAssistant, entry: PS5Entry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PS5Entry) -> bool | None:
    platforms = _platforms(entry.runtime_data)
    if _LEGACY_ENTRY_RESULT:
        return await hass.config_entries.async_unload_platforms(entry, platforms)
    await hass.config_entries.async_unload_platforms_or_raise(entry, platforms)
    return None
