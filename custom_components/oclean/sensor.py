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
from homeassistant.helpers.device_registry import DeviceInfo
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
    merge_update,
    parse_battery_level,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=30)
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
        self.timezone = ZoneInfo(hass.config.time_zone)

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
                for key, uuid in (
                    (KEY_MODEL, MODEL_NUMBER_UUID),
                    (KEY_SOFTWARE, SOFTWARE_REVISION_UUID),
                    (KEY_HARDWARE, HARDWARE_REVISION_UUID),
                ):
                    with suppress(BleakError, TimeoutError, UnicodeDecodeError):
                        value = await client.read_gatt_char(uuid)
                        data[key] = value.decode().strip("\x00").strip()

                with suppress(BleakError, TimeoutError, ValueError):
                    data[KEY_BATTERY] = parse_battery_level(
                        await client.read_gatt_char(BATTERY_LEVEL_UUID)
                    )

                parser = OcleanNotificationParser(self.timezone)
                session_received = asyncio.Event()

                def notification_handler(_sender: Any, raw: bytearray) -> None:
                    payload = bytes(raw)
                    _LOGGER.debug(
                        "Oclean %s notification: %s", self.address, payload.hex()
                    )
                    parsed = parser.feed(payload)
                    merge_update(data, parsed)
                    if KEY_LAST_SESSION in parsed:
                        session_received.set()

                subscribed: list[str] = []
                for uuid in (READ_NOTIFY_UUID, RECEIVE_BRUSH_UUID):
                    try:
                        await client.start_notify(uuid, notification_handler)
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
                        build_time_command(datetime.now(self.timezone)),
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
                        await asyncio.wait_for(session_received.wait(), timeout=8)
                merge_update(data, parser.flush())

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
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            manufacturer="Oclean",
            name=entry.title,
            model=coordinator.data.get(KEY_MODEL),
            sw_version=coordinator.data.get(KEY_SOFTWARE),
            hw_version=coordinator.data.get(KEY_HARDWARE),
        )

    @property
    def native_value(self) -> Any:
        """Return the latest decoded value."""
        return self.coordinator.data.get(self.entity_description.key)
