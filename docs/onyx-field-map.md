# Rointe Onyx — RTDB field map

Everything known about the data behind a Rointe Onyx storage heater on the **v4**
Nexa platform, gathered by reverse-engineering the web app against one live
heater. Nothing here comes from Rointe.

If you have a Rointe device this integration has not seen, a redacted diagnostics
dump (Settings → Devices & Services → Rointe Nexa → Download diagnostics) is the
single most useful thing you can attach to an issue.

## The two nodes

| Node | Role |
|---|---|
| `/zones/{zoneId}/data` | **commanded** — what the app writes, and reads back for display |
| `/devices/{serial}/data` | **reported** — what the heater says about itself |

97 keys in the device node, 44 in the zone node, and the zone node is a strict
subset. Both carry the same field names, which is what makes this easy to get
wrong: a write to the device node returns HTTP 200, reads back correctly, and is
silently re-asserted by the heater on its next sync. **Commands go to the zone
node.**

The rule that follows: read anything **writable** from the zone node and anything
**physical** from the device node. Mix them up and a setting the user just
changed appears to snap back for several minutes while the heater catches up.

Writes are RTDB merges, and slash-separated keys work, so a single day of a
schedule can be replaced with `{"schedule_charging/2": "001000000000000000000000"}`
rather than resending the week.

**Never write `last_sync_datetime_device`.** It is the heater's own sync marker;
the app only ever echoes back what the device wrote. Setting it appears to tell
the heater the state is already synced, and the change is discarded.

## Charge control

| Field | Meaning |
|---|---|
| `charge_percentage` | stored charge, % |
| `charge_percentage_limit` | charge ceiling, % |
| `charge_percentage_min` | charge floor, % |
| `charging` | in a charging state — **not** "drawing nameplate power" |
| `charge_smart` | Smart Charge: the heater sizes its own charge |
| `schedule_charging[0..6]` | 7 x 24 chars, `1` = may charge that hour, `0` = not. Index 0 = Monday |
| `schedule_charge_type` | 0 custom, 1 Period 1, 2 Period 2 |
| `additional_charge` | **hours** it may charge outside the window |
| `charging_consuption` | *(their typo)* constant 10000 on this model. Reads as 10 kWh of usable storage; unconfirmed |

**Smart Charge does not override the schedule.** Observed on a live heater: with
`charge_smart: true`, a schedule permitting 00:00–07:00 and `additional_charge: 1`,
the heater charged 00:00–07:37 — inside its permitted hours, then 37 minutes of
its one-hour overrun allowance. The schedule says *when it may*, Smart Charge
decides *how much and exactly when* within that, and `additional_charge` caps the
spill.

**`charging` is not a power reading.** The Onyx is an inverter unit and
modulates. 7.66 h of `charging: true` against a 1560 W element implies 11.9 kWh,
while the stored charge moved about 20 % over the same window. Estimating energy
from time × nameplate power overstates it by roughly six times.

**`charge_percentage` is noisy around a charge cycle.** Idling it falls cleanly,
but during and after charging it makes excursions of tens of percent within an
hour — enough that naively summing every rise accumulated 171 % of "gain" on a
day whose net change was −8 %. Measure net gain across a whole charging session
instead, or use the daily statistics.

## Heating

`schedule[0..6]` is 7 x 24 chars: `C` comfort, `E` eco, `O` anti-frost. There is
no off state — those three levels are the whole alphabet, and a fresh heater
reads as all-`O`. Index 0 = Monday, and **the end hour is exclusive**: a
02:00–03:00 window sets exactly one character, at index 2.

`schedule_type`: 0 custom, and 1–4 are the presets named Week routine, Weekends
off, Home all day, Away.

| Field | Meaning |
|---|---|
| `status` | `comfort` / `eco` / `ice`, or **`none`** when the heater is in a state that is not one of the three |
| `power` | 1 standby, 2 on |
| `mode` | 0 manual, 1 following the schedule |
| `comfort` / `eco` / `ice` | the three setpoints |
| `temp` | the active setpoint, mirroring whichever mode is current — read it, do not write it |
| `temp_probe` | room temperature |
| `temp_surface` | surface/core temperature |

## The extra heating element

The Onyx has a direct heating element as well as the storage core, and four flags
say which modes may fire it. `res` is *resistencia*.

| Field | App control |
|---|---|
| `res_auto_config` | Extra heating element → Automatic Mode |
| `res_boost_config` | → Boost Mode |
| `res_man_config` | → Manual Mode |
| `res_holiday_config` | → Holiday Mode |

Worth knowing if you are load-shifting: with `res_boost_config` on, a Boost draws
real power rather than only moving stored heat.

## Boost is the timer

| Field | Meaning |
|---|---|
| `timer_mode` | boost on/off — the authority |
| `timer_config_temp` / `timer_config_time` | what a boost is **set** to: °C and seconds |
| `timer_temp` / `timer_time` | the boost **currently running**: °C, and an absolute **UTC unix epoch** end time |

Confirmed: a 2 h boost set at 12:32:07 local wrote `timer_time` for exactly
start + 7200 s. `timer_time` is left behind stale once a boost ends, so it must
never be used on its own to decide whether one is running.

Boost also forces `mode: 0`, and nothing puts the weekly programme back by
itself — not switching Boost off, not the timer expiring. Anything driving Boost
should remember the previous `mode` and restore it.

## Comfort, display and housekeeping

| Field | Meaning |
|---|---|
| `silence_mode` | Silent Mode — reduces fan speed |
| `windows_open_mode` / `windows_open_status` | open-window detection, and whether it is currently triggered |
| `dont_disturb_mode` | do-not-disturb: screen off and sounds muted for the period |
| `dont_disturb_start` / `_end` | seconds since local midnight |
| `dont_disturb_decrease` | Night Mode — drops the setpoint by up to 1.5 °C over 3 hours |
| `buzzer` | Sound, 0–10 |
| `backlight_on` | Backlight brightness, 0–10 |
| `backlight` | Backlight brightness in **standby**, 0–10 |
| `backlight_time` | seconds; the web app offers always-on / 10 / 30 |
| `block_remote` | Remote lock — locks editing on the heater's own panel |
| `is_alive`, `wifisignal`, `wifissid`, `utcoffset`, `utczone` | housekeeping |
| `product_type` 5, `product_model`, `product_brand`, `product_version`, `nominal_power`, `firmware_version` | identity |

`adelanto_enable` is **Early start** (*adelanto* = bringing forward), and it is in
the command channel, so it can be driven.

## Present but with no control anywhere

No app screen, phone or web, touches these on an Onyx. They look like shared
firmware across Rointe's range rather than anything this model uses:
`ledbar_on`, `ledbar_standby`, `ledbar_color_enable`, `color`, `offset_probe`,
`offset_surface`, `offset_floor`, `offset_humidity`, `has_floor_probe`,
`use_floor_probe`, `surface_probe_enabled`, `legionella_mode`,
`legionella_status`, `pir_mode`, `pir_datetime`, `pilot_mode`, `user_mode`,
`advance_enable` (distinct from `adelanto_enable`, and it never moves).

## Still unexplained

`tpfl` (45), `tpsf` (43), `status_warming` (2), `mgmt_modules` (1), `com_type`
(true), `power_supply_details` (0), `block_local`, `smart_reset`, `debug_mode`,
`check_updates_day` / `_now` / `_time`, and the units of `charging_consuption`.
None has a visible control, so they would need a firmware-level poke rather than
an app one.

## Method

Every mapping above was established the same way: dump both nodes, make **one**
change in the Nexa app, wait a poll, dump again, diff. Where two settings had to
be applied together the attribution is called out as uncertain rather than
guessed. Values that merely differ between the nodes are usually just the heater
lagging a command by a few minutes, not two different meanings — a mistake worth
avoiding.

Rointe answer bad credentials with **HTTP 418**, not 401 or 400, which is worth
special-casing so a typo does not look like an outage.
