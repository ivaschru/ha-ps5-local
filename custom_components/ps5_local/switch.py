"""Wake a paired PS5 or send it into rest mode."""

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from ps5_remoteplay import DeviceStatus

from .entity import PS5Entity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([PS5Power(entry.runtime_data, entry.title, "power")])


class PS5Power(PS5Entity, SwitchEntity):
    """The switch follows confirmed console state, including external changes."""

    _attr_device_class = SwitchDeviceClass.SWITCH

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return data.status == DeviceStatus.AWAKE if data else None

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_power(DeviceStatus.AWAKE)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_power(DeviceStatus.STANDBY)
