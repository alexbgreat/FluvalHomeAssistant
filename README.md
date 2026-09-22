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
- `button` entity: make the light blink so it can be physically located.
- A persistent BLE connection with automatic reconnect, and periodic
  polling to stay in sync with changes made from the FluvalSmart app or
  the light's own buttons.

Programming the Auto/Pro on-device schedules from Home Assistant isn't
implemented yet — use the official app for that; this integration can
still switch the light in and out of whatever schedule is already stored
on it.

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

## Protocol notes

For anyone extending this integration or debugging with a BLE sniffer:

- GATT service `00001000-0000-1000-8000-00805f9b34fb`, write characteristic
  `...1001...`, notify characteristic `...1002...`.
- Every message (in both directions) is wrapped as
  `[0x54, (len(payload)+1) ^ 0x54, key ^ 0x54, *[b ^ key for b in payload]]`
  where `key` is an arbitrary random byte chosen by the sender. This is
  not encryption — the key travels with the message, obfuscated by a
  fixed XOR with `0x54` — but real devices and the official app expect it.
- The unwrapped payload is `[0x68, command, ...args, checksum]`, where
  `checksum` is the XOR of every preceding byte.
- Per-channel brightness is sent/read as tenths of a percent (0–1000),
  and a channel value of `0xFFFF` means "leave this channel unchanged" in
  a set-channels command.
- A light's model is a 2-byte big-endian ID broadcast in the BLE
  advertisement's manufacturer data.

See `custom_components/fluval_smart_ble/protocol.py` and `models.py` for
the full command set and the model/channel table.

This integration is not affiliated with or endorsed by Fluval/Hagen.
