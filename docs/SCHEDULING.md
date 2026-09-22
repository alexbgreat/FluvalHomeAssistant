# Scheduling RPCs (Auto / Pro modes)

`CMD_CYCLE` (Auto) and `CMD_PRO` (Pro) are implemented: the
integration's **Aquarium Light** sidebar panel (`panel.py`, `api.py`,
`frontend/fluval-schedule-panel.js`) edits both schedules and programs
them onto the light (`protocol.py:
frame_set_auto()` / `frame_set_pro()`), and the Auto/Pro-mode `CMD_READ`
responses are decoded on a best-effort basis to prefill that editor
(`parse_auto_schedule()` / `parse_pro_schedule()`). The implementation
always sends Auto's turn-off block (disabled via its enable byte when
unused) so the variant is unambiguous, doesn't edit the dynamic-effect
trailer, and re-sends one read back from the light unchanged. The other
commands on this page remain reference only.

Everything here comes from `CommUtil.java` (the builders:
`setLedAuto`, `setLedPro`, `setLedDynamicPeriod`, `sendKey`,
`increaseBright`/`decreaseBright`, `setLedCustom`, `preview`/
`stopPreview`) and the bean classes it serializes
(`LightAuto`, `LightPro`, `TimerBrightPoint`, `RampTime`), all under
`com.inledco.fluvalsmart.{util,bean}` in the decompiled app. All byte
layouts below are transcribed directly from that code; none of it has
been exercised against a real light, so treat lengths and offsets as
"this is what the app sends/expects", not "this is confirmed correct on
the wire" the way PROTOCOL.md's §1-4 are.

All frames below use the standard `[0x68, cmd, args..., checksum]`
wrapper from [PROTOCOL.md §3](PROTOCOL.md#3-plaintext-frame-format), sent
through the same wire obfuscation and chunking as everything else. Only
the `args` portion is described here. `N` = the light's channel count
(4 or 5 - see `models.py`).

## The three modes

`CMD_MODE` (`0x02`) switches between:

| Value | Mode | What runs the light |
|---|---|---|
| `0` | Manual | Whatever `CMD_CTRL` last set, directly |
| `1` | Auto | A single sunrise/sunset ramp, described by `CMD_CYCLE` |
| `2` | Pro | A multi-point (4-10 point) daily timeline, described by `CMD_PRO` |

Both Auto and Pro optionally layer a scheduled "dynamic effect" (storm/
cloud/moonlight simulation) on top, configured either inline (as a
trailing block on `CMD_CYCLE`/`CMD_PRO`) or standalone via
`CMD_DYNAMIC_PERIOD`.

## `CMD_CYCLE` (`0x07`) - Auto mode schedule

Builder: `CommUtil.setLedAuto(address, LightAuto)`. A `LightAuto` is
"day brightness" + "night brightness" (each one byte per channel,
**0-100, a plain percentage - not the 0-1000 tenths-of-a-percent scale
`CMD_CTRL` uses**), plus a sunrise ramp and a sunset ramp (each a start
time and end time - the light fades between day and night brightness
over that window), plus two independent optional blocks that may or may
not be present.

Base layout (always present):

| Bytes | Field |
|---|---|
| 0-3 | Sunrise ramp: start hour, start minute, end hour, end minute |
| `4 .. 4+N-1` | Day brightness, one byte per channel, 0-100 |
| `4+N .. 4+N+3` | Sunset ramp: start hour, start minute, end hour, end minute |
| `8+N .. 8+N+N-1` | Night brightness, one byte per channel, 0-100 |

(Offsets above are relative to the start of `args`, i.e. add 2 to get
the offset within the full frame including `[0x68, cmd]`.)

After that base block (`8 + 2N` bytes so far), there are four possible
total lengths depending on which of the two optional blocks are
present - this is exactly mirrored by `CommUtil.decodeLight()`'s four
length checks on the read side (`channelCount*2 + 12/15/18/21`, i.e.
`base + 4/7/10/13` using the numbering above):

- **Neither** (`+4` bytes, i.e. `channelCount*2+12` total incl.
  header/cmd/checksum): nothing else.
- **Turn-off only** (`+7`): `turnoffEnable` (byte, 0/1),
  `turnoffHour`, `turnoffMinute`. A once-a-day fixed shutoff time,
  independent of the sunrise/sunset ramps.
- **Dynamic-effect only** (`+10`): `week` (bitmask byte - see below),
  `dynamicPeriod` (RampTime, 4 bytes: start hour/min, end hour/min),
  `dynamicMode` (byte - effect ID, see `CMD_DYN` below).
- **Both** (`+13`): `turnoffEnable`, `turnoffHour`, `turnoffMinute`,
  then `week`, `dynamicPeriod` (4 bytes), `dynamicMode` - i.e. the
  turn-off block followed by the dynamic-effect block, concatenated.

`week` bitmask (also used identically by `CMD_PRO`'s dynamic block):

| Bit | 0x01 | 0x02 | 0x04 | 0x08 | 0x10 | 0x20 | 0x40 | 0x80 |
|---|---|---|---|---|---|---|---|---|
| Meaning | Sunday | Monday | Tuesday | Wednesday | Thursday | Friday | Saturday | Dynamic effect enabled |

(Bit 7, `0x80`, is a separate "is the dynamic effect on at all" master
switch, distinct from which days it runs.)

**Whether turn-off and dynamic-effect are present is inferred purely
from the total frame length** - there's no separate flag byte
announcing which variant follows. A future implementation needs to pick
one variant deliberately (probably always sending the full 13-byte
variant, with the turn-off disabled and/or dynamic effect disabled via
their own enable bits, rather than trying to omit bytes) to avoid
ambiguity.

## `CMD_PRO` (`0x10`) - Pro mode schedule

Builder: `CommUtil.setLedPro()` (two overloads - see note below).
A Pro schedule is a sorted list of 4-10 "timer bright points", each a
time of day plus a per-channel brightness (again 0-100 plain
percentage, one byte per channel), interpolated between consecutive
points across the day. Layout:

| Bytes | Field |
|---|---|
| 0 | Point count, `4`-`10` |
| `1 .. 1+(N+2)-1` | Point 0: hour, minute, then N brightness bytes |
| `1+(N+2) .. 1+2(N+2)-1` | Point 1: same shape |
| ... | one `(N+2)`-byte block per point, in ascending time-of-day order |

After all the points (`1 + pointCount*(N+2)` bytes so far), an optional
6-byte dynamic-effect trailer may follow, in the **same shape** as
Auto's dynamic block: `week` (bitmask, same table as above),
`dynamicPeriod` (4 bytes), `dynamicMode` (1 byte). Presence is again
inferred from total length (`decodeLight()` accepts either
`pointCount*(N+2)+5` or that `+6` more, matching with/without the
trailer, relative to the frame's own header+cmd+checksum overhead).

Points must be sent in ascending time order; the app enforces this with
`Arrays.sort()` before serializing (`PointComparator`, sorting by
`hour*60+minute`) - a future implementation should do the same rather
than relying on the light to sort them.

**Two Java-side builder overloads, same wire format**: `CommUtil` has
both `setLedPro(String, LightPro)` and
`setLedPro(String, List<TimerBrightPoint>)`. They are not two different
protocols - the second is just a convenience wrapper that always omits
the dynamic-effect trailer (equivalent to the first with
`hasDynamic=false`). Don't read them as two wire formats.

## `CMD_DYNAMIC_PERIOD` (`0x11`) - update just the dynamic-effect window

Builder: `CommUtil.setLedDynamicPeriod(address, mode, RampTime, week)`.
A lighter-weight way to update *only* the dynamic-effect schedule
without resending the whole Auto/Pro schedule it's attached to:

| Bytes | Field |
|---|---|
| 0 | `week`/mode-select byte (passed as the first argument - the app calls this parameter "mode" here, distinct from the trailing `dynamicMode` byte; likely the same bitmask as `week` above, but this wasn't cross-checked against a real device) |
| 1-4 | RampTime: start hour, start minute, end hour, end minute |
| 5 | `dynamicMode` byte (effect ID, see `CMD_DYN` below) |

Fixed 6-byte args, no variants.

## `CMD_DYN` (`0x0A`) - trigger/select a dynamic effect

Builder: `CommUtil.sendKey(address, mode)` - 1 byte, the effect ID.
**Its exact runtime behavior (does it play the effect immediately as a
live preview, or does it just set which effect the dynamic-schedule
windows above will play?) was not confirmed** - inferred only from the
name `sendKey` and from the icon/id mapping in
`DeviceUtil.getDynamicRes()`:

| ID | Effect |
|---|---|
| 1-3 | Thunder 1-3 |
| 4 | "All color" |
| 5-8 | Cloud 1-4 |
| 9-11 | Moon 1-3 |

## `CMD_PREVIEW` / `CMD_STOP_PREVIEW` (`0x0B` / `0x0C`)

`CMD_PREVIEW` has the *identical* args layout to `CMD_CTRL`
(`2xN` bytes, big-endian uint16 tenths-of-a-percent per channel,
`0xFFFF` = unchanged) - see `CommUtil.preview()`, which is a
byte-for-byte copy of `setLed()` except for the command byte.
`CMD_STOP_PREVIEW` takes no args. Inferred purpose, from where the app
calls these (only reachable while editing a Pro-mode point in the UI):
let the app show live output on the physical light while a user drags a
brightness slider for a *not-yet-saved* schedule point, without
disturbing whatever the light's actual committed program is; stop
reverts to that committed program. Not confirmed against real hardware.

## `CMD_CUSTOM` (`0x06`) - save current output to an on-device preset slot

Builder: `CommUtil.setLedCustom(address, slot)` - 1 byte, `slot`
`0`-`3`. Inferred purpose, from the app's UI flow
(`LightManualFragment`'s long-press handler on one of 4 preset
indicators): long-pressing a preset slot in the Manual tab captures
whatever the light is currently outputting into that slot, both in the
app's own local storage (so short-pressing it later replays those exact
`CMD_CTRL` values from the phone) *and* by sending this command to the
light. Whether the light does anything with slot storage beyond
acknowledging it - e.g. whether a physical button on the light can
recall these slots independently of the app - is unknown.

## `CMD_CHN_INC` / `CMD_CHN_DEC` (`0x08` / `0x09`)

Builders: `CommUtil.increaseBright(address, channels, amount)` /
`decreaseBright(...)` - 2 bytes: a channel bitmask, then an amount.
Bitmask values (`CommUtil.CHNL_*`):

| Constant | Value | Channel |
|---|---|---|
| `CHNL_RED` | `0x01` | Red (or the RGBW-analogous first channel) |
| `CHNL_GREEN` | `0x02` | Green |
| `CHNL_BLUE` | `0x04` | Blue |
| `CHNL_WHITE` | `0x08` | White |
| `CHNL_ALL` | `0x0F` | All four |

These names are RGBW-specific (they're only meaningful for the
4-channel models), so this command's applicability to 5-channel models
(Marine/Fresh/etc., whose channels aren't literally R/G/B/W) is
unclear. **The unit of "amount" was not determined** - it could be
whole percent (matching the Auto/Pro 0-100 scale) or tenths of a percent
(matching `CMD_CTRL`'s 0-1000 scale); nothing in the decompiled code
converts it, so it's presumably passed straight through as whatever the
firmware expects, un-scaled by the app.

## `CMD_READTIME` (`0x0D`) - read the light's on-board clock

Builder: `CommUtil.readDeviceTime(address)` - no args. **The response
format was never captured or decoded** in the parts of the app that
were decompiled (unlike `CMD_READ`, nothing in `CommUtil.decodeLight()`
or elsewhere handles a `CMD_READTIME` reply). If this is ever
implemented, the response almost certainly mirrors `CMD_SYNCTIME`'s
request layout (year-2000, month, day, weekday, hour, minute, second -
see [PROTOCOL.md](PROTOCOL.md)), but that's a guess based on symmetry,
not something read out of the app.

## Suggested entity shapes, if this is ever implemented

Not a commitment, just notes for whoever picks this up:

- **Auto mode**: a `time` entity pair for the sunrise window (start/
  end) and another pair for sunset, plus `number` entities for day and
  night brightness *per channel* (0-100%, distinct from the existing
  manual-mode `number` entities which use the 0-100.0% tenths scale -
  these ones are whole percent). A `switch` + `time` pair for the
  turn-off block. Reading back which variant (turn-off/dynamic
  presence) is in use requires decoding `CMD_READ`'s Auto-mode response
  per the length table in [PROTOCOL.md §5](PROTOCOL.md#5-command-reference).
- **Pro mode**: a fixed set of `time` + per-channel `number` entities is
  awkward for a variable 4-10 point list. This probably wants a
  dedicated config flow / options-flow style editor, or a service call
  taking a list of points as YAML/JSON, rather than one entity per
  point.
- **Dynamic effect**: a `select` for the effect ID (table under
  `CMD_DYN` above) plus a `time` pair and day-of-week `select`
  (multiple) for its window - whether via the inline trailer on Auto/
  Pro or the standalone `CMD_DYNAMIC_PERIOD`, whichever proves simpler
  once tested against real hardware.

Given how much of this page is inferred rather than confirmed, the
first real step for any of this is capturing real `CMD_READ` responses
while the light is in Auto and Pro mode (the same debug-logging
approach used to fix the multi-chunk bug - see
[PROTOCOL.md §4](PROTOCOL.md#4-multi-chunk-reassembly-the-important-gotcha))
and checking they match the length/offset tables above before writing
any code against them.
