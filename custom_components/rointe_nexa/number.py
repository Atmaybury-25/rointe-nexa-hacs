"""Adjustable charge limits and preset setpoints for a Rointe heater."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import (
    ADDITIONAL_CHARGE_MAX,
    CHARGE_MAX_HIGH,
    CHARGE_MAX_LOW,
    CHARGE_MIN_HIGH,
    CHARGE_MIN_LOW,
    CHARGE_STEP,
    validate_charge_limits,
)
from .const import DOMAIN
from .coordinator import RointeCoordinator
from .entity import RointeEntity, async_setup_device_entities


@dataclass(frozen=True, kw_only=True)
class RointeNumberDescription(NumberEntityDescription):
    """A writable number, and the field behind it."""

    field: str
    # Charge limits are whole percent and the firmware wants ints; the
    # setpoints are half-degrees and must stay floats. Writing 20.5 as an int
    # silently becomes 20.
    write_float: bool = False


NUMBERS: tuple[RointeNumberDescription, ...] = (
    RointeNumberDescription(
        key="charge_percentage_limit",
        translation_key="max_charge",
        name="Maximum charge",
        field="charge_percentage_limit",
        native_min_value=CHARGE_MAX_LOW,
        native_max_value=CHARGE_MAX_HIGH,
        native_step=CHARGE_STEP,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        icon="mdi:battery-high",
    ),
    RointeNumberDescription(
        key="charge_percentage_min",
        translation_key="min_charge",
        name="Minimum charge",
        field="charge_percentage_min",
        native_min_value=CHARGE_MIN_LOW,
        native_max_value=CHARGE_MIN_HIGH,
        native_step=CHARGE_STEP,
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
        native_max_value=ADDITIONAL_CHARGE_MAX,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.HOURS,
        mode=NumberMode.BOX,
        icon="mdi:clock-plus-outline",
    ),
    RointeNumberDescription(
        key="comfort_temperature",
        translation_key="comfort_temperature",
        name="Comfort temperature",
        field="comfort",
        native_min_value=7,
        native_max_value=30,
        native_step=0.5,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=NumberDeviceClass.TEMPERATURE,
        mode=NumberMode.BOX,
        write_float=True,
    ),
    RointeNumberDescription(
        key="eco_temperature",
        translation_key="eco_temperature",
        name="Eco temperature",
        field="eco",
        native_min_value=7,
        native_max_value=30,
        native_step=0.5,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=NumberDeviceClass.TEMPERATURE,
        mode=NumberMode.BOX,
        write_float=True,
    ),
    RointeNumberDescription(
        key="anti_frost_temperature",
        translation_key="anti_frost_temperature",
        name="Anti-frost temperature",
        field="ice",
        native_min_value=7,
        native_max_value=30,
        native_step=0.5,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=NumberDeviceClass.TEMPERATURE,
        mode=NumberMode.BOX,
        write_float=True,
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
        lambda serial: [RointeNumber(coordinator, serial, d) for d in NUMBERS],
    )


class RointeNumber(RointeEntity, NumberEntity):
    """One writable setting.

    The two charge limits bound Rointe's Smart charging: the minimum (0-30%)
    and maximum (60-100%) it may aim for, exactly the ranges the Nexa app
    allows. They are not a way to stop a heater charging - for that, use the
    `rointe_nexa.set_travel_mode` service or take hours out of the charge
    schedule.

    `additional_charge` is hours of charging the heater may take outside its
    scheduled window - set it to 0 to stop it wandering into expensive hours.

    The comfort/eco/anti-frost setpoints are the stored presets the weekly
    programme selects between. The climate entity's target temperature only
    writes whichever preset happens to be active right now, so these are the
    way to change a preset while a different one is running.
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
        """Write the value in the type the firmware stores it as.

        The three preset setpoints are the same fields the climate entity
        writes when you drag its target, so changing one here changes the
        heater's stored preset - not just the current hour.
        """
        field = self.entity_description.field
        if field in _CHARGE_PAIR:
            await self.command(self._charge_pair(field, int(value)))
        elif self.entity_description.write_float:
            await self.command({field: float(value)})
        else:
            await self.command({field: int(value)})

    def _charge_pair(self, field: str, value: int) -> dict[str, int]:
        """Both smart-charge limits, as the app always writes them.

        The app never writes one limit without the other, and insists the
        minimum stays below the maximum. Changing one here sends the other's
        current value alongside it.
        """
        other = _CHARGE_PAIR[field]
        current = self.read_commanded(other)
        try:
            current = int(current)
        except (TypeError, ValueError):
            current = CHARGE_MIN_HIGH if other == "charge_percentage_min" else CHARGE_MAX_HIGH
        # A limit left outside the app's range (e.g. written by an older
        # version of this integration) is pulled back inside it, so it can
        # never block changing the other one. The ranges do not overlap, so
        # the minimum always ends up below the maximum.
        if other == "charge_percentage_min":
            current = min(max(current, CHARGE_MIN_LOW), CHARGE_MIN_HIGH)
        else:
            current = min(max(current, CHARGE_MAX_LOW), CHARGE_MAX_HIGH)
        current -= current % CHARGE_STEP
        if field == "charge_percentage_min":
            minimum, maximum = value, current
        else:
            minimum, maximum = current, value
        try:
            minimum, maximum = validate_charge_limits(minimum, maximum)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        return {"charge_percentage_min": minimum, "charge_percentage_limit": maximum}


_CHARGE_PAIR = {
    "charge_percentage_min": "charge_percentage_limit",
    "charge_percentage_limit": "charge_percentage_min",
}
