"""Constants for the Rointe Nexa integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "rointe_nexa"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

DEFAULT_SCAN_INTERVAL = 60

# Rointe's own preset names, as they appear in the device's `status` field.
STATUS_COMFORT = "comfort"
STATUS_ECO = "eco"
STATUS_ICE = "ice"

# `power`: 1 = standby, 2 = running.
POWER_STANDBY = 1
POWER_ON = 2

# `mode`: 0 = manual, 1 = following the schedule.
MODE_MANUAL = 0
MODE_SCHEDULE = 1

SERVICE_SET_CHARGE_HOURS = "set_charge_hours"
SERVICE_SET_HEATING_SCHEDULE = "set_heating_schedule"
SERVICE_BOOST = "boost"

ATTR_HOURS = "hours"
ATTR_DAYS = "days"
ATTR_SCHEDULE = "schedule"
ATTR_TEMPERATURE = "temperature"
ATTR_DURATION = "duration"
