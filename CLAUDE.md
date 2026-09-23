# Notes for Claude

## Workflow preferences

- When asked to "merge and push", merge the finished feature branch into
  `main` locally (a `--no-ff` merge commit, no rebasing or force-pushing)
  and push `main` to origin. No pull request is needed for this; the owner
  has authorized merging into `main` directly.
- The owner tests from `main`, so once a fix is committed and validated,
  merge it into `main` and push without waiting to be asked.
- Commit memory/notes like this file to the repository: sessions run in
  ephemeral containers, so anything not pushed is lost.

## Project

- Home Assistant custom integration for Fluval Smart aquarium lights over
  BLE: `custom_components/fluval_smart_ble/`. Protocol notes in `docs/`.
- The "Aquarium Light" sidebar panel is a dependency-free web component
  (`frontend/fluval-schedule-panel.js`) talking to websocket commands in
  `api.py`; registered by `panel.py`.
- Sun sync (`sun_sync.py`) is a Home Assistant-side mode that rewrites the
  light's Auto schedule nightly from the real sunrise/sunset.
- Weather sync (`weather_sync.py`) rides on sun sync: it swaps only the Auto
  schedule's single dynamic effect when the weather entity's condition
  changes and at the day/night boundaries (sunrise-fade start, sunset-fade
  end). It must not re-run sun sync's compute for that: after noon sun sync
  targets tomorrow's sun times, which shifts the boundaries.
  Its effects cover the whole day between them, so leaving weather sync
  must replace the effect (`is_weather_effect()` recognises one by its
  window matching the schedule's day/night period), never keep it.
- An update only takes effect after a full Home Assistant restart; platforms
  like diagnostics are imported on first use, so they can be newer than the
  running coordinator - read new coordinator attributes with getattr there.
  When a fix "doesn't work", first check the owner restarted.
- Support both older (2024.x) and current Home Assistant cores; there's no
  test suite in the repo yet, so validate against real HA cores
  (`pytest-homeassistant-custom-component`) in a scratch venv.
  Gotchas: set `asyncio_mode = auto`; pytest-freezer's `freezer` freezes the
  loop clock so any `asyncio.sleep` hangs - use
  `freezegun.freeze_time(..., tick=True)`, started after `hass_ws_client`
  connects (a frozen clock invalidates its token); patch
  `coordinator.SCHEDULE_MODE_DELAY` to 0 and mock the coordinator's
  `_async_ensure_connected`/`_async_write` instead of setting up bluetooth.
