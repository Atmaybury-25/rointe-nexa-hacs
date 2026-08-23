"""Polling coordinator for Rointe Nexa."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RointeAuthError, RointeClient, RointeDevice, RointeError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, MODE_MANUAL

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

    async def _async_update_data(self) -> dict[str, RointeDevice]:
        try:
            if not self._devices:
                self._devices = await self.client.async_discover()
                if not self._devices:
                    _LOGGER.warning(
                        "No Rointe devices found. Is a heater paired in the Nexa app?"
                    )
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

    async def async_command(self, device: RointeDevice, payload: dict) -> None:
        """Send a command and refresh so the UI does not sit on stale state."""
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
        await self.async_command(device, payload)

    async def async_end_boost(self, device: RointeDevice) -> None:
        """End a boost and hand the weekly programme back."""
        payload: dict = {"timer_mode": False}
        if device.mode_before_boost is not None:
            payload["mode"] = device.mode_before_boost
        device.mode_before_boost = None
        device.boost_acknowledged = False
        await self.async_command(device, payload)

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
            await self.async_end_boost(device)
