"""Constants for the Oclean integration."""

DOMAIN = "oclean"
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"


def parse_battery_level(payload: bytes | bytearray) -> int:
    """Parse a Bluetooth Battery Level characteristic."""
    if len(payload) != 1 or payload[0] > 100:
        raise ValueError(f"Invalid battery payload: {payload.hex()}")
    return payload[0]


if __name__ == "__main__":
    assert parse_battery_level(b"\x64") == 100
    for invalid in (b"", b"\x65", b"\x01\x02"):
        try:
            parse_battery_level(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Accepted invalid payload: {invalid.hex()}")
