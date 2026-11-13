"""Expose an allowlist of non-identifying troubleshooting data."""

from homeassistant.core import HomeAssistant

from . import PS5Entry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PS5Entry
) -> dict:
    """Keep address, account, pairing data and activity out of exports."""
    coordinator = entry.runtime_data
    data = coordinator.data if coordinator.last_update_success else None
    return {
        "paired": coordinator.credentials is not None,
        "reachable": data is not None,
        "power_state": data.status.value if data else None,
        "firmware": data.firmware
        if data and (data.firmware or "").isascii() and (data.firmware or "").isdigit()
        else None,
    }
