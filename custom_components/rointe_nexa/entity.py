"""Shared entity base for Rointe Nexa."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import RointeDevice
from .const import DOMAIN
from .coordinator import RointeCoordinator


class RointeEntity(CoordinatorEntity[RointeCoordinator]):
    """Base for everything this integration exposes."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: RointeCoordinator, serial: str) -> None:
        super().__init__(coordinator)
        self._serial = serial

    @property
    def device(self) -> RointeDevice | None:
        return self.coordinator.data.get(self._serial) if self.coordinator.data else None

    @property
    def available(self) -> bool:
        device = self.device
        return bool(super().available and device and device.online)

    @property
    def device_info(self) -> DeviceInfo:
        device = self.device
        name = device.name if device else self._serial
        firmware = device.firmware if device else {}
        return DeviceInfo(
            identifiers={(DOMAIN, self._serial)},
            name=name,
            manufacturer="Rointe",
            model=device.model if device else "Rointe heater",
            sw_version=str(firmware.get("firmware_version") or ""),
            suggested_area=device.zone_name if device and device.zone_name else None,
            serial_number=self._serial,
        )

    def read(self, key: str, default: Any = None) -> Any:
        """Read a field off the device.

        Deliberately NOT called `value`: NumberEntity carries a legacy `value`
        property that this would shadow, which makes NumberEntity.state return
        a bound method and sends Home Assistant's repr into infinite
        recursion. The failure surfaces as a RecursionError with no frame of
        ours in it, so it is worth the odd name.
        """
        device = self.device
        return device.get(key, default) if device else default

    def read_commanded(self, key: str, default: Any = None) -> Any:
        """Read a writable setting - the commanded value, not the readback."""
        device = self.device
        return device.get_commanded(key, default) if device else default

    async def command(self, payload: dict[str, Any]) -> None:
        device = self.device
        if device is None:
            return
        await self.coordinator.async_command(device, payload)
