"""Binary sensors for a Rointe heater."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import RointeCoordinator
from .entity import RointeEntity


@dataclass(frozen=True, kw_only=True)
class RointeBinaryDescription(BinarySensorEntityDescription):
    """A binary sensor and how to derive it."""

    value_fn: Callable[[Any], bool | None]


def _bool(key: str) -> Callable[[Any], bool | None]:
    def _get(device: Any) -> bool | None:
        value = device.get(key)
        return bool(value) if value is not None else None

    return _get


BINARY_SENSORS: tuple[RointeBinaryDescription, ...] = (
    RointeBinaryDescription(
        key="charging",
        translation_key="charging",
        name="Charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=_bool("charging"),
    ),
    RointeBinaryDescription(
        key="windows_open_status",
        translation_key="window_open",
        name="Open window detected",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=_bool("windows_open_status"),
    ),
    RointeBinaryDescription(
        key="is_alive",
        translation_key="online",
        name="Online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_bool("is_alive"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: RointeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RointeBinarySensor(coordinator, serial, description)
        for serial in coordinator.data
        for description in BINARY_SENSORS
    )


class RointeBinarySensor(RointeEntity, BinarySensorEntity):
    """A yes/no reading from the heater."""

    entity_description: RointeBinaryDescription

    def __init__(
        self,
        coordinator: RointeCoordinator,
        serial: str,
        description: RointeBinaryDescription,
    ) -> None:
        super().__init__(coordinator, serial)
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        device = self.device
        return self.entity_description.value_fn(device) if device else None

    @property
    def available(self) -> bool:
        # The online sensor must stay available to report being offline.
        if self.entity_description.key == "is_alive":
            return self.coordinator.last_update_success and self.device is not None
        return super().available
