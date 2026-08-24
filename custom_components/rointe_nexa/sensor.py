"""Sensors for a Rointe heater."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import RointeCoordinator
from .entity import RointeEntity


@dataclass(frozen=True, kw_only=True)
class RointeSensorDescription(SensorEntityDescription):
    """A sensor and how to pull its value out of the device state."""

    value_fn: Callable[[Any], Any]


def _float(key: str) -> Callable[[Any], Any]:
    def _get(device: Any) -> float | None:
        value = device.get(key)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return _get


SENSORS: tuple[RointeSensorDescription, ...] = (
    RointeSensorDescription(
        key="charge_percentage",
        translation_key="charge_percentage",
        name="Charge",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-charging-60",
        value_fn=_float("charge_percentage"),
    ),
    RointeSensorDescription(
        key="temp_surface",
        translation_key="core_temperature",
        name="Core temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_float("temp_surface"),
    ),
    RointeSensorDescription(
        key="temp_probe",
        translation_key="room_temperature",
        name="Room temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_float("temp_probe"),
    ),
    RointeSensorDescription(
        key="wifisignal",
        translation_key="wifi_signal",
        name="WiFi signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_float("wifisignal"),
    ),
)


# Fields nobody has explained yet, exposed so they can be watched over time
# instead of guessed at. All disabled by default - enable them only if you are
# trying to work out what a field means, then tell us in an issue.
#
# The interesting question these are here to answer: several Onyx units have a
# current transformer around the incoming feed, so the heater is measuring
# something. None of these move while it sits idle, and the Rointe app never
# reads any of them - the consumption page is nominal power times time, which is
# why it is labelled indicative. Watching them across a real charge cycle is the
# way to find out whether any of them carries a measurement.
UNMAPPED: tuple[RointeSensorDescription, ...] = tuple(
    RointeSensorDescription(
        key=key,
        name=f"Raw {key}",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:help-rhombus-outline",
        value_fn=_float(key),
    )
    for key in (
        "charging_consuption",
        "nominal_effective_power",
        "power_supply_details",
        "status_warming",
        "tpfl",
        "tpsf",
        "mgmt_modules",
    )
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: RointeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RointeSensor(coordinator, serial, description)
        for serial in coordinator.data
        for description in SENSORS + UNMAPPED
    )


class RointeSensor(RointeEntity, SensorEntity):
    """One reading from the heater."""

    entity_description: RointeSensorDescription

    def __init__(
        self,
        coordinator: RointeCoordinator,
        serial: str,
        description: RointeSensorDescription,
    ) -> None:
        super().__init__(coordinator, serial)
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key}"

    @property
    def native_value(self) -> Any:
        device = self.device
        return self.entity_description.value_fn(device) if device else None
