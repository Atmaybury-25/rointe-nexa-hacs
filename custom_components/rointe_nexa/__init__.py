"""The Rointe Nexa integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
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
    ATTR_HOURS,
    ATTR_SCHEDULE,
    ATTR_TEMPERATURE,
    DOMAIN,
    PLATFORMS,
    SERVICE_BOOST,
    SERVICE_SET_CHARGE_HOURS,
    SERVICE_SET_HEATING_SCHEDULE,
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
    hass.services.async_register(DOMAIN, SERVICE_BOOST, _boost, BOOST_SCHEMA)
