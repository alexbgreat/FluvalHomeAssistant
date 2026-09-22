# Notes for Claude

## Workflow preferences

- When asked to "merge and push", merge the finished feature branch into
  `main` locally (a `--no-ff` merge commit, no rebasing or force-pushing)
  and push `main` to origin. No pull request is needed for this; the owner
  has authorized merging into `main` directly.
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
- Support both older (2024.x) and current Home Assistant cores; there's no
  test suite in the repo yet, so validate against real HA cores
  (`pytest-homeassistant-custom-component`) in a scratch venv.
