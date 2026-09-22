"""Constants for the Fluval Smart BLE integration."""
from __future__ import annotations

DOMAIN = "fluval_smart_ble"

# GATT service/characteristics exposed by the Fluval Smart BLE module.
SERVICE_UUID = "00001000-0000-1000-8000-00805f9b34fb"
CHAR_WRITE_UUID = "00001001-0000-1000-8000-00805f9b34fb"
CHAR_NOTIFY_UUID = "00001002-0000-1000-8000-00805f9b34fb"

# Frame header byte used by every plaintext command/response frame.
FRAME_HEADER = 0x68

# Command byte values (see CommUtil in the FluvalSmart app).
CMD_MODE = 0x02
CMD_SWITCH = 0x03
CMD_CTRL = 0x04
CMD_READ = 0x05
CMD_CUSTOM = 0x06
CMD_CYCLE = 0x07
CMD_CHN_INC = 0x08
CMD_CHN_DEC = 0x09
CMD_DYN = 0x0A
CMD_PREVIEW = 0x0B
CMD_STOP_PREVIEW = 0x0C
CMD_READTIME = 0x0D
CMD_SYNCTIME = 0x0E
CMD_FIND = 0x0F
CMD_PRO = 0x10
CMD_DYNAMIC_PERIOD = 0x11

MODE_MANUAL = 0x00
MODE_AUTO = 0x01
MODE_PRO = 0x02

MODES = {
    MODE_MANUAL: "manual",
    MODE_AUTO: "auto",
    MODE_PRO: "pro",
}
MODES_REVERSE = {v: k for k, v in MODES.items()}

# Not a mode of the light itself: Home Assistant runs the light in Auto
# mode and rewrites its schedule nightly to follow the real sun.
MODE_SUN_SYNC = "sun_sync"
# Sun sync, with the schedule's dynamic effect following a weather entity.
MODE_WEATHER_SYNC = "weather_sync"
MODE_OPTIONS = [*MODES.values(), MODE_SUN_SYNC, MODE_WEATHER_SYNC]

# A channel value of 0xFFFF ("-1" as a Java short) means "leave this
# channel's brightness unchanged" when sent in a CMD_CTRL frame.
CHANNEL_UNCHANGED = 0xFFFF

# Brightness is transmitted in tenths of a percent (0-1000 == 0.0-100.0%).
BRIGHTNESS_SCALE = 10

CONF_MODEL_ID = "model_id"

UPDATE_INTERVAL_SECONDS = 30
