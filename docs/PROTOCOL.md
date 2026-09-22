# Fluval Smart BLE protocol reference

This is the master reference for the Bluetooth LE protocol spoken by
Fluval Smart lights. It was recovered entirely by decompiling the
FluvalSmart Android app (jadx) and disassembling the small native
library it bundles (`libhy_api.so`) - Fluval publishes no protocol
spec. Everything below is either read directly from the app's
decompiled Java, or was proven against a real light's debug logs; where
something is inferred rather than confirmed, that's called out
explicitly.

This document covers transport, framing, and the full command set,
including commands this integration does not implement. Two areas get
their own dedicated documents because of their size and risk:

- **[SCHEDULING.md](SCHEDULING.md)** - the Auto/Pro on-device schedule
  commands (`CMD_CYCLE`, `CMD_PRO`, `CMD_DYNAMIC_PERIOD`, and related).
- **[OTA.md](OTA.md)** - the firmware update ("OTA"/bootloader)
  mechanism. Reference only; deliberately not implemented here.

The source of truth for what this integration actually implements today
is `custom_components/fluval_smart_ble/protocol.py`, `coordinator.py`
and `const.py`.

## 1. Transport

- **GATT service**: `00001000-0000-1000-8000-00805f9b34fb`
- **Write characteristic**: `00001001-0000-1000-8000-00805f9b34fb`
  (`CHARACTERS[0]` in the app; commands are written here)
- **Notify characteristic**: `00001002-0000-1000-8000-00805f9b34fb`
  (`CHARACTERS[1]`; responses/state arrive here as notifications)
- Two further characteristics (`...1003...`, `...1004...`) exist on the
  same service and are used only by the generic BLE-module
  configuration sub-protocol described in [§6](#6-the-register-sub-protocol-out-of-scope).
  Nothing this integration needs lives there.
- **Write type**: the app inspects the write characteristic's GATT
  properties at connect time and prefers `write-without-response` if the
  characteristic advertises it, falling back to `write` (with response)
  otherwise. This integration does the same
  (`coordinator.py:_async_ensure_connected`).
- **Chunking**: a logical message longer than 17 bytes is split into
  ≤17-byte pieces before being wrapped (see §2) and sent as separate
  writes/notifications, with an ~8ms gap between write chunks. 17 was
  chosen to fit comfortably under the smallest negotiated ATT MTU (23
  bytes: 20 usable payload bytes, minus the 3-byte wire header from
  §2 = 17 payload bytes per chunk).
- **Connection settle time**: the app waits 300ms after GATT service
  discovery before enabling notifications, then a further 400ms before
  it will send anything. Empirically (see the git history of this
  integration's `coordinator.py`), writing to this BLE module too soon
  after connecting gets silently dropped, so this integration mirrors
  that ~700ms grace period (`CONNECT_SETTLE_DELAY`).

## 2. Wire obfuscation layer

Every message, in both directions, is wrapped by a scheme implemented
natively in `libhy_api.so` (JNI functions
`Java_com_ble_api_EncodeUtil_encodeMessage` /
`..._decodeMessage`, called from `BleService.send()` /
`onCharacteristicChanged()` whenever `setDecode(true)` is active, which
it always is). It was recovered by disassembling the x86_64 build of
that library (5.8KB, stripped, two exported JNI functions) - there is no
decompiler for this, it was read directly out of the `objdump -d`
disassembly.

Given a plaintext frame `payload` (see §3), the wire bytes are:

```
wire[0]   = 0x54
wire[1]   = (len(payload) + 1) ^ 0x54      (payload length, obfuscated)
wire[2]   = key ^ 0x54                     (key is any random byte, chosen per message)
wire[3:]  = [b ^ key for b in payload]
```

To decode: `key = wire[2] ^ wire[0]`, then `payload = [b ^ key for b in
wire[3:]]`. **This is not encryption** - the key travels inside the
message itself, obfuscated by a fixed XOR with the constant `0x54`
(`'T'`). Anyone can decode it with no shared secret. It exists purely
because the app author's tooling produces this shape by default; it
adds no real confidentiality or authentication.

Implemented in `protocol.py` as `encode_message()` / `decode_message()`.

## 3. Plaintext frame format

```
payload[0]    = 0x68              ("FRM_HDR" in the app)
payload[1]    = command byte      (see §5)
payload[2:-1] = command arguments (command-specific, see §5)
payload[-1]   = checksum = XOR of every byte before it
```

The checksum is a simple running XOR (`CommUtil.getCRC()` in the app);
there is no polynomial CRC despite the app's naming. Verifying it is
equivalent to checking that XORing the *entire* frame (including the
checksum byte) together yields zero.

Implemented as `build_frame()` / `xor_checksum()` in `protocol.py`.

## 4. Multi-chunk reassembly (the important gotcha)

This is the single most important, least obvious fact about this
protocol, and got this integration's first several releases stuck with
every entity reporting Unavailable. It is worth being explicit about it
here so nobody re-discovers it the hard way.

**A response longer than ~17 plaintext bytes is *not* one wire message
(§2) split across BLE notification packets by the transport.** Each BLE
notification is its own complete, independently-wrapped chunk - its own
random `key`, its own 3-byte `[0x54, len, key]` header - exactly
mirroring how the app's own `BleManager.sendBytes()` chunks and
independently wraps *outgoing* writes longer than 17 bytes
(`BleManager.java`, the `while (i < bArr.length)` loop, each iteration
calling `bleService.send()` - and encoding - on its own ≤17-byte slice).
The device does the same thing in reverse for its responses.

The correct algorithm is therefore: **decode each notification
independently, then concatenate the decoded plaintexts** - not
concatenate the raw wire bytes and decode once. Concatenating raw bytes
and decoding as a single message produces bytes that look plausible for
a while (the leading bytes decode correctly, since chunk 1's key is
right for chunk 1's bytes) and then degrade into garbage past the first
chunk boundary, with an invalid checksum - which is exactly the failure
signature that flagged this bug in the first place.

Worked real-world example (captured from a live Aquasky 600mm, see the
project's commit history for the full debug-log capture):

```
notification 1 (20 raw bytes): 54466559343038313b3114121413213120313131
  decodes alone to (17 bytes): 68050109000a0025232522100011000000
  -> checksum of this alone is INVALID (0x6f != trailing 0x00)

notification 2 (15 raw bytes): 5459d3858786918778808792878314
  decodes alone to (12 bytes): 0200011600ff070015000493
  -> doesn't even start with 0x68, not a frame on its own

concatenating the two DECODED plaintexts (17 + 12 = 29 bytes):
  68050109000a00252325221000110000000200011600ff070015000493
  -> valid checksum, and 29 bytes is exactly channelCount*2+21 (see
     SCHEDULING.md §Auto), the length of a 4-channel Auto-mode read
     response with both the turn-off and dynamic-effect fields present.
```

Reassembly also needs a way to know when a message is "done" without
knowing its expected length in advance (response length varies by mode
and by which optional fields are present - see SCHEDULING.md). This
integration's approach: after decoding and appending each chunk, check
whether the accumulated plaintext is itself a valid, checksummed frame
(`frame[0] == 0x68` and the running XOR is zero); if not, wait for the
next chunk. A checksum passing by chance on a genuinely incomplete
buffer is a 1-in-256 fluke per attempt, which in practice never happens
across the 2-3 chunks any real response needs.

The app's own analogous logic (`BleManager.mRcvBytes` /
`CommUtil.mRcvBytes`) clears its accumulation buffer if more than 64ms
passes between notifications; this integration mirrors that timeout
(`REASSEMBLY_TIMEOUT` in `protocol.py`). In practice, chunks for one
response arrive roughly 1ms apart.

Implemented as `FrameReassembler` in `protocol.py`.

## 5. Command reference

All commands share the frame format from §3. "Dir" is `→` (app/HA to
light) or `←` (light to app/HA, i.e. a notification).

| Byte | Name | Dir | Args | Status here |
|---|---|---|---|---|
| `0x02` | `CMD_MODE` | → | 1 byte: `0`=Manual, `1`=Auto, `2`=Pro | Implemented (`select` entity) |
| `0x03` | `CMD_SWITCH` | → | 1 byte: `0`=off, `1`=on | Implemented (`light` on/off) |
| `0x04` | `CMD_CTRL` | → | `2×N` bytes: per-channel brightness, **big-endian** uint16, tenths of a percent (0-1000 = 0.0-100.0%); `0xFFFF` means "leave this channel unchanged" | Implemented (`light` brightness/RGBW, `number` per channel) |
| `0x05` | `CMD_READ` | → / ← | no args (request); response layout depends on current mode, see below | Implemented (state polling) |
| `0x06` | `CMD_CUSTOM` | → | 1 byte: preset slot index `0`-`3` | Reference only, see SCHEDULING.md |
| `0x07` | `CMD_CYCLE` | → | Auto-mode schedule, see SCHEDULING.md | Implemented (options flow schedule editor) |
| `0x08` | `CMD_CHN_INC` | → | 2 bytes: channel bitmask, amount | Reference only, see SCHEDULING.md |
| `0x09` | `CMD_CHN_DEC` | → | 2 bytes: channel bitmask, amount | Reference only, see SCHEDULING.md |
| `0x0A` | `CMD_DYN` | → | 1 byte: dynamic-effect ID | Reference only, see SCHEDULING.md |
| `0x0B` | `CMD_PREVIEW` | → | same layout as `CMD_CTRL` | Reference only, see SCHEDULING.md |
| `0x0C` | `CMD_STOP_PREVIEW` | → | no args | Reference only |
| `0x0D` | `CMD_READTIME` | → / ← | no args (request); response layout not captured | Reference only (request builder exists conceptually; not wired up) |
| `0x0E` | `CMD_SYNCTIME` | → | 7 bytes: year-2000, month(0-based), day, weekday(0=Sun..6=Sat), hour, minute, second | **Implemented** (auto-sync on connect + "Sync Time" button) |
| `0x0F` | `CMD_FIND` | → | no args | Implemented ("Find" button) |
| `0x10` | `CMD_PRO` | → | Pro-mode schedule, see SCHEDULING.md | Implemented (options flow schedule editor) |
| `0x11` | `CMD_DYNAMIC_PERIOD` | → | 6 bytes: week bitmask, RampTime×4, mode | Reference only, see SCHEDULING.md |

Only `CMD_READ` is known to produce a notification in reply; the app's
own send path (`sendBytes`) never blocks waiting for one, so most
commands appear to be fire-and-forget as far as the client is concerned
(the light may still notify unsolicited on its own trigger - a physical
button press, for instance - which is why the coordinator's reassembler
has to be generically ready to receive at any time, not just after a
`CMD_READ`).

### `CMD_READ` response layout

The response always starts `[0x68, 0x05, mode, ...]`. `mode` (byte
offset 2) tells you which of three shapes follows, and therefore which
total length to expect:

- **`mode == 0` (Manual)**: fixed length `channelCount*6 + 6`.
  - byte 3: bit 0 = on/off
  - byte 4: "dynamic" byte (purpose not fully confirmed - not the same
    field as the Auto/Pro dynamic-effect block; possibly a currently
    active custom-preset index)
  - bytes `5 .. 5+2N-1`: per-channel brightness, **little-endian**
    uint16, tenths of a percent - note this is the *opposite* byte
    order from the `CMD_CTRL` request (§ above is big-endian). Confirmed
    both ways against the decompiled app; not a typo.
  - bytes after that: four more `channelCount`-length byte arrays,
    the four saved "custom preset" snapshots (see `CMD_CUSTOM` in
    SCHEDULING.md) - not currently parsed by this integration since
    nothing here needs them.
  - Implemented in `protocol.py:parse_read_response()`.
- **`mode == 1` (Auto)**: one of four lengths depending on which
  optional fields are present. Fully detailed in SCHEDULING.md. The
  bytes after the mode byte have the same layout as `CMD_CYCLE`'s args;
  decoded best-effort to prefill the schedule editor.
- **`mode == 2` (Pro)**: variable length depending on point count.
  Fully detailed in SCHEDULING.md. Same story - the bytes after the mode
  byte mirror `CMD_PRO`'s args and are decoded best-effort.

## 6. Model identification (BLE advertisement)

Before ever connecting, the app (and this integration's config flow)
identifies which Fluval model a discovered device is from its BLE
advertisement, using the numeric IDs in `DeviceUtil.LIGHT_ID_*` (see
`custom_components/fluval_smart_ble/models.py` for the full table this
integration ships).

**The encoding is not a normal, spec-compliant manufacturer-data
payload.** The BLE module doesn't use a real Bluetooth SIG company ID.
Instead, the *entire* manufacturer-data field - both the nominal 2-byte
"company ID" and the payload that follows it - is read as one
continuous byte stream, and the first 4 bytes of that stream are ASCII
digit characters (`'0'`-`'9'`), each treated as one hex nibble of the
decimal model ID:

```
value = 0
for byte in first_4_bytes_of(company_id_bytes + payload_bytes):
    value = (value << 4) | (byte - ord('0'))
```

Confirmed against a real Aquasky 600mm's advertisement:
`manufacturer_data = {0x3130: bytes.fromhex("3431303130330000...")}`.
Reconstructing the raw over-the-air bytes (`company_id` is little-endian,
so byte order is `[company_id & 0xFF, company_id >> 8, *payload]`) gives
ASCII `"01410103..."`; the first four characters `"0141"`, read as hex
nibbles, are `0x0141 = 321 = LIGHT_ID_AQUASKY_600`.

This only works because every currently-known model ID happens to have
a hex representation using only the digits `0`-`9` (no `A`-`F`) - almost
certainly a deliberate choice by whoever designed this scheme, so a
plain BLE scanner (or a human squinting at a hex dump) can read the
model number directly as text. A future model ID whose hex form needs
`A`-`F` would not survive this encoding and would need a different
detection path.

Implemented as `_extract_model_id()` in `config_flow.py`.

There is a second, independent way the app can learn the model ID:
*after connecting*, reading the "advertising manufacturer specific data"
configuration register over GATT (register index 41 in
`BleRegConstants.REG`, itself wired to raw register byte value `71` -
see §7) returns 2 raw bytes that are the model ID as a plain big-endian
`uint16` (no ASCII/hex-nibble trick). This integration's *first*
attempt at model detection used this big-endian-binary interpretation
and applied it to the advertisement instead of a post-connect GATT
read, which is why it initially misidentified every light - see this
project's commit history ("Fix model-ID detection...") for the full
story. It is included here only so the distinction is documented and
doesn't get re-confused: **the advertisement encoding is the
ASCII-hex-nibble scheme above; the post-connect GATT register read (not
used by this integration) is the plain binary one.**

## 7. The register sub-protocol (out of scope)

Separately from everything above, the underlying BLE module (a generic,
white-label UART-to-BLE bridge chip used across many unrelated
products, not something Fluval designed) exposes a small set of
configuration "registers" - advertised name, TX power, RX gain, various
timing/interval parameters, a password, battery level, and the
"advertising manufacturer specific data" mentioned in §6 - readable and
writable over `CHARACTERS[3]` via `BleService.readReg()` /
`BleService.setReg()` in the app. `BleRegConstants.REG` maps a symbolic
index (e.g. `REG_ADV_MFR_SPC = 41`) to the actual byte value sent on the
wire (`REG[41] == 71`) - the indices and wire values are different
numbers, which is an easy detail to trip over if this is ever
revisited.

This integration does not use this sub-protocol at all - it's part of
the generic module vendor's SDK, used by Fluval's own app mainly at
manufacturing/provisioning time (setting the advertised name and
manufacturer data during production) plus the model-confirmation read
described in §6. It's documented here only for completeness and so a
future reader isn't surprised by its existence; it was not fully
reverse engineered (the per-register value encoding differs by register
type - some are single bytes, some are little-endian ints - and wasn't
traced out register-by-register since nothing this integration does
needs it).

## 8. Confidence and open questions

What's been **confirmed against a real light** (not just read out of
decompiled code): the wire obfuscation round-trip, the plaintext frame
format and checksum, on/off, mode switching, per-channel brightness
read and write, the multi-chunk reassembly behavior (§4), and the
ASCII-hex model ID encoding (§6).

What's **decompiled-only / not yet exercised against real hardware**:
everything in SCHEDULING.md and OTA.md, the `CMD_READTIME` response
shape, the exact meaning of the Manual-mode response's "dynamic" byte
(offset 4), and the register sub-protocol (§7).

If you're extending this integration and hit a real device that
contradicts something written here, trust the device and update this
document - that's exactly how the multi-chunk bug in §4 was found and
fixed.
