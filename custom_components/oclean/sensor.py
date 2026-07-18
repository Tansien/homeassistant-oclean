"""Sensors for Oclean Bluetooth toothbrushes."""

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta
import logging
from typing import Any
from zoneinfo import ZoneInfo

from bleak import BleakClient
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import (
    BATTERY_LEVEL_UUID,
    DOMAIN,
    HARDWARE_REVISION_UUID,
    KEY_BATTERY,
    KEY_DURATION,
    KEY_HARDWARE,
    KEY_LAST_SESSION,
    KEY_MODEL,
    KEY_PROGRAM,
    KEY_SCORE,
    KEY_SOFTWARE,
    MODEL_NUMBER_UUID,
    OcleanNotificationParser,
    READ_NOTIFY_UUID,
    RECEIVE_BRUSH_UUID,
    SEND_BRUSH_UUID,
    SOFTWARE_REVISION_UUID,
    WRITE_UUID,
    build_time_command,
    is_empty_session_response,
    merge_update,
    parse_battery_level,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=30)
ENRICHMENT_WAIT = 1.5
PARALLEL_UPDATES = 1

SENSORS = (
    SensorEntityDescription(
        key=KEY_BATTERY,
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key=KEY_LAST_SESSION,
        translation_key="last_session",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    SensorEntityDescription(
        key=KEY_DURATION,
        translation_key="duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key=KEY_SCORE,
        translation_key="score",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(key=KEY_PROGRAM, translation_key="program"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Oclean sensors."""
    address = entry.unique_id
    assert address is not None
    coordinator = OcleanCoordinator(hass, address)
    await coordinator.async_config_entry_first_refresh()
    async_add_entities(
        OcleanSensor(coordinator, entry, description) for description in SENSORS
    )


class OcleanCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll one toothbrush with one shared BLE connection."""

    def __init__(self, hass: HomeAssistant, address: str) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL
        )
        self.address = address

    async def _async_update_data(self) -> dict[str, Any]:
        data = dict(self.data or {})
        device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if device is None:
            return data

        try:
            client = await establish_connection(
                BleakClient, device, self.address, max_attempts=3
            )
            try:
                await asyncio.sleep(2)
                metadata_updates: dict[str, str] = {}
                for key, uuid, registry_field in (
                    (KEY_MODEL, MODEL_NUMBER_UUID, "model"),
                    (KEY_SOFTWARE, SOFTWARE_REVISION_UUID, "sw_version"),
                    (KEY_HARDWARE, HARDWARE_REVISION_UUID, "hw_version"),
                ):
                    try:
                        value = await client.read_gatt_char(uuid)
                        decoded = value.decode().strip("\x00").strip()
                    except (BleakError, TimeoutError, UnicodeDecodeError):
                        continue
                    if decoded:
                        data[key] = decoded
                        metadata_updates[registry_field] = decoded

                if metadata_updates:
                    registry = dr.async_get(self.hass)
                    device_entry = registry.async_get_device(
                        identifiers={(DOMAIN, self.address)}
                    )
                    if device_entry is not None:
                        registry.async_update_device(
                            device_entry.id, **metadata_updates
                        )

                with suppress(BleakError, TimeoutError, ValueError):
                    data[KEY_BATTERY] = parse_battery_level(
                        await client.read_gatt_char(BATTERY_LEVEL_UUID)
                    )

                timezone = ZoneInfo(self.hass.config.time_zone)
                parser = OcleanNotificationParser(timezone)
                session_received = asyncio.Event()
                session_response_received = asyncio.Event()
                pending_score: int | None = None
                last_payloads: dict[str, bytes] = {}
                session_transport_ok = False

                def accept(parsed: dict[str, Any]) -> None:
                    nonlocal pending_score
                    if KEY_LAST_SESSION in parsed:
                        session_response_received.set()
                    if KEY_SCORE in parsed and KEY_LAST_SESSION not in parsed:
                        if not session_received.is_set():
                            pending_score = parsed[KEY_SCORE]
                            return
                        merge_update(data, parsed)
                        return
                    if not merge_update(data, parsed):
                        return
                    if KEY_LAST_SESSION in parsed:
                        session_received.set()
                        if pending_score is not None:
                            merge_update(data, {KEY_SCORE: pending_score})
                            pending_score = None

                def status_handler(_sender: Any, raw: bytearray) -> None:
                    nonlocal session_transport_ok
                    payload = bytes(raw)
                    last_payloads[READ_NOTIFY_UUID] = payload
                    _LOGGER.debug(
                        "Oclean %s status notification: %s",
                        self.address,
                        payload.hex(),
                    )
                    parsed = parser.feed_status(payload)
                    if is_empty_session_response(payload):
                        session_response_received.set()
                        session_transport_ok = True
                    elif KEY_LAST_SESSION in parsed or KEY_SCORE in parsed:
                        session_transport_ok = True
                    accept(parsed)

                def session_handler(_sender: Any, raw: bytearray) -> None:
                    nonlocal session_transport_ok
                    payload = bytes(raw)
                    session_transport_ok = True
                    if is_empty_session_response(payload):
                        session_response_received.set()
                    last_payloads[RECEIVE_BRUSH_UUID] = payload
                    _LOGGER.debug(
                        "Oclean %s session notification: %s",
                        self.address,
                        payload.hex(),
                    )
                    accept(parser.feed_session(payload))

                subscribed: list[str] = []
                for uuid, handler in (
                    (READ_NOTIFY_UUID, status_handler),
                    (RECEIVE_BRUSH_UUID, session_handler),
                ):
                    try:
                        await client.start_notify(uuid, handler)
                        subscribed.append(uuid)
                    except (BleakError, TimeoutError) as err:
                        _LOGGER.debug(
                            "Unable to subscribe %s on %s: %s",
                            uuid,
                            self.address,
                            err,
                        )

                with suppress(BleakError, TimeoutError, ValueError):
                    await client.write_gatt_char(
                        WRITE_UUID,
                        build_time_command(datetime.now(timezone)),
                        response=True,
                    )
                for command in (b"\x03\x03", b"\x03\x07"):
                    try:
                        await client.write_gatt_char(
                            SEND_BRUSH_UUID, command, response=True
                        )
                    except (BleakError, TimeoutError) as err:
                        _LOGGER.debug(
                            "Unable to send %s to %s: %s",
                            command.hex(),
                            self.address,
                            err,
                        )
                    await asyncio.sleep(0.1)

                if subscribed:
                    with suppress(TimeoutError):
                        await asyncio.wait_for(
                            session_response_received.wait(), timeout=8
                        )

                channels = (
                    (RECEIVE_BRUSH_UUID, session_handler),
                    (READ_NOTIFY_UUID, status_handler),
                )
                poll_channels = (
                    channels
                    if not session_response_received.is_set()
                    else tuple(
                        channel for channel in channels if channel[0] not in subscribed
                    )
                    if session_received.is_set()
                    else ()
                )
                if poll_channels:
                    for _attempt in range(6):
                        await asyncio.sleep(1)
                        for uuid, handler in poll_channels:
                            try:
                                payload = bytes(await client.read_gatt_char(uuid))
                            except (BleakError, TimeoutError):
                                continue
                            if uuid == RECEIVE_BRUSH_UUID:
                                session_transport_ok = True
                            if (
                                len(payload) > 2
                                and payload != last_payloads.get(uuid)
                            ):
                                handler(None, bytearray(payload))
                        if session_response_received.is_set() and (
                            not session_received.is_set() or KEY_SCORE in data
                        ):
                            break

                accept(parser.flush())
                if session_received.is_set():
                    await asyncio.sleep(ENRICHMENT_WAIT)
                elif session_transport_ok:
                    _LOGGER.debug(
                        "No Oclean session data returned by %s", self.address
                    )
                else:
                    _LOGGER.warning(
                        "No Oclean session response received from %s",
                        self.address,
                    )

                for uuid in subscribed:
                    with suppress(BleakError, TimeoutError):
                        await client.stop_notify(uuid)
            finally:
                with suppress(BleakError, TimeoutError):
                    await client.disconnect()
        except (BleakError, TimeoutError) as err:
            _LOGGER.debug("Unable to poll %s: %s", self.address, err)

        return data


class OcleanSensor(CoordinatorEntity[OcleanCoordinator], SensorEntity):
    """A value from an Oclean toothbrush poll."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OcleanCoordinator,
        entry: ConfigEntry,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"
        device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            connections={(dr.CONNECTION_BLUETOOTH, coordinator.address)},
            manufacturer="Oclean",
            name=entry.title,
        )
        if KEY_MODEL in coordinator.data:
            device_info["model"] = coordinator.data[KEY_MODEL]
        if KEY_SOFTWARE in coordinator.data:
            device_info["sw_version"] = coordinator.data[KEY_SOFTWARE]
        if KEY_HARDWARE in coordinator.data:
            device_info["hw_version"] = coordinator.data[KEY_HARDWARE]
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        """Return whether this value has ever been observed."""
        return (
            super().available
            and self.entity_description.key in self.coordinator.data
        )

    @property
    def native_value(self) -> Any:
        """Return the latest decoded value."""
        return self.coordinator.data.get(self.entity_description.key)
