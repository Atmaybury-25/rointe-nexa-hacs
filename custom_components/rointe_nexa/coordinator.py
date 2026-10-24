"""Polling coordinator for Rointe Nexa."""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from homeassistant.util import dt as dt_util

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RointeAuthError, RointeClient, RointeDevice, RointeError
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DISCOVERY_INTERVAL,
    DOMAIN,
    MODE_MANUAL,
    POWER_OFF,
)

_LOGGER = logging.getLogger(__name__)


class RointeCoordinator(DataUpdateCoordinator[dict[str, RointeDevice]]):
    """Keeps every heater's commanded and reported state fresh.

    Polling rather than subscribing: the app uses the Firebase websocket, but
    the wire protocol is undocumented and reconnect handling is a lot of
    surface area for a heater whose state changes on the scale of minutes.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: RointeClient,
        entry: ConfigEntry,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.entry = entry
        self._devices: list[RointeDevice] = []
        self._last_discovery = 0.0

    async def _async_discover(self) -> None:
        """Re-read the inventory, keeping the existing device objects.

        The objects carry state of their own (the mode a boost interrupted),
        so a heater that is still there keeps its object and only has its
        inventory fields refreshed. New heaters are appended; the platforms
        notice them on the next update and add their entities.
        """
        found = await self.client.async_discover()
        self._last_discovery = time.monotonic()
        if not found:
            if not self._devices:
                _LOGGER.warning(
                    "No Rointe devices found. Is a heater paired in the Nexa app?"
                )
            # An empty inventory from a working API is more likely a transient
            # glitch than every heater being deleted. Keep what we have.
            return
        existing = {d.unique_id: d for d in self._devices}
        merged: list[RointeDevice] = []
        for new in found:
            old = existing.get(new.unique_id)
            if old is None:
                if self._devices:
                    _LOGGER.info("New Rointe heater found: %s", new.name)
                merged.append(new)
                continue
            old.device_id = new.device_id
            old.name = new.name
            old.zone_id = new.zone_id
            old.zone_name = new.zone_name
            old.installation_id = new.installation_id
            old.installation_name = new.installation_name
            old.installation = new.installation
            merged.append(old)
        self._devices = merged

    async def async_rediscover(self) -> None:
        """Re-read the inventory now, e.g. after changing travel mode.

        Best effort: the change itself has already been made, so a failed
        re-read is logged rather than reported as the change failing.
        """
        try:
            await self._async_discover()
        except RointeError as err:
            _LOGGER.warning("could not re-read the Rointe inventory: %s", err)
        await self.async_request_refresh()

    async def _async_update_data(self) -> dict[str, RointeDevice]:
        try:
            if (
                not self._devices
                or time.monotonic() - self._last_discovery > DISCOVERY_INTERVAL
            ):
                await self._async_discover()
            await self.client.async_refresh(self._devices)
        except RointeAuthError as err:
            # Surfaces a re-auth prompt rather than retrying a bad password
            # forever - Rointe answer bad credentials with HTTP 418.
            raise ConfigEntryAuthFailed(str(err)) from err
        except RointeError as err:
            raise UpdateFailed(str(err)) from err

        for device in self._devices:
            await self._check_boost_expiry(device)

        return {device.unique_id: device for device in self._devices}

    async def async_command(
        self, device: RointeDevice, payload: dict, *, force: bool = False
    ) -> None:
        """Send a command and refresh so the UI does not sit on stale state.

        Like the Nexa app, refuse to change settings on a heater that has been
        switched off at the unit (reported `power` 0) - the heater ignores
        them until it is switched back on. Writes that change `power` itself
        are always allowed, as are internal clean-ups (`force`).
        """
        if not force and "power" not in payload and device.reported.get("power") == POWER_OFF:
            raise HomeAssistantError(
                f"{device.name} is switched off at the heater; switch it on "
                "before changing its settings"
            )
        await self.client.async_command(device, payload)
        await self.async_request_refresh()

    # -- boost ------------------------------------------------------------
    #
    # Boost is the heater's timer, and it only runs out of schedule mode. The
    # heater never returns to its programme by itself, so these three methods
    # remember the mode Boost interrupted and hand it back when Boost ends -
    # whether it is switched off or simply runs out of time.

    async def async_start_boost(self, device: RointeDevice, extra: dict | None = None) -> None:
        """Begin a boost, remembering what the heater was doing."""
        if not device.get_commanded("timer_mode"):
            mode = device.get_commanded("mode")
            device.mode_before_boost = int(mode) if mode is not None else None
            device.boost_acknowledged = False
        payload: dict = {"timer_mode": True, "mode": MODE_MANUAL}
        if extra:
            payload.update(extra)
        # The app writes the boost end time as an absolute UTC epoch, next to
        # the configured duration. Confirmed 24 Aug 2026: a 2 h boost set at
        # 12:32 London wrote timer_time for 14:32:07 exactly. Without it the
        # heater has no deadline of its own, only whatever is left over from the
        # last boost - which is stale, and may already be in the past.
        duration = payload.get(
            "timer_config_time", device.get_commanded("timer_config_time")
        )
        temperature = payload.get(
            "timer_config_temp", device.get_commanded("timer_config_temp")
        )
        if duration:
            payload["timer_time"] = int(dt_util.utcnow().timestamp()) + int(duration)
        if temperature is not None:
            payload["timer_temp"] = temperature
        await self.async_command(device, payload)

    async def async_end_boost(self, device: RointeDevice) -> None:
        """End a boost and hand the weekly programme back."""
        payload: dict = {"timer_mode": False}
        if device.mode_before_boost is not None:
            payload["mode"] = device.mode_before_boost
        device.mode_before_boost = None
        device.boost_acknowledged = False
        # Forced: handing the programme back must not be blocked by the
        # switched-off guard, or a heater turned off mid-boost would be left
        # in manual for good.
        await self.async_command(device, payload, force=True)

    async def _check_boost_expiry(self, device: RointeDevice) -> None:
        """Hand the programme back when the heater's own timer runs out.

        The falling edge has to be read from the reported node: the timer
        expires on the heater, which never writes to the command channel, so
        the commanded `timer_mode` would otherwise sit True forever.
        """
        if device.mode_before_boost is None:
            return
        reported = device.reported.get("timer_mode")
        if reported is None:
            return
        if reported:
            device.boost_acknowledged = True
        elif device.boost_acknowledged:
            _LOGGER.debug("%s: boost timer expired, restoring mode", device.name)
            try:
                await self.async_end_boost(device)
            except RointeError as err:
                # Never fail the whole poll over this; it is retried next time
                # only if the boost is seen again, so log it loudly.
                _LOGGER.warning("%s: could not restore mode after boost: %s", device.name, err)
