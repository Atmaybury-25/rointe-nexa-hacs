"""The Rointe Nexa integration."""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    HEATING_CHARS,
    RointeAuthError,
    RointeClient,
    RointeError,
    hours_to_schedule,
    validate_day,
)
from .const import (
    ATTR_DAYS,
    ATTR_DURATION,
    ATTR_END,
    ATTR_HOURS,
    ATTR_SCHEDULE,
    ATTR_START,
    ATTR_TEMPERATURE,
    DOMAIN,
    PLATFORMS,
    SERVICE_BOOST,
    SERVICE_CLEAR_TRAVEL_MODE,
    SERVICE_SET_CHARGE_HOURS,
    SERVICE_SET_HEATING_SCHEDULE,
    SERVICE_SET_TRAVEL_MODE,
)
from .coordinator import RointeCoordinator

_LOGGER = logging.getLogger(__name__)

_DAY_INDEX = vol.All(vol.Coerce(int), vol.Range(min=0, max=6))
_HOUR = vol.All(vol.Coerce(int), vol.Range(min=0, max=23))

SET_CHARGE_HOURS_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(ATTR_HOURS): vol.All(cv.ensure_list, [_HOUR]),
        vol.Optional(ATTR_DAYS): vol.All(cv.ensure_list, [_DAY_INDEX]),
    }
)

SET_HEATING_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(ATTR_SCHEDULE): cv.string,
        vol.Optional(ATTR_DAYS): vol.All(cv.ensure_list, [_DAY_INDEX]),
    }
)

BOOST_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Optional(ATTR_TEMPERATURE): vol.All(vol.Coerce(float), vol.Range(min=7, max=30)),
        vol.Optional(ATTR_DURATION): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
    }
)

SET_TRAVEL_MODE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(ATTR_START): cv.datetime,
        vol.Optional(ATTR_END): vol.Any(None, cv.datetime),
    }
)

CLEAR_TRAVEL_MODE_SCHEMA = vol.Schema({vol.Required("entity_id"): cv.entity_ids})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Rointe Nexa from a config entry."""
    session = async_get_clientsession(hass)
    client = RointeClient(
        entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD], session
    )

    try:
        await client.async_login()
    except RointeAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except RointeError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = RointeCoordinator(hass, client, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            for service in (
                SERVICE_SET_CHARGE_HOURS,
                SERVICE_SET_HEATING_SCHEDULE,
                SERVICE_BOOST,
                SERVICE_SET_TRAVEL_MODE,
                SERVICE_CLEAR_TRAVEL_MODE,
            ):
                hass.services.async_remove(DOMAIN, service)
    return unloaded


def _devices_for_call(hass: HomeAssistant, call: ServiceCall):
    """Resolve entity_ids to (coordinator, device) pairs.

    Commands are zone-scoped, so two entities on the same heater resolve to the
    same device. De-duplicate, or a single call would write twice.
    """
    registry = er.async_get(hass)
    seen: set[tuple[str, str]] = set()
    out = []
    for entity_id in call.data["entity_id"]:
        entry = registry.async_get(entity_id)
        if entry is None or entry.platform != DOMAIN or entry.config_entry_id is None:
            _LOGGER.warning("%s is not a Rointe Nexa entity", entity_id)
            continue
        coordinator: RointeCoordinator | None = hass.data.get(DOMAIN, {}).get(
            entry.config_entry_id
        )
        if coordinator is None or not coordinator.data:
            continue
        # every entity this integration creates uses "{serial}_{key}"
        serial = (entry.unique_id or "").split("_")[0]
        device = coordinator.data.get(serial)
        if device is None:
            _LOGGER.warning("no Rointe device behind %s", entity_id)
            continue
        key = (entry.config_entry_id, device.unique_id)
        if key in seen:
            continue
        seen.add(key)
        out.append((coordinator, device))
    return out


def _installations_for_call(hass: HomeAssistant, call: ServiceCall):
    """Resolve entity_ids to one (coordinator, device) per installation.

    Travel mode belongs to the installation, so several heaters in one home
    must produce a single API call, not one each.
    """
    seen: set[str] = set()
    out = []
    for coordinator, device in _devices_for_call(hass, call):
        if not device.installation_id or device.installation_id in seen:
            continue
        seen.add(device.installation_id)
        out.append((coordinator, device))
    return out


def _installation_zone(hass: HomeAssistant, device) -> tuple[str, ZoneInfo]:
    """The installation's IANA time zone, falling back to Home Assistant's."""
    for name in (device.installation.get("timeCountry"), hass.config.time_zone):
        if not name:
            continue
        try:
            return str(name), ZoneInfo(str(name))
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return "UTC", ZoneInfo("UTC")


def _to_wall_clock(value: datetime | None, zone: ZoneInfo) -> datetime | None:
    """A naive wall-clock time in the installation's zone.

    A time given without an offset is taken as local to the installation,
    which is what anyone typing "Friday 09:00" means.
    """
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(zone)
    return value.replace(tzinfo=None)


def _register_services(hass: HomeAssistant) -> None:
    """Register services once, however many config entries exist."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_CHARGE_HOURS):
        return

    async def _set_charge_hours(call: ServiceCall) -> None:
        hours = sorted(set(call.data[ATTR_HOURS]))
        days = call.data.get(ATTR_DAYS) or list(range(7))
        day_string = hours_to_schedule(hours)
        for coordinator, device in _devices_for_call(hass, call):
            await coordinator.client.async_set_charge_schedule(
                device, {int(d): day_string for d in days}
            )
            await coordinator.async_request_refresh()

    async def _set_heating_schedule(call: ServiceCall) -> None:
        day_string = validate_day(call.data[ATTR_SCHEDULE].upper(), HEATING_CHARS)
        days = call.data.get(ATTR_DAYS) or list(range(7))
        for coordinator, device in _devices_for_call(hass, call):
            await coordinator.client.async_set_heating_schedule(
                device, {int(d): day_string for d in days}
            )
            await coordinator.async_request_refresh()

    async def _boost(call: ServiceCall) -> None:
        # Goes through the coordinator so the mode the boost interrupts is
        # remembered and handed back when it ends, exactly as the switch does.
        extra: dict = {}
        if ATTR_TEMPERATURE in call.data:
            extra["timer_config_temp"] = call.data[ATTR_TEMPERATURE]
        if ATTR_DURATION in call.data:
            extra["timer_config_time"] = int(call.data[ATTR_DURATION]) * 60
        for coordinator, device in _devices_for_call(hass, call):
            await coordinator.async_start_boost(device, extra)

    hass.services.async_register(
        DOMAIN, SERVICE_SET_CHARGE_HOURS, _set_charge_hours, SET_CHARGE_HOURS_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_HEATING_SCHEDULE,
        _set_heating_schedule,
        SET_HEATING_SCHEDULE_SCHEMA,
    )
    async def _set_travel_mode(call: ServiceCall) -> None:
        targets = _installations_for_call(hass, call)
        if not targets:
            raise ServiceValidationError("no Rointe heater in that target")
        for coordinator, device in targets:
            zone_name, zone = _installation_zone(hass, device)
            start = _to_wall_clock(call.data[ATTR_START], zone)
            end = _to_wall_clock(call.data.get(ATTR_END), zone)
            try:
                await coordinator.client.async_set_travel_periods(
                    device.installation_id, [(start, end)], zone_name
                )
                # The flag is a toggle, so only flip it when it is off.
                if not device.installation.get("holidaysMode"):
                    await coordinator.client.async_set_travel_mode_flag(
                        device.installation_id, True
                    )
            except ValueError as err:
                raise ServiceValidationError(str(err)) from err
            except RointeError as err:
                raise HomeAssistantError(f"Rointe refused travel mode: {err}") from err
            await coordinator.async_rediscover()

    async def _clear_travel_mode(call: ServiceCall) -> None:
        for coordinator, device in _installations_for_call(hass, call):
            try:
                await coordinator.client.async_clear_travel_mode(
                    device.installation_id,
                    mode_active=bool(device.installation.get("holidaysMode")),
                )
            except RointeError as err:
                raise HomeAssistantError(f"Rointe refused to clear travel mode: {err}") from err
            await coordinator.async_rediscover()

    hass.services.async_register(DOMAIN, SERVICE_BOOST, _boost, BOOST_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_SET_TRAVEL_MODE, _set_travel_mode, SET_TRAVEL_MODE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_TRAVEL_MODE, _clear_travel_mode, CLEAR_TRAVEL_MODE_SCHEMA
    )
