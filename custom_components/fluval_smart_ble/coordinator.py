"""BLE connection and state management for a Fluval Smart light."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

import homeassistant.util.dt as dt_util
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CHAR_NOTIFY_UUID,
    CHAR_WRITE_UUID,
    MODE_MANUAL,
    UPDATE_INTERVAL_SECONDS,
)
from .models import FluvalModel
from .protocol import (
    FrameReassembler,
    encode_message,
    frame_find,
    frame_read,
    frame_set_channels,
    frame_set_mode,
    frame_sync_time,
    frame_turn_off,
    frame_turn_on,
    parse_read_response,
)

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 15
CONNECT_SETTLE_DELAY = 0.7
READ_RESPONSE_TIMEOUT = 5
MAX_WRITE_CHUNK = 17
CHUNK_DELAY = 0.008


@dataclass
class FluvalState:
    """Last known state of a Fluval Smart light."""

    is_on: bool | None = None
    mode: int | None = None
    channel_values: list[float] = field(default_factory=list)


class FluvalCoordinator(DataUpdateCoordinator[FluvalState]):
    """Maintains a BLE connection to a Fluval Smart light and its state."""

    def __init__(self, hass: HomeAssistant, address: str, model: FluvalModel) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"Fluval {address}",
            update_interval=None,
        )
        self.address = address
        self.model = model
        self.data = FluvalState(channel_values=[0.0] * len(model.channels))

        self._client: BleakClientWithServiceCache | None = None
        self._connect_lock = asyncio.Lock()
        self._write_char: BleakGATTCharacteristic | None = None
        self._write_with_response = True
        self._reassembler = FrameReassembler()
        self._read_waiters: list[asyncio.Future[bytes]] = []
        self._unloading = False

    async def async_setup(self) -> None:
        """Perform the first connection and start periodic polling."""
        await self._async_ensure_connected()
        await self.async_refresh()
        self.hass.async_create_background_task(
            self._async_poll_loop(), name=f"fluval_smart_ble-poll-{self.address}"
        )

    async def _async_poll_loop(self) -> None:
        while not self._unloading:
            try:
                await self.async_refresh()
            except Exception:  # noqa: BLE001 - keep the poll loop alive
                _LOGGER.debug("Periodic refresh failed for %s", self.address, exc_info=True)
            await asyncio.sleep(UPDATE_INTERVAL_SECONDS)

    async def async_unload(self) -> None:
        """Tear down the BLE connection."""
        self._unloading = True
        client = self._client
        self._client = None
        if client is not None and client.is_connected:
            await client.disconnect()

    async def _async_update_data(self) -> FluvalState:
        await self._async_ensure_connected()
        try:
            frame = await self._async_send_and_wait(frame_read())
        except (BleakError, TimeoutError, EOFError) as err:
            raise UpdateFailed(
                f"Could not read Fluval light {self.address} state: "
                f"{type(err).__name__}: {err}"
            ) from err

        _LOGGER.debug("Fluval light %s read response: %s", self.address, frame.hex())
        parsed = parse_read_response(frame, len(self.model.channels))
        if parsed is None:
            raise UpdateFailed(
                f"Unexpected response while reading Fluval light {self.address} "
                f"state (frame={frame.hex()}, expected {len(self.model.channels)} channels)"
            )

        state = self.data
        state.mode = parsed.mode
        if parsed.mode == MODE_MANUAL:
            state.is_on = parsed.is_on
            if parsed.channel_values is not None:
                state.channel_values = parsed.channel_values
        return state

    def _disconnected_callback(self, client: BleakClientWithServiceCache) -> None:
        _LOGGER.debug("Fluval light %s disconnected", self.address)
        if self._client is client:
            self._client = None
        for waiter in self._read_waiters:
            if not waiter.done():
                waiter.set_exception(BleakError("disconnected"))
        self._read_waiters.clear()

    async def _async_ensure_connected(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        async with self._connect_lock:
            if self._client is not None and self._client.is_connected:
                return
            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if ble_device is None:
                raise BleakError(f"Fluval light {self.address} not currently visible")

            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                self.address,
                disconnected_callback=self._disconnected_callback,
                use_services_cache=True,
                ble_device_callback=lambda: bluetooth.async_ble_device_from_address(
                    self.hass, self.address, connectable=True
                ),
                timeout=CONNECT_TIMEOUT,
            )
            await client.start_notify(CHAR_NOTIFY_UUID, self._notification_handler)

            write_char = client.services.get_characteristic(CHAR_WRITE_UUID)
            self._write_with_response = not (
                write_char is not None and "write-without-response" in write_char.properties
            )
            self._write_char = write_char
            self._client = client

            # The official app waits 300ms after service discovery before
            # enabling notifications, then a further 400ms before treating
            # the connection as ready to receive its first command. Cheap
            # BLE modules like this one's firmware can silently drop writes
            # sent before its own post-connection init settles, so mirror
            # that same ~700ms grace period here.
            _LOGGER.debug("Fluval light %s connected, waiting for module to settle", self.address)
            await asyncio.sleep(CONNECT_SETTLE_DELAY)

            try:
                await self._async_write(frame_sync_time(dt_util.now()))
            except BleakError:
                _LOGGER.debug(
                    "Fluval light %s: syncing time on connect failed", self.address, exc_info=True
                )

    async def async_sync_time(self) -> None:
        """Push the current local time to the light's on-board clock."""
        await self._async_ensure_connected()
        await self._async_write(frame_sync_time(dt_util.now()))

    def _notification_handler(self, _characteristic: BleakGATTCharacteristic, data: bytearray) -> None:
        _LOGGER.debug("Fluval light %s notification: %s", self.address, bytes(data).hex())
        frame = self._reassembler.feed(bytes(data))
        if frame is None:
            return
        _LOGGER.debug("Fluval light %s decoded frame: %s", self.address, frame.hex())
        for waiter in list(self._read_waiters):
            if not waiter.done():
                waiter.set_result(frame)
        self._read_waiters.clear()

        parsed = parse_read_response(frame, len(self.model.channels))
        if parsed is None:
            return
        state = self.data
        state.mode = parsed.mode
        if parsed.mode == MODE_MANUAL:
            state.is_on = parsed.is_on
            if parsed.channel_values is not None:
                state.channel_values = parsed.channel_values
        self.async_set_updated_data(state)

    async def _async_write(self, frame: bytes) -> None:
        client = self._client
        if client is None or not client.is_connected:
            raise BleakError("not connected")
        wire = encode_message(frame)
        _LOGGER.debug(
            "Fluval light %s writing frame=%s wire=%s (response=%s)",
            self.address,
            frame.hex(),
            wire.hex(),
            self._write_with_response,
        )
        for offset in range(0, len(wire), MAX_WRITE_CHUNK):
            chunk = wire[offset : offset + MAX_WRITE_CHUNK]
            await client.write_gatt_char(
                CHAR_WRITE_UUID, chunk, response=self._write_with_response
            )
            if offset + MAX_WRITE_CHUNK < len(wire):
                await asyncio.sleep(CHUNK_DELAY)

    async def _async_send_and_wait(self, frame: bytes, timeout: float = READ_RESPONSE_TIMEOUT) -> bytes:
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[bytes] = loop.create_future()
        self._read_waiters.append(waiter)
        try:
            await self._async_write(frame)
            return await asyncio.wait_for(waiter, timeout)
        finally:
            if waiter in self._read_waiters:
                self._read_waiters.remove(waiter)

    async def async_turn_on(self) -> None:
        await self._async_ensure_connected()
        await self._async_write(frame_turn_on())
        self.data.is_on = True
        self.async_set_updated_data(self.data)

    async def async_turn_off(self) -> None:
        await self._async_ensure_connected()
        await self._async_write(frame_turn_off())
        self.data.is_on = False
        self.async_set_updated_data(self.data)

    async def async_set_mode(self, mode: int) -> None:
        await self._async_ensure_connected()
        await self._async_write(frame_set_mode(mode))
        self.data.mode = mode
        self.async_set_updated_data(self.data)

    async def async_set_channel(self, index: int, value: float) -> None:
        """Set a single channel's brightness, leaving the others unchanged."""
        await self._async_ensure_connected()
        values: list[float | None] = [None] * len(self.model.channels)
        values[index] = value
        await self._async_write(frame_set_channels(values))
        if 0 <= index < len(self.data.channel_values):
            self.data.channel_values[index] = value
        self.async_set_updated_data(self.data)

    async def async_set_channels(self, values: list[float | None]) -> None:
        """Set some or all channels' brightness in a single command."""
        await self._async_ensure_connected()
        await self._async_write(frame_set_channels(values))
        for index, value in enumerate(values):
            if value is not None and index < len(self.data.channel_values):
                self.data.channel_values[index] = value
        self.async_set_updated_data(self.data)

    async def async_find(self) -> None:
        """Ask the light to blink so it can be located."""
        await self._async_ensure_connected()
        await self._async_write(frame_find())
