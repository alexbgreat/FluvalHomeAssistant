# Fluval Smart BLE for Home Assistant

A Home Assistant custom integration that controls Fluval Smart aquarium
lights directly over Bluetooth LE — no hub, cloud account or the
FluvalSmart app required.

It was built by reverse engineering the FluvalSmart Android app (BLE GATT
usage, command framing and the trivial byte-obfuscation it applies on the
wire), since Fluval does not publish a protocol spec or a local API.

## Supported hardware

Any Fluval light that pairs with the FluvalSmart app over Bluetooth,
including the Marine & Reef, Fresh & Plant, AquaSky, Nano, Roma, Vicenza,
Venezia, A-Sky/Oak and Plant Aqua lines. The light's model is
auto-detected from its BLE advertisement, which determines its channel
count and channel names. An unrecognized model still works, falling back
to a generic 4-channel RGBW layout.

Lights that only support Wi-Fi (no Bluetooth) are not supported.

## Features

- Config-flow setup: Home Assistant auto-discovers nearby lights over
  Bluetooth; pick one to add it.
- `light` entity: on/off, plus brightness or full RGBW color control on
  models with red/green/blue/white channels.
- `number` entities: direct 0–100% control of every individual LED
  channel (matching the sliders in the manual tab of the official app),
  for models whose channels don't map onto a single color.
- `select` entity: switch the light between Manual, Auto (sunrise/sunset
  ramp), Pro (multi-point schedule) and Sun sync modes.
- Sun sync mode: Home Assistant recomputes the Auto schedule every day
  from the real sunrise and sunset at your location (with configurable
  offsets) and pushes it to the light nightly — see
  [Sun sync](#sun-sync).
- `button` entities: make the light blink so it can be physically
  located, and push the current time to the light's on-board clock on
  demand.
- Automatic time sync: the light's Auto/Pro schedules run against its
  own clock, which drifts and resets on power loss, so this integration
  pushes Home Assistant's current local time to it on every connection.
- A persistent BLE connection with automatic reconnect, and periodic
  polling to stay in sync with changes made from the FluvalSmart app or
  the light's own buttons.

- **Aquarium Light** sidebar panel: program the light's on-device Auto
  (sunrise/sunset) and Pro (4-10 point) schedules from Home Assistant,
  with a live 24-hour brightness chart — see
  [Configuring the light schedule](#configuring-the-light-schedule).

## Installation

### HACS

Add this repository as a custom repository in HACS, then install
"Fluval Smart" and restart Home Assistant.

### Manual

Copy `custom_components/fluval_smart_ble` into your Home Assistant
`config/custom_components/` directory and restart Home Assistant.

## Setup

Home Assistant's Bluetooth integration must be enabled and have an
adapter (or an ESPHome/Shelly Bluetooth proxy) in range of the light.
Once the light is discovered, a notification invites you to add it under
**Settings → Devices & Services**. You can also add it manually from
**Settings → Devices & Services → Add Integration → Fluval Smart**.

## Configuring the light schedule

The light runs its Auto and Pro schedules by itself, against its own
clock, so they keep working even when Home Assistant is offline. To edit
them, open **Aquarium Light** in the Home Assistant sidebar (it appears
for admin users once a light is set up). If you have several lights,
pick one from the drop-down. The panel also shows and switches the
light's current mode (Manual / Auto / Pro / Sun sync), and has three tabs:

- **Auto schedule**: the sunrise window (the light fades from night to
  day brightness), the sunset window (it fades back), day and night
  brightness sliders for each LED channel, and an optional fixed daily
  turn-off time.
- **Pro schedule**: a table of 4-10 points, each a time of day and a
  brightness for each LED channel. Add or remove points as needed; the
  light interpolates between consecutive points, wrapping around
  midnight.
- **Sun sync**: see [below](#sun-sync).

Both tabs plot the resulting brightness of every channel across the day
as you edit.

Brightness is a whole percentage per channel. Saving sends the schedule
to the light right away and, unless you untick the option, switches the
light into that mode. The panel is prefilled with the schedule the
light last reported (it only reports the one for its active mode; use
**Refresh** to re-read it), otherwise with what was last saved from Home
Assistant.

### Dynamic effects

Each of the Auto, Pro and Sun sync tabs has a **Dynamic effect** section:
the light can layer one of its built-in effects — Thunderstorm 1-3, All
colors, Cloudy 1-4 or Moonlight 1-3 — over the schedule during a daily
window, on the days of the week you pick. The window can run past
midnight (e.g. moonlight 22:00-01:00), and it's shaded on the chart.
Each schedule has its own effect; an effect set up in the FluvalSmart app
is shown and can be edited here.

**Preview on light** asks the light to play the selected effect right
away. That command's behaviour hasn't been confirmed on real hardware,
so treat it as experimental.

The schedule commands were reverse engineered from the app but haven't
been exercised against every model; if a light doesn't behave as
expected after saving, enable debug logging for
`custom_components.fluval_smart_ble` and open an issue with the logged
frames.

### Sun sync

The light's own clock only knows the time of day, so its Auto mode fades
at the same times all year. Sun sync is a mode run by Home Assistant on
top of it: every night Home Assistant works out the coming day's sunrise
and sunset for the location set under **Settings → System → General**,
applies your offsets, and writes the result to the light as its Auto
schedule (the light stays in Auto mode and runs it by itself).

In the **Sun sync** tab you set:

- **Sunrise / sunset fade**: when each fade starts relative to the sun
  event (negative = before; up to ±6 hours) and how long it lasts (up to
  4 hours). The default sunrise fade starts at sunrise, and the default
  sunset fade ends exactly at sunset (starts 60 min before, lasts 60).
- **Day / night brightness** per channel, and an optional daily turn-off
  time, as in the Auto tab.
- **Nightly update** time (default 03:00): when the new day's schedule is
  pushed. Pick a time between sunset and sunrise. If the light can't be
  reached then, Home Assistant retries every 10 minutes for 3 hours. A
  push is also made whenever Home Assistant starts, to catch up on nights
  it was offline.

The chart shows the day's sun times and the resulting fades as you edit.
**Turn on & push now** saves the settings, pushes today's schedule (a
push made after noon uses the next day's sun times) and switches the
light to Auto. Settings that would make a fade run past midnight or
overlap the other fade are rejected, since the light's schedule is a
single day.

Choosing any other mode — from the panel or the `select` entity — turns
sun sync off, as does saving a schedule from the Auto tab (or saving a
Pro schedule that switches to Pro mode), so the nightly push never
overwrites a schedule you set by hand. If the light is switched out of
Auto from the FluvalSmart app, the mode shows as that mode, and the
nightly push keeps the stored Auto schedule current without switching
the light back.

## Protocol documentation

Fluval publishes no protocol spec; everything this integration does was
recovered by reverse engineering the FluvalSmart Android app. The full
write-up lives in [`docs/`](docs/), split by topic:

- **[docs/PROTOCOL.md](docs/PROTOCOL.md)** - the master reference:
  BLE transport, the wire-obfuscation layer, frame format, the full
  command table, the multi-chunk response reassembly quirk that caused
  this integration's early "everything shows Unavailable" bug, and how
  a light's model is identified from its BLE advertisement.
- **[docs/SCHEDULING.md](docs/SCHEDULING.md)** - the Auto/Pro on-device
  schedule commands (sunrise/sunset ramps, multi-point timelines,
  scheduled "dynamic effects" like storm/cloud/moonlight simulation).
  Documented for future use; not implemented by this integration today.
- **[docs/OTA.md](docs/OTA.md)** - the firmware update mechanism.
  Reference only, and deliberately **not** implemented here - see that
  page for why.

This integration is not affiliated with or endorsed by Fluval/Hagen.
