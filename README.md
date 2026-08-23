# Rointe Nexa for Home Assistant

Control Rointe heaters — including the **Onyx storage heater**, which no other
integration supports — from Home Assistant.

## Why this exists

The two existing community integrations target Rointe's **v3** platform. Accounts
created on **v4** (`api-v4-prod.rointe.io`) fail to authenticate against them, and
storage heaters are not modelled at all. This integration was written after
reverse-engineering the v4 web app against a live Onyx.

Three things it does differently, none of which are guessable:

1. **Firebase is not signed into with your password.** The app derives a
   credential from your Rointe user id — `{user_id}@rointe.com` as the email,
   the same uuid as the password.
2. **Commands go to the zone node, not the device node.** `/zones/{id}/data` is
   the command channel; `/devices/{serial}/data` is the heater's report back.
   Writing to the device node returns HTTP 200, reads back fine, and is
   silently overwritten by the heater minutes later.
3. **`last_sync_datetime_device` is never written.** It is the heater's own sync
   marker. Bumping it makes the heater discard the change.

## Install

Copy `custom_components/rointe_nexa` into your Home Assistant `config/custom_components/`,
restart, then **Settings → Devices & Services → Add Integration → Rointe Nexa**
and sign in with your Nexa account.

## Entities

| Entity | Notes |
|---|---|
| `climate` | Comfort / Eco / Anti-frost presets, target temperature, on/off |
| `climate` HVAC mode | **Auto** = the heater runs its own weekly programme. **Heat** = a mode was picked by hand and holds. **Off** = standby. Picking a preset (or Boost) drops it to Heat; set it back to Auto to hand the programme back. |
| `sensor` Charge | **State of charge, %** — the number worth graphing |
| `sensor` Core temperature | The storage core, typically 40–70 °C |
| `sensor` Room temperature | |
| `binary_sensor` Charging / Open window / Online | |
| `number` Maximum / Minimum charge | |
| `number` Additional charge | Hours the heater may charge *outside* its window. Set 0 to stop it wandering into expensive hours. |
| `switch` Boost | The app's Boost — the timer, with its own temperature and duration. Remembers the mode it interrupted and hands it back when the boost ends, whether switched off or run out of time. |
| `switch` Smart charging | Rointe's optimiser. **Turn it off** if you want Home Assistant to pick the charging hours. |

## Services

### `rointe_nexa.set_charge_hours`

The one that matters. Choose which hours the heater may charge:

```yaml
service: rointe_nexa.set_charge_hours
target:
  entity_id: climate.living_room
data:
  hours: [1, 2, 3, 4, 5]     # 01:00–06:00
  days: [0, 1, 2, 3, 4]      # Mon–Fri, omit for every day
```

Hour numbers are the hour that *starts* then, so `[2]` is 02:00–03:00.

### `rointe_nexa.set_heating_schedule`

24 characters, one per hour: `C` comfort, `E` eco, `O` anti-frost.

### `rointe_nexa.boost`

```yaml
service: rointe_nexa.boost
target:
  entity_id: climate.living_room
data:
  temperature: 21
  duration: 60      # minutes
```

## Driving it from electricity prices

With [BottlecapDave's Octopus Energy integration](https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy),
a target-rate sensor picks the cheapest window and this writes it to the heater.
Turn **Smart charging** off first, or Rointe's optimiser gets a say too.

```yaml
automation:
  - alias: Rointe - charge in the cheapest hours
    triggers:
      - trigger: time
        at: "22:00:00"
    actions:
      - action: rointe_nexa.set_charge_hours
        target:
          entity_id: climate.living_room
        data:
          hours: >
            {{ state_attr('binary_sensor.octopus_energy_target_heating',
                          'target_times')
               | map(attribute='start') | map('as_datetime') | map('as_local')
               | map(attribute='hour') | unique | list }}
```

On Agile, run this after 16:00 when the following day's rates publish.

## Example dashboard

`examples/` carries the Home Assistant side of this setup.

`examples/packages/rointe_heating.yaml` is a package of helper sensors the
integration does not provide: an Economy 7 window that follows two time helpers,
charge headroom against the ceiling you set, hours spent charging today, an
estimated kWh from the 1560 W element, and a snapshot of the charge left the
moment the cheap rate ends — the number that answers whether the cheap window
actually fills the heater. Copy it into `config/packages/` and add

```yaml
homeassistant:
  packages: !include_dir_named packages
```

to `configuration.yaml`.

`examples/dashboards/heating.json` is a two-view dashboard — live state, charge
and core temperature over 24 h and 7 d, room comparison, charge controls, and
30-day statistics. `tablet-heating-view.json` is a trimmed single view for a wall
tablet. Paste either into a dashboard's raw configuration editor. Both need
[Mushroom](https://github.com/piitaya/lovelace-mushroom) and
[ApexCharts Card](https://github.com/RomRider/apexcharts-card) from HACS, and both
use the entity ids of one heater named SH.453AB4 — search and replace for yours.

## Limitations

- **Cloud only.** No local API, no Matter. No internet means no control; the
  heater keeps running its own schedule.
- **Polling**, every 60 s. The app uses a Firebase websocket; polling is far
  less code for a device whose state moves on the scale of minutes.
- Commands are **zone-scoped**. Two heaters in one Nexa zone share settings.
- Writable settings are read back from the **zone** node, which the app writes
  and the heater catches up to a few minutes later; physical readings come from
  the **device** node. Mixing the two makes a setting appear to snap back.
- Unofficial and unaffiliated with Rointe, who declined to discuss API access
  with the community. They have broken third-party integrations before.

## Credit

Builds on the reverse-engineering in
[tggm/rointe-radiators](https://github.com/tggm/rointe-radiators) and
[alex1075/rointe-hacs](https://github.com/alex1075/rointe-hacs).
