"""Report locally observed power state and activity."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from ._remoteplay import DeviceStatus

from .entity import PS5Entity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Add sensors with stable registry IDs regardless of pairing state."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            PS5Status(coordinator, entry.title, "power_state"),
            PS5Activity(coordinator, entry.title, "activity"),
            PS5TitleID(coordinator, entry.title, "title_id"),
        ]
    )


class PS5Status(PS5Entity, SensorEntity):
    """Expose the distinction between awake and network-enabled rest mode."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["awake", "rest_mode"]

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        if data is None:
            return None
        return "awake" if data.status == DeviceStatus.AWAKE else "rest_mode"


class PS5Activity(PS5Entity, SensorEntity):
    """Show the game or app name only when included in the console reply."""

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        return data.activity[:255] if data and data.activity else None


class PS5TitleID(PS5Entity, SensorEntity):
    """Make the optional title identifier available for exact automation rules."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        return data.title_id[:255] if data and data.title_id else None
