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

Programming the Auto/Pro on-device schedules from Home Assistant isn't
implemented yet — use the official app for that; this integration can
still switch the light in and out of whatever schedule is already stored
on it. The scheduling commands are fully documented for future work in
[docs/SCHEDULING.md](docs/SCHEDULING.md).

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
