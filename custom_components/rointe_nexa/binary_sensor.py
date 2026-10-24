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
from .entity import RointeEntity, async_setup_device_entities


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
    async_setup_device_entities(
        coordinator,
        entry,
        async_add_entities,
        lambda serial: [
            *(RointeBinarySensor(coordinator, serial, d) for d in BINARY_SENSORS),
            RointeTravelModeSensor(coordinator, serial),
        ],
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


class RointeTravelModeSensor(RointeEntity, BinarySensorEntity):
    """Whether the heater's installation is in the app's Travel mode.

    Travel mode is held on the installation, not the heater, so every heater
    in the installation shows the same value. Refreshed with the inventory
    (every 30 minutes, and straight after the travel-mode services run).
    """

    _attr_translation_key = "travel_mode"
    _attr_name = "Travel mode"
    _attr_icon = "mdi:bag-suitcase"

    def __init__(self, coordinator: RointeCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial)
        self._attr_unique_id = f"{serial}_travel_mode"

    @property
    def is_on(self) -> bool | None:
        device = self.device
        if device is None or "holidaysMode" not in device.installation:
            return None
        return bool(device.installation.get("holidaysMode"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        device = self.device
        periods = []
        for item in (device.installation.get("holidays") or []) if device else []:
            if not isinstance(item, dict):
                continue
            start, end = item.get("from"), item.get("to")
            periods.append(
                {
                    "start": start.get("date") if isinstance(start, dict) else start,
                    "end": end.get("date") if isinstance(end, dict) else end,
                }
            )
        return {"periods": periods}
