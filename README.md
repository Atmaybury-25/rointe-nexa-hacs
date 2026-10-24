# Rointe Nexa for Home Assistant

Control Rointe heaters from Home Assistant — including the **Onyx high heat
retention storage heater**, which no other integration supports.

Storage heaters are worth automating. They buy electricity at one time of day and
release it at another, so the whole game is deciding *when* to charge and *how
much*. That decision wants to be made against half-hourly prices and a room
temperature sensor, not by a wall dial. This integration exposes the levers that
make that possible: charge percentage, charge ceiling and floor, the weekly
charging schedule, boost, and Rointe's own optimiser.

## Read this before installing

**This is vibe-coded.** It was written in a single long session by Claude,
driven by someone who owns one of these heaters, against an API that Rointe do
not document and have never published. Nothing here is blessed by Rointe.

What that means in practice:

- **It works, but it is a work in progress.** It has been running against exactly
  one heater, one account, and one firmware version. Everything in it was
  verified against that heater, and nothing in it has been verified against
  yours.
- **Expect bugs.** Especially around device types this has never seen — radiators,
  towel rails, water heaters, multi-heater zones, anything other than one Onyx in
  one zone.
- **The field map is incomplete.** 115 fields come back from the device node and
  a good few are still unidentified. A couple of guesses in here are labelled as
  guesses; the rest were confirmed by changing something in the Nexa app and
  watching which field moved.
- **Rointe can break this at any time.** They broke every third-party integration
  once already when they migrated from v3 to v4, and they declined to discuss API
  access with the community.
- **It writes to your heating.** Read [Safety](#safety) below.

**Please fork it.** Send a PR, open an issue, or take it somewhere better — that
is the point of it being here. If you have a Rointe device that is not an Onyx,
a dump of its RTDB node would be genuinely useful.

## Why it exists

The two existing community integrations target Rointe's **v3** platform. Accounts
created on **v4** (`api-v4-prod.rointe.io`) cannot authenticate against them at
all, and storage heaters are not modelled anywhere. This was written after
reverse-engineering the v4 web app against a live Onyx.

Three things it does differently, none of which are guessable:

1. **Firebase is not signed into with your password.** The app derives a
   credential from your Rointe user id — `{user_id}@rointe.com` as the email, the
   same uuid as the password. Signing in with your real email and password
   returns `INVALID_LOGIN_CREDENTIALS`, which is what every "invalid
   authentication" bug report against the older integrations turns out to be.
2. **Commands go to the zone node, not the device node.** `/zones/{id}/data` is
   the command channel; `/devices/{serial}/data` is the heater reporting back.
   Writing to the device node returns HTTP 200, reads back correctly, and is
   silently overwritten by the heater a few minutes later.
3. **`last_sync_datetime_device` is never written.** It is the heater's own sync
   marker. Bumping it makes the heater discard the change.

There is a fourth rule that shapes every entity: anything **writable** is read
back from the zone node, and anything **physical** from the device node. Mix them
up and a setting the user just changed appears to snap back to its old value for
several minutes, because the heater has not caught up yet.

**[docs/onyx-field-map.md](docs/onyx-field-map.md) is the full field map** — all
97 keys the heater reports, what each one means where it is known, which are
vestigial shared firmware, and which are still unexplained. It is the reference
this integration was built from, and the place to look before adding anything.

## Install

**HACS (recommended).** HACS → three-dot menu → *Custom repositories* → add this
repository's URL with category **Integration** → then find *Rointe Nexa* and
download it. Restart Home Assistant.

**Manually.** Copy `custom_components/rointe_nexa` into your Home Assistant
`config/custom_components/` and restart.

Either way, then go to **Settings → Devices & Services → Add Integration →
Rointe Nexa** and sign in with your Nexa account. Your password is used once, to
get a user id from Rointe's REST API; the realtime layer never sees it.

## Entities

| Entity | Notes |
|---|---|
| `climate` | Comfort / Eco / Anti-frost presets, target temperature, on/off |
| `climate` HVAC mode | **Auto** = the heater runs its own weekly programme. **Heat** = a mode was picked by hand and holds. **Off** = standby. Picking a preset (or Boost) drops it to Heat; set it back to Auto to hand the programme back. |
| `sensor` Charge | **State of charge, %** — the number worth graphing |
| `sensor` Core temperature | The storage core, typically 40–70 °C |
| `sensor` Room temperature | The heater's own probe |
| `binary_sensor` Charging / Open window / Online | |
| `binary_sensor` Travel mode | Whether the installation is in the app's Travel mode. Attributes list the periods. Same value on every heater in the installation. |
| `number` Maximum / Minimum charge | The limits Rointe's **Smart charging** aims between. Same ranges as the Nexa app: minimum 0–30 %, maximum 60–100 %, steps of 5, and both are always written together. Not a way to stop a heater charging — see Travel mode. |
| `number` Additional charge | Hours the heater may charge *outside* its window, 0–7. Set 0 to stop it wandering into expensive hours. |
| `number` Comfort / Eco / Anti-frost temperature | The three stored setpoints the weekly programme switches between. The climate target temperature only writes whichever preset is active right now, so use these to change one that is not. |
| `switch` Boost | The app's Boost — the timer, with its own temperature and duration. Remembers the mode it interrupted and hands it back when the boost ends, whether switched off or run out of time. |
| `switch` Smart charging | Rointe's optimiser. **Turn it off** if you want Home Assistant to pick the charging hours. |
| `switch` Extra element in auto / manual / boost / travel mode | Configuration. Whether the direct-acting element (as well as the storage core) may heat in each mode. With one off, that mode runs on stored heat only. |
| `switch` Silent mode | Configuration. The app's silent mode. |

Heaters added in the Nexa app are picked up automatically within 30 minutes; no
reload needed.

Like the app, the integration refuses to change settings on a heater that has
been switched off at the unit, except to switch it back on. Climate shows such a
heater as **Off** (attribute `switched_off_at_heater`).

Note that Boost releases stored heat; it does not take a charge. If you are
trying to buy cheap electricity, `set_charge_hours` is the service you want.

## Services

### `rointe_nexa.set_charge_hours`

The one that matters. Choose which hours the heater may charge:

```yaml
action: rointe_nexa.set_charge_hours
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
action: rointe_nexa.boost
target:
  entity_id: climate.living_room
data:
  temperature: 21
  duration: 60      # minutes
```

### `rointe_nexa.set_travel_mode` / `rointe_nexa.clear_travel_mode`

The Nexa app's Travel mode. Rointe's servers hold the period and switch every
product in the installation off for it, so it keeps working if Home Assistant
or your internet goes down mid-trip.

```yaml
action: rointe_nexa.set_travel_mode
target:
  entity_id: climate.living_room   # any heater in the installation
data:
  start: "2026-12-20 08:00:00"     # local to the installation
  end: "2026-12-27 12:00:00"       # omit for indefinite
```

`clear_travel_mode` removes the periods and switches travel mode off. Whether
the direct element may still run while away is the *Extra element in travel
mode* switch.

Do **not** use Maximum charge as a "stop charging" lever. Earlier versions
allowed writing it below 60 %; the heater is only ever sent 60–100 % by
Rointe's own app and did not reliably stop charging at 0 %.

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

Worth knowing: `schedule_charging` is **hourly**, not half-hourly. On a
half-hourly tariff you are picking the best hours, not the best slots, and there
is roughly a minute of polling plus a few minutes of heater sync between the
write and the heater acting on it.

## Example dashboard

`examples/` carries the Home Assistant side of this setup.

`examples/packages/rointe_heating.yaml` is a package of helper sensors the
integration does not provide: an Economy 7 window that follows two time helpers,
charge headroom against the ceiling you set, hours spent charging today, an
estimated kWh from the element wattage, and a snapshot of the charge left the
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
use the entity ids of a heater named SH.ABC123 — search and replace for yours.

## Safety

This writes to a fixed heating appliance over an undocumented cloud API. It can
change setpoints, charge limits and schedules. It cannot make the heater exceed
its own limits — every value goes through Rointe's own platform and the heater's
firmware — but a wrong schedule can still leave you cold, or charging at the most
expensive time of day. Sanity-check any automation before trusting it through a
winter, and keep an eye on the charge percentage for the first few weeks.

If you rely on this heating for anyone vulnerable, do not make it the only thing
standing between them and a cold house.

## Limitations

- **Cloud only.** No local API, no Matter. Every port on the heater is closed.
  It does have a Bluetooth control channel (the Android app can read and write
  the same fields over BLE), but the heater only advertises in pairing mode, so
  it is not usable day to day. No internet means no control; the
  heater keeps running its own schedule.
- **Polling**, every 60 s. The app uses a Firebase websocket; polling is far
  less code for a device whose state moves on the scale of minutes.
- Commands are **zone-scoped**. Two heaters in one Nexa zone share settings, so
  give each heater its own zone in the Nexa app.
- Writable settings are read back from the **zone** node, which the app writes
  and the heater catches up to a few minutes later; physical readings come from
  the **device** node.
- Only tested against one Onyx storage heater on one v4 account.
- Unofficial and unaffiliated with Rointe.

## Contributing

Issues and pull requests welcome, and forks even more so. Useful contributions,
roughly in order of value:

1. An RTDB `/devices/{serial}` dump from a Rointe device that is **not** an Onyx —
   scrub the address, coordinates, serial, MAC and wifi SSID before posting.
2. Identification of any of the unmapped fields, particularly `advance_enable` /
   `adelanto_enable`, and the units of
   `charging_consuption` (their typo, not mine).
3. Whether writing the installation-level 24-slot `tariff` array actually steers
   Rointe's smart charging, which would be a far more durable control surface
   than writing schedule fields.
4. Anything that makes this less of a one-heater integration.

The Firebase API key in `api.py` is Rointe's own, taken from the public web
bundle at `rointenexa.com`, and is not a secret belonging to any user.

## Credit

Builds on the reverse-engineering in
[tggm/rointe-radiators](https://github.com/tggm/rointe-radiators) and
[alex1075/rointe-hacs](https://github.com/alex1075/rointe-hacs).

## Licence

MIT — see [LICENSE](LICENSE).
