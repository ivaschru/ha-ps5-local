"""Request the PS5 home screen from Home Assistant."""

from homeassistant.components.button import ButtonEntity
from ._remoteplay import DeviceStatus

from .entity import PS5Entity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([PS5Home(entry.runtime_data, entry.title, "home")])


class PS5Home(PS5Entity, ButtonEntity):
    """Optional control without an independently observable completion state."""

    _attr_entity_registry_enabled_default = False

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return (
            super().available and data is not None and data.status == DeviceStatus.AWAKE
        )

    async def async_press(self) -> None:
        await self.coordinator.async_go_home()
