"""Boost and smart-charge switches for a Rointe heater."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util
from homeassistant.helpers.restore_state import (
    ExtraStoredData,
    RestoredExtraData,
    RestoreEntity,
)

from .const import DOMAIN
from .coordinator import RointeCoordinator
from .entity import RointeEntity, async_setup_device_entities


@dataclass(frozen=True, kw_only=True)
class RointeSwitchDescription(SwitchEntityDescription):
    """A plain on/off setting and the field behind it."""

    field: str


# The app's Additional settings screen (DetailOptionsStrategy.
# updateAdditionalSettings). `res` is resistencia - the direct-acting element
# alongside the storage core. Each flag says whether that element may fire in
# a given mode; with one off, that mode heats from stored charge only.
FIELD_SWITCHES: tuple[RointeSwitchDescription, ...] = (
    RointeSwitchDescription(
        key="res_auto_config",
        translation_key="element_auto",
        name="Extra element in auto",
        field="res_auto_config",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:heating-coil",
    ),
    RointeSwitchDescription(
        key="res_man_config",
        translation_key="element_manual",
        name="Extra element in manual",
        field="res_man_config",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:heating-coil",
    ),
    RointeSwitchDescription(
        key="res_boost_config",
        translation_key="element_boost",
        name="Extra element in boost",
        field="res_boost_config",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:heating-coil",
    ),
    RointeSwitchDescription(
        key="res_holiday_config",
        translation_key="element_travel",
        name="Extra element in travel mode",
        field="res_holiday_config",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:heating-coil",
    ),
    RointeSwitchDescription(
        key="silence_mode",
        translation_key="silent_mode",
        name="Silent mode",
        field="silence_mode",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:fan-off",
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
            RointeBoostSwitch(coordinator, serial),
            RointeSmartChargeSwitch(coordinator, serial),
            *(RointeFieldSwitch(coordinator, serial, d) for d in FIELD_SWITCHES),
        ],
    )


class RointeBoostSwitch(RointeEntity, SwitchEntity, RestoreEntity):
    """The app's Boost.

    Boost is the timer, not `advance_enable` - confirmed by watching the field
    move when Boost is pressed in the app. Temperature and duration live in
    timer_config_temp and timer_config_time; the rointe_nexa.boost service sets
    those, this switch just uses whatever the heater already holds.

    Starting and ending a boost go through the coordinator, which remembers
    the mode the boost interrupted, so the `rointe_nexa.boost` service behaves
    the same way this switch does. RestoreEntity carries that memory across a
    Home Assistant restart; without it a restart mid-boost would strand the
    heater in manual.
    """

    _attr_translation_key = "boost"
    _attr_name = "Boost"
    _attr_icon = "mdi:fire"

    def __init__(self, coordinator: RointeCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial)
        self._attr_unique_id = f"{serial}_boost"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        device = self.device
        stored = await self.async_get_last_extra_data()
        if device is None or stored is None:
            return
        data = stored.as_dict()
        mode = data.get("mode_before_boost")
        if mode is not None and device.mode_before_boost is None:
            device.mode_before_boost = int(mode)
            device.boost_acknowledged = bool(data.get("boost_acknowledged"))

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        device = self.device
        return RestoredExtraData(
            {
                "mode_before_boost": device.mode_before_boost if device else None,
                "boost_acknowledged": device.boost_acknowledged if device else False,
            }
        )

    @property
    def is_on(self) -> bool | None:
        value = self.read_commanded("timer_mode")
        return bool(value) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        duration = self.read_commanded("timer_config_time")
        # timer_time is the boost end as a UTC epoch. It is left behind stale
        # once a boost ends, so only report it while one is actually running.
        ends_at = None
        if self.is_on:
            raw = self.read_commanded("timer_time")
            try:
                ends_at = dt_util.utc_from_timestamp(int(raw)).isoformat() if raw else None
            except (TypeError, ValueError):
                ends_at = None
        device = self.device
        return {
            "boost_temperature": self.read_commanded("timer_config_temp"),
            "boost_minutes": int(duration) // 60 if duration else None,
            "boost_ends_at": ends_at,
            "mode_before_boost": device.mode_before_boost if device else None,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        device = self.device
        if device is not None:
            await self.coordinator.async_start_boost(device)

    async def async_turn_off(self, **kwargs: Any) -> None:
        device = self.device
        if device is not None:
            await self.coordinator.async_end_boost(device)


class RointeSmartChargeSwitch(RointeEntity, SwitchEntity):
    """Rointe's own charge optimiser.

    Leave it on and the heater decides when to charge within its window. Turn
    it off to make the written charge schedule authoritative, which is what you
    want if Home Assistant is picking the hours from electricity prices.
    """

    _attr_translation_key = "smart_charge"
    _attr_name = "Smart charging"
    _attr_icon = "mdi:auto-fix"

    def __init__(self, coordinator: RointeCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial)
        self._attr_unique_id = f"{serial}_charge_smart"

    @property
    def is_on(self) -> bool | None:
        value = self.read_commanded("charge_smart")
        return bool(value) if value is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.command({"charge_smart": True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.command({"charge_smart": False})


class RointeFieldSwitch(RointeEntity, SwitchEntity):
    """A boolean setting written straight to its field."""

    entity_description: RointeSwitchDescription

    def __init__(
        self,
        coordinator: RointeCoordinator,
        serial: str,
        description: RointeSwitchDescription,
    ) -> None:
        super().__init__(coordinator, serial)
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key}"

    @property
    def available(self) -> bool:
        # Only offer the switch on heaters that actually carry the field.
        device = self.device
        return super().available and device is not None and (
            self.entity_description.field in device.zone
            or self.entity_description.field in device.reported
        )

    @property
    def is_on(self) -> bool | None:
        value = self.read_commanded(self.entity_description.field)
        return bool(value) if value is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.command({self.entity_description.field: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.command({self.entity_description.field: False})
