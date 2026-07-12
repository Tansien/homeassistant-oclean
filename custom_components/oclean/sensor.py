"""Battery sensor for Oclean Bluetooth toothbrushes."""

from datetime import timedelta
import logging

from bleak import BleakClient
from bleak.exc import BleakError

from homeassistant.components import bluetooth
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import BATTERY_LEVEL_UUID, DOMAIN, parse_battery_level

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=30)
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Oclean battery sensor."""
    address = entry.unique_id
    assert address is not None
    async_add_entities([OcleanBatterySensor(hass, entry, address)], True)


class OcleanBatterySensor(SensorEntity):
    """An Oclean battery sensor."""

    _attr_available = False
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "battery"

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, address: str
    ) -> None:
        """Initialize the sensor."""
        self._hass = hass
        self._address = address
        self._attr_unique_id = f"{address}_battery"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            manufacturer="Oclean",
            name=entry.title,
        )

    async def async_update(self) -> None:
        """Read the standard Bluetooth Battery Level characteristic."""
        device = bluetooth.async_ble_device_from_address(
            self._hass, self._address, connectable=True
        )
        if device is None:
            return

        try:
            async with BleakClient(device, timeout=20) as client:
                payload = await client.read_gatt_char(BATTERY_LEVEL_UUID)
            self._attr_native_value = parse_battery_level(payload)
            self._attr_available = True
        except (BleakError, TimeoutError, ValueError) as err:
            _LOGGER.debug("Unable to read %s battery: %s", self._address, err)
