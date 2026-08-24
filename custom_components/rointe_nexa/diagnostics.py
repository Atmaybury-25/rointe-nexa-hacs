"""Diagnostics for Rointe Nexa.

Dumps both RTDB nodes for every heater, redacted, so that fields nobody has
explained yet can be identified without anyone handing round credentials.

If you have a Rointe device this integration has never seen, this is the file
to attach to an issue: it lists every key the heater reports, every key the app
commands, and which of them this integration actually understands.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import RointeCoordinator

# Anything that identifies the account, the home or the specific unit. The
# serial is a doubled MAC and the SSID names the household, so both go.
TO_REDACT = {
    "address",
    "city",
    "debug_server_path",
    "email",
    "id_token",
    "installation",
    "latitude",
    "longitude",
    "mac",
    "password",
    "postcode",
    "refresh_token",
    "serial",
    "serialNumber",
    "serialnumber",
    "token",
    "um_password",
    "user_id",
    "username",
    "wifipass",
    "wifissid",
}

# Every field this integration reads or writes. Everything else in the node is
# reported here as unused - not as unimportant, just unmapped. Keeping this
# list honest is the point of the whole file.
USED_KEYS = frozenset(
    {
        "additional_charge",
        "charge_percentage",
        "charge_percentage_limit",
        "charge_percentage_min",
        "charge_smart",
        "charging",
        "comfort",
        "eco",
        "ice",
        "is_alive",
        "last_sync_datetime_device",
        "mode",
        "power",
        "schedule",
        "schedule_charging",
        "status",
        "temp",
        "temp_probe",
        "temp_surface",
        "timer_config_temp",
        "timer_config_time",
        "timer_mode",
        "type",
        "wifisignal",
        "windows_open_status",
    }
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Everything useful about one account's heaters, with the private bits out."""
    coordinator: RointeCoordinator = hass.data[DOMAIN][entry.entry_id]

    devices: list[dict[str, Any]] = []
    for device in (coordinator.data or {}).values():
        reported = device.reported or {}
        commanded = device.zone or {}
        all_keys = set(reported) | set(commanded)
        devices.append(
            {
                "name": device.name,
                "model": device.model,
                "online": device.online,
                "zone_name": device.zone_name,
                "firmware": async_redact_data(dict(device.firmware or {}), TO_REDACT),
                "keys": {
                    "reported_count": len(reported),
                    "commanded_count": len(commanded),
                    "used_by_integration": sorted(all_keys & USED_KEYS),
                    "not_used_by_integration": sorted(all_keys - USED_KEYS),
                    "reported_only": sorted(set(reported) - set(commanded)),
                    "commanded_only": sorted(set(commanded) - set(reported)),
                    "disagreeing": sorted(
                        k
                        for k in set(reported) & set(commanded)
                        if reported[k] != commanded[k]
                    ),
                },
                "reported": async_redact_data(dict(reported), TO_REDACT),
                "commanded": async_redact_data(dict(commanded), TO_REDACT),
            }
        )

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "scan_interval_seconds": (
            coordinator.update_interval.total_seconds()
            if coordinator.update_interval
            else None
        ),
        "device_count": len(devices),
        "devices": devices,
    }
