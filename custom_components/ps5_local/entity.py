"""Shared device registry and entity identity."""

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PS5Coordinator


class PS5Entity(CoordinatorEntity[PS5Coordinator]):
    """Identify the console by hardware ID, independent of its current IP."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PS5Coordinator, title: str, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.identifier}_{key}"
        self._attr_translation_key = key
        snapshot = coordinator.data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.identifier)},
            connections={(CONNECTION_NETWORK_MAC, coordinator.identifier)},
            manufacturer="Sony",
            model="PlayStation 5",
            name=title,
            sw_version=snapshot.firmware if snapshot else None,
        )
