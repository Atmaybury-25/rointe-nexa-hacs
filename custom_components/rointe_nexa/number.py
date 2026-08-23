"""Adjustable charge limits for a Rointe heater."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import RointeCoordinator
from .entity import RointeEntity


@dataclass(frozen=True, kw_only=True)
class RointeNumberDescription(NumberEntityDescription):
    """A writable number, and the field behind it."""

    field: str


NUMBERS: tuple[RointeNumberDescription, ...] = (
    RointeNumberDescription(
        key="charge_percentage_limit",
        translation_key="max_charge",
        name="Maximum charge",
        field="charge_percentage_limit",
        native_min_value=0,
        native_max_value=100,
        native_step=5,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        icon="mdi:battery-high",
    ),
    RointeNumberDescription(
        key="charge_percentage_min",
        translation_key="min_charge",
        name="Minimum charge",
        field="charge_percentage_min",
        native_min_value=0,
        native_max_value=100,
        native_step=5,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        icon="mdi:battery-low",
    ),
    RointeNumberDescription(
        key="additional_charge",
        translation_key="additional_charge",
        name="Additional charge",
        field="additional_charge",
        native_min_value=0,
        native_max_value=8,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.HOURS,
        mode=NumberMode.BOX,
        icon="mdi:clock-plus-outline",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: RointeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RointeNumber(coordinator, serial, description)
        for serial in coordinator.data
        for description in NUMBERS
    )


class RointeNumber(RointeEntity, NumberEntity):
    """One writable setting.

    `additional_charge` is hours of charging the heater may take outside its
    scheduled window - set it to 0 to stop it wandering into expensive hours.
    """

    entity_description: RointeNumberDescription

    def __init__(
        self,
        coordinator: RointeCoordinator,
        serial: str,
        description: RointeNumberDescription,
    ) -> None:
        super().__init__(coordinator, serial)
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key}"

    @property
    def native_value(self) -> float | None:
        value = self.read_commanded(self.entity_description.field)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self.command({self.entity_description.field: int(value)})
