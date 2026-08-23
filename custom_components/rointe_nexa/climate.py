"""Climate entity for a Rointe heater."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    MODE_MANUAL,
    MODE_SCHEDULE,
    POWER_ON,
    POWER_STANDBY,
    STATUS_COMFORT,
    STATUS_ECO,
    STATUS_ICE,
)
from .coordinator import RointeCoordinator
from .entity import RointeEntity

PRESET_COMFORT = "comfort"
PRESET_ECO = "eco"
PRESET_ANTI_FROST = "anti_frost"

# Rointe's `status` string <-> the preset shown in HA. The lowest level is
# named "ice" in the firmware and presented as anti-frost in the app.
PRESET_TO_STATUS = {
    PRESET_COMFORT: STATUS_COMFORT,
    PRESET_ECO: STATUS_ECO,
    PRESET_ANTI_FROST: STATUS_ICE,
}
STATUS_TO_PRESET = {v: k for k, v in PRESET_TO_STATUS.items()}

# Each preset stores its own setpoint under its own key.
PRESET_TO_SETPOINT_KEY = {
    PRESET_COMFORT: "comfort",
    PRESET_ECO: "eco",
    PRESET_ANTI_FROST: "ice",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: RointeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RointeClimate(coordinator, serial) for serial in coordinator.data
    )


class RointeClimate(RointeEntity, ClimateEntity):
    """The heater as a thermostat."""

    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.AUTO, HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = [PRESET_COMFORT, PRESET_ECO, PRESET_ANTI_FROST]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_min_temp = 7
    _attr_max_temp = 30
    _attr_target_temperature_step = 0.5
    # Kept for compatibility with HA versions that still expect these flags.
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, coordinator: RointeCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial)
        self._attr_unique_id = f"{serial}_climate"

    @property
    def current_temperature(self) -> float | None:
        value = self.read("temp_probe")
        return float(value) if value is not None else None

    @property
    def preset_mode(self) -> str | None:
        # Commanded, not reported: the heater lags, and while a schedule runs
        # its own status need not be one of the three presets.
        return STATUS_TO_PRESET.get(str(self.read_commanded("status", "")).lower())

    @property
    def target_temperature(self) -> float | None:
        """The setpoint of whichever preset is active.

        `temp` mirrors it, but only once the heater has caught up, so read the
        preset's own key and fall back to `temp`.
        """
        preset = self.preset_mode
        if preset:
            value = self.read_commanded(PRESET_TO_SETPOINT_KEY[preset])
            if value is not None:
                return float(value)
        value = self.read_commanded("temp")
        return float(value) if value is not None else None

    @property
    def hvac_mode(self) -> HVACMode:
        # AUTO means the heater is running its own weekly programme; HEAT
        # means a mode was picked by hand and holds until AUTO hands the
        # programme back. Without AUTO there is no way back to the schedule.
        if self.read_commanded("power") == POWER_STANDBY:
            return HVACMode.OFF
        if self.read_commanded("mode") == MODE_MANUAL:
            return HVACMode.HEAT
        return HVACMode.AUTO

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "core_temperature": self.read("temp_surface"),
            "charge_percentage": self.read("charge_percentage"),
            "following_schedule": self.read_commanded("mode") != MODE_MANUAL,
            "rointe_status": self.read_commanded("status"),
            "reported_status": self.read("status"),
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        preset = self.preset_mode or PRESET_COMFORT
        # Writing the preset's own key rather than `temp`, which is the
        # heater's readback of it.
        await self.command({PRESET_TO_SETPOINT_KEY[preset]: float(temperature)})

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        status = PRESET_TO_STATUS.get(preset_mode)
        if status is None:
            return
        await self.command({"status": status, "power": POWER_ON, "mode": MODE_MANUAL})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.command({"power": POWER_STANDBY})
        elif hvac_mode == HVACMode.AUTO:
            await self.command({"power": POWER_ON, "mode": MODE_SCHEDULE})
        else:
            await self.command({"power": POWER_ON, "mode": MODE_MANUAL})
