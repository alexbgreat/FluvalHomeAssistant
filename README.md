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
  ramp) and Pro (multi-point schedule) modes.
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
light's current mode (Manual / Auto / Pro), and has two tabs:

- **Auto schedule**: the sunrise window (the light fades from night to
  day brightness), the sunset window (it fades back), day and night
  brightness sliders for each LED channel, and an optional fixed daily
  turn-off time.
- **Pro schedule**: a table of 4-10 points, each a time of day and a
  brightness for each LED channel. Add or remove points as needed; the
  light interpolates between consecutive points, wrapping around
  midnight.

Both tabs plot the resulting brightness of every channel across the day
as you edit.

Brightness is a whole percentage per channel. Saving sends the schedule
to the light right away and, unless you untick the option, switches the
light into that mode. The panel is prefilled with the schedule the
light last reported (it only reports the one for its active mode; use
**Refresh** to re-read it), otherwise with what was last saved from Home
Assistant. A dynamic effect
(storm/cloud/moonlight) set up from the FluvalSmart app is left as is.

The schedule commands were reverse engineered from the app but haven't
been exercised against every model; if a light doesn't behave as
expected after saving, enable debug logging for
`custom_components.fluval_smart_ble` and open an issue with the logged
frames.

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
