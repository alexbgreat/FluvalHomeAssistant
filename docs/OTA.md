# Firmware update ("OTA") mechanism - reference only

**This integration does not implement firmware updates, and this
document is not an invitation to add them.** Flashing new firmware over
BLE has a real, asymmetric downside: get it wrong and you can brick a
customer's light with no recovery path, whereas getting it right saves
them a trip to... using the official app, which already does this. This
page exists purely so the mechanism is documented for reference (e.g.
if a future contributor needs to understand *why* a light behaves oddly
after using the official app's update feature, or wants to add a
firmware-version *sensor* without going anywhere near writing flash).
If anyone does decide to pursue actually implementing this, read the
whole page first and see the [cautions](#if-you-still-want-to-implement-this)
at the end.

Source: `com.inledco.fluvalsmart.ota.{OTAConstants,OTAPresenter,Frame}`
and `com.ble.ble.oad.*` in the decompiled app.

## Transport

OTA commands travel over the **same** GATT write/notify characteristics
and the **same** wire-obfuscation wrapper
([PROTOCOL.md §1-2](PROTOCOL.md)) as ordinary light-control commands -
`OTAPresenter` calls the same `BleManager.getInstance().sendBytes(...)`
used everywhere else. What's different is the plaintext framing inside
that wrapper: OTA frames do **not** use the `[0x68, cmd, ..., checksum]`
shape from PROTOCOL.md §3. They have their own, simpler, and internally
inconsistent framing (some request types include a trailing checksum
byte, some don't - see the table below; this was read directly out of
the code, it isn't a transcription error).

## High-level flow

1. **Check for an update**: connect, read the device's model ID and
   firmware version (`readMfr`, then `GET_STATUS`/`GET_VERSION` - see
   below), and separately query a Fluval-operated HTTP endpoint for the
   latest available version for that model ID (see
   [Remote endpoints](#remote-endpoints-do-not-call-these) - this part
   has nothing to do with BLE).
2. **Download firmware**: if a newer version exists, download an Intel
   HEX firmware image from a second Fluval-operated endpoint.
3. **Parse the image**: parse the `.hex`/`.txt` file into a list of
   Intel HEX records (`Frame.Builder.getFramesFromFile()`).
4. **Enter the bootloader**: send `GET_STATUS`; the response tells you
   whether the device is already in its bootloader or needs a manual
   power cycle to get there.
5. **Get bootloader info**: send `GET_VERSION`; the response gives the
   bootloader's own version plus the flashable address range and block
   sizes.
6. **Erase**: send `ERASE_FLASH` covering the app's address range.
7. **Write**: send one `WRITE_FLASH` per parsed HEX record (each
   record's data length capped at 8 bytes - see
   [Firmware image format](#firmware-image-format)), waiting for each
   one's echo before sending the next.
8. **Verify** (optional/best-effort in the app): `CALC_CHECKSUM`.
9. **Reset**: send `RESET_DEVICE` to reboot into the newly-written
   application firmware.

Every step above waits for a matching response before proceeding, with
a 1-second countdown timer (`OTAPresenter.mCountDownTimer`) that aborts
the whole process and shows an error dialog if a step doesn't respond
in time.

## Command reference

Constants from `OTAConstants`. Values in the "Request bytes" column are
exactly what `OTAPresenter` sends (as always, this is the *plaintext*
that then gets wire-wrapped per PROTOCOL.md §2 before transmission).

| Value | Name | Request bytes | Notes |
|---|---|---|---|
| `0x00` | `OTA_CMD_GET_VERSION` | `[0, 0, 0, 0]` | No trailing checksum. Response (12 bytes, `[0, 8, ..., ...]`) carries bootloader major/minor version (bytes 4-5) and the flashable app address range + block sizes (bytes 6-11): app start address (bytes 6-7, little-endian), app end address (bytes 8-9, little-endian), erase block size (byte 10), write block size (byte 11). |
| `0x01` | `OTA_CMD_READ_FLASH` | *(never sent by the app)* | Defined but unused - `OTAPresenter` has no code path that sends this. |
| `0x02` | `OTA_CMD_WRITE_FLASH` | `[2, dataLen, addrLo, addrHi, ...data]` | `dataLen` ≤ 8 (see below). No trailing checksum. Echo response's address (bytes 2-3, little-endian) is compared against the frame just sent to confirm it landed before advancing to the next record; status byte at offset 4 is `1`=ok, `-2` (`0xFE`)=out of range. |
| `0x03` | `OTA_CMD_ERASE_FLASH` | `[3, blockCount, startAddrLo, startAddrHi]` | No trailing checksum. `blockCount` computed client-side from the address range and erase block size reported by `GET_VERSION`. Status byte at response offset 4, same 1/-2 meaning as above. |
| `0x09` | `OTA_CMD_CALC_CHECKSUM` | *(builder not present in decompiled `OTAPresenter`; only the response is handled)* | Response sub-type `4` = success (carries a checksum value the app reads but doesn't compare against anything visible); sub-type `1` with status `-2` = failure. |
| `0x0A` | `OTA_CMD_RESET_DEVICE` | `[10, 0, 0, 0]` | No trailing checksum. Success = status byte `1` at response offset 4; the app then disconnects and shows a success dialog. |
| `0x68` (`104`) | `OTA_CMD_GET_STATUS` | `[104, 0, 0, 104]` | **Does** include a trailing XOR checksum (`104^0^0^0 = 104`) - the one exception to "no checksum" among these. Two distinct response shapes: sub-type `1` + status `1` = now in bootloader, proceed to `GET_VERSION`; sub-type `0` = still running application firmware (needs the user to power-cycle the light into the bootloader manually - the app shows a "please repower the device" dialog). Response status codes generally: `1` = `OTA_RESPONSE_SUCCESS`, `-2` (`0xFE`) = `OTA_REPONSE_OUTOF_RANGE`, `-1` (`0xFF`) = `OTA_REPSONSE_INVALID_COMMAND` (a generic catch-all the app checks for any command it doesn't otherwise recognize the response shape for). |

Response frames in general (`OTAPresenter.decodeReceiveData`) are read
as `[requestType, responseSubType, ...4+ more bytes...]`, minimum 5
bytes - `requestType` at offset 0 is compared against whichever command
is currently in flight (`mCurrentCommand`) to ignore anything
unexpected.

## Firmware image format

Firmware files are standard **Intel HEX** (`.hex`/`.txt`), one record
per line: `:LLAAAATT[DD...]CC` (length byte, 16-bit address, record
type, data bytes, checksum - all hex-ASCII). `Frame.Builder` parses this
with a full checksum validation (sum of every byte including the
length/address/type/checksum itself must be `0 mod 256`) and rejects
malformed records outright (returns `null`, which the app surfaces as
"firmware file damaged").

Record types seen handled: `0` = data, `1` = end-of-file (triggers
`resetDevice()` directly, skipping erase/write, when it's an
all-zero-length/zero-address EOF record - i.e. the *only* actual EOF
record the app expects to act on is the very first frame in a
single-frame list, which only happens for a firmware image that is
itself just that one EOF record; in the normal multi-record case the
EOF record type is really only meaningful as a loop terminator, not
something individually transmitted). Types `2` and `4` are accepted as
*valid* by the parser's range check but have no handling in
`upgradeFirmware()` beyond that - they're parsed and then effectively
ignored.

**8-byte record splitting**: `OTA_CMD_WRITE_FLASH` caps data length at
8 bytes per command, but Intel HEX records commonly carry up to 16.
`Frame.Builder.getFramesFromFile()` handles this by splitting any
parsed record longer than 8 bytes into exactly two `Frame` objects (first
8 bytes at the record's address, remaining bytes at `address + 4` -
note: `+4`, not `+8`, because `Frame.address` is stored as a 16-bit
*word* address rather than a byte address, per the `>> 1` in the
address calculation - so `+4` words `== +8` bytes). This means the
in-memory frame list can be longer than the number of lines in the
`.hex` file.

## Remote endpoints (do not call these)

The app checks for and downloads firmware from Fluval-operated HTTP
(not HTTPS) endpoints:

- Version check: `http://47.251.4.156:9000/OTAInfoModels/GetOTAInfo?deviceid=<model_id>`
- Firmware download: `http://47.251.4.156:9000<file_link_from_above>`

These are recorded here purely as a factual part of understanding what
the official app does - **this integration will never call them, and
nobody should probe a third party's infrastructure.** They're someone
else's production server, reachable over plain HTTP with no auth
visible in the decompiled code, and poking at it is not something this
project has any business doing.

## If you still want to implement this

Don't, without a spare/disposable unit to practice on first, and
without having actually watched the official app do a real update
while capturing every frame (the same debug-logging technique used
elsewhere in this project - see
[PROTOCOL.md §8](PROTOCOL.md#8-confidence-and-open-questions)) so the
table above is *confirmed*, not just transcribed from decompiled code
that was never exercised against hardware. Specifically:

- The "no checksum" commands above are exactly that in the decompiled
  code - if that's wrong (e.g. a firmware revision that started
  requiring one), sending an under-length or malformed frame to a
  bootloader mid-erase is a bricking scenario, not a "the light didn't
  respond, try again" scenario.
- `ERASE_FLASH`'s address range and block size come from a **live**
  `GET_VERSION` response, not from anything hardcoded per model - never
  guess or cache these across sessions/firmware revisions.
- There is no verification step between "erase" and "the point where
  reset is the only way to know if it worked" other than the optional,
  seemingly-unused `CALC_CHECKSUM` step. Don't skip it if you implement
  this, even though the reference app's own flow doesn't clearly gate
  on its result.
- Never implement `WRITE_FLASH` without first successfully completing
  `GET_STATUS` → `GET_VERSION` → `ERASE_FLASH` on that exact connection,
  in that exact order, matching `OTAPresenter`'s sequencing.
