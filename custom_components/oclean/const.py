"""Constants and protocol parsing for the Oclean integration."""

from datetime import UTC, datetime, timedelta, timezone as fixed_timezone, tzinfo
from typing import Any

DOMAIN = "oclean"

BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
MODEL_NUMBER_UUID = "00002a24-0000-1000-8000-00805f9b34fb"
SOFTWARE_REVISION_UUID = "00002a28-0000-1000-8000-00805f9b34fb"
HARDWARE_REVISION_UUID = "00002a27-0000-1000-8000-00805f9b34fb"
READ_NOTIFY_UUID = "5f78df94-798c-46f5-990a-855b673fbb86"
WRITE_UUID = "9d84b9a3-000c-49d8-9183-855b673fbb85"
SEND_BRUSH_UUID = "5f78df94-798c-46f5-990a-855b673fbb89"
RECEIVE_BRUSH_UUID = "5f78df94-798c-46f5-990a-855b673fbb90"

KEY_BATTERY = "battery"
KEY_MODEL = "model"
KEY_SOFTWARE = "software"
KEY_HARDWARE = "hardware"
KEY_LAST_SESSION = "last_session"
KEY_DURATION = "duration"
KEY_SCORE = "score"
KEY_PROGRAM = "program"
SESSION_KEYS = (KEY_LAST_SESSION, KEY_DURATION, KEY_SCORE, KEY_PROGRAM)

SESSION_RECORD_SIZE = 42
MAX_SESSION_RECORDS = 64
MIN_SESSION_YEAR = 2015
MAX_FUTURE_SKEW = timedelta(days=1)
_MAGIC = b"\x03\x07*B#"
_TZ_OFFSETS_MIN = (
    -720,
    -660,
    -600,
    -540,
    -480,
    -420,
    -360,
    -300,
    -240,
    -210,
    -180,
    -120,
    -60,
    0,
    60,
    120,
    180,
    210,
    240,
    270,
    300,
    330,
    345,
    360,
    390,
    420,
    480,
    540,
    570,
    600,
    660,
    720,
    780,
)


def parse_battery_level(payload: bytes | bytearray) -> int:
    """Parse a Bluetooth Battery Level characteristic."""
    if len(payload) != 1 or payload[0] > 100:
        raise ValueError(f"Invalid battery payload: {payload.hex()}")
    return payload[0]


def build_time_command(now: datetime) -> bytes:
    """Build the Type-1 0201 clock-calibration command."""
    offset = now.utcoffset()
    if offset is None or not 2000 <= now.year <= 2255:
        raise ValueError("Oclean time calibration requires an aware supported date")
    offset_min = int(offset.total_seconds() / 60)
    tz_index = min(
        range(len(_TZ_OFFSETS_MIN)),
        key=lambda index: abs(_TZ_OFFSETS_MIN[index] - offset_min),
    ) + 1
    weekday = (now.weekday() + 1) % 7
    return b"\x02\x01" + bytes(
        (
            now.year - 2000,
            now.month,
            now.day,
            now.hour,
            now.minute,
            now.second,
            weekday,
            tz_index,
        )
    )


def merge_update(current: dict[str, Any], incoming: dict[str, Any]) -> bool:
    """Merge data without retaining fields from an older brushing session."""
    timestamp = incoming.get(KEY_LAST_SESSION)
    previous = current.get(KEY_LAST_SESSION)
    if timestamp is not None and previous is not None and timestamp < previous:
        return False
    if timestamp is not None and (previous is None or timestamp > previous):
        for key in SESSION_KEYS:
            current.pop(key, None)
    current.update(incoming)
    return True


def _parse_session(
    record: bytes, timezone: tzinfo, now: datetime
) -> dict[str, Any]:
    """Parse confirmed fields from one Type-1 session record."""
    if len(record) < 9:
        return {}
    session_timezone = timezone
    if len(record) >= SESSION_RECORD_SIZE and 1 <= record[17] <= len(
        _TZ_OFFSETS_MIN
    ):
        session_timezone = fixed_timezone(
            timedelta(minutes=_TZ_OFFSETS_MIN[record[17] - 1])
        )
    session_now = now.astimezone(session_timezone)
    years = (
        (2000 + record[0],)
        if record[0]
        else (session_now.year, session_now.year - 1)
    )
    local = None
    for year in years:
        try:
            candidate = datetime(
                year,
                record[1],
                record[2],
                record[3],
                record[4],
                record[5],
                tzinfo=session_timezone,
            )
        except ValueError:
            continue
        if record[0] or candidate <= session_now + MAX_FUTURE_SKEW:
            local = candidate
            break
    if (
        local is None
        or local.year < MIN_SESSION_YEAR
        or local > session_now + MAX_FUTURE_SKEW
    ):
        return {}

    result = {KEY_LAST_SESSION: local.astimezone(UTC)}
    if record[0]:
        result[KEY_PROGRAM] = record[6]
    duration = int.from_bytes(record[7:9], "big")
    if duration:
        result[KEY_DURATION] = duration
    if len(record) >= SESSION_RECORD_SIZE and 0 < record[33] <= 100:
        result[KEY_SCORE] = record[33]
    return result


class OcleanNotificationParser:
    """Reassemble and parse Oclean Type-1 notifications."""

    def __init__(self, timezone: tzinfo) -> None:
        self._timezone = timezone
        self._buffer = bytearray()
        self._expected = 0

    def feed_status(self, data: bytes) -> dict[str, Any]:
        """Parse one status-channel notification."""
        if data[:2] == b"\x03\x03" and len(data) >= 6 and data[5] <= 100:
            return {KEY_BATTERY: data[5]}
        if data[:2] == b"\x00\x00" and len(data) >= 3 and data[2] <= 100:
            return {KEY_SCORE: data[2]}
        if data.startswith(_MAGIC) and len(data) >= 7:
            return _parse_session(
                data[7:], self._timezone, datetime.now(self._timezone)
            )
        return {}

    def feed_session(self, data: bytes) -> dict[str, Any]:
        """Consume one session-channel notification."""
        if self._expected:
            self._buffer.extend(data)
            return self._finish() if len(self._buffer) >= self._expected else {}

        if data[:2] == b"\x00\x00" and len(data) >= 3 and data[2] <= 100:
            return {KEY_SCORE: data[2]}
        if not data.startswith(_MAGIC) or len(data) < 7:
            return {}

        count = int.from_bytes(data[5:7], "big")
        payload = data[7:]
        if count == 0:
            return _parse_session(
                payload, self._timezone, datetime.now(self._timezone)
            )
        if count > MAX_SESSION_RECORDS:
            return {}

        self._buffer = bytearray(payload)
        self._expected = count * SESSION_RECORD_SIZE
        return self._finish() if len(self._buffer) >= self._expected else {}

    def flush(self) -> dict[str, Any]:
        """Return the newest complete record from a partial transfer."""
        if not self._expected:
            return {}
        if len(self._buffer) < SESSION_RECORD_SIZE:
            data = bytes(self._buffer)
            self._buffer.clear()
            self._expected = 0
            return _parse_session(
                data, self._timezone, datetime.now(self._timezone)
            )
        self._expected = len(self._buffer) // SESSION_RECORD_SIZE * SESSION_RECORD_SIZE
        return self._finish()

    def _finish(self) -> dict[str, Any]:
        data = bytes(self._buffer[: self._expected])
        self._buffer.clear()
        self._expected = 0
        now = datetime.now(self._timezone)
        records = (
            _parse_session(
                data[index : index + SESSION_RECORD_SIZE], self._timezone, now
            )
            for index in range(0, len(data), SESSION_RECORD_SIZE)
        )
        return max(
            (record for record in records if KEY_LAST_SESSION in record),
            key=lambda record: record[KEY_LAST_SESSION],
            default={},
        )


if __name__ == "__main__":
    assert parse_battery_level(b"\x64") == 100
    assert build_time_command(
        datetime(2026, 7, 17, 12, 34, 56, tzinfo=UTC)
    ) == bytes.fromhex("02011a07110c2238050e")
    for invalid in (b"", b"\x65", b"\x01\x02"):
        try:
            parse_battery_level(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Accepted invalid payload: {invalid.hex()}")

    record = bytearray(SESSION_RECORD_SIZE)
    record[:9] = bytes.fromhex("1a07110c22384c0096")
    record[33] = 98
    future = bytearray(record)
    future[0] = 255
    future[33] = 91
    parser = OcleanNotificationParser(UTC)
    assert not parser.feed_session(_MAGIC + b"\x00\x02" + future[:13])
    assert parser.feed_status(bytes.fromhex("0303020e4b640000")) == {
        KEY_BATTERY: 100
    }
    inline = parser.feed_status(
        bytes.fromhex("03072a422300001a021510191fe7001e001e6400")
    )
    assert inline[KEY_LAST_SESSION] == datetime(
        2026, 2, 21, 16, 25, 31, tzinfo=UTC
    )
    score_push = bytes.fromhex("00005f00ffffffffffffff1a0215101a23e7001e")
    merge_update(inline, parser.feed_status(score_push))
    assert inline[KEY_SCORE] == 95
    parsed = parser.feed_session(future[13:] + record)
    assert parsed[KEY_LAST_SESSION] == datetime(2026, 7, 17, 12, 34, 56, tzinfo=UTC)
    assert parsed[KEY_PROGRAM] == 76
    assert parsed[KEY_DURATION] == 150
    assert parsed[KEY_SCORE] == 98

    short_frame = bytes.fromhex("03072a422300011a02181535134c009600960b03")
    short = parser.feed_status(short_frame)
    assert short[KEY_PROGRAM] == 76
    assert short[KEY_DURATION] == 150
    short_parser = OcleanNotificationParser(UTC)
    assert not short_parser.feed_session(short_frame)
    assert short_parser.flush()[KEY_DURATION] == 150

    scoreless = bytearray(record)
    scoreless[33] = 0
    partial_parser = OcleanNotificationParser(UTC)
    assert not partial_parser.feed_session(
        _MAGIC + b"\x00\x02" + scoreless
    )
    pending_score = partial_parser.feed_status(score_push)
    recovered = partial_parser.flush()
    merge_update(recovered, pending_score)
    assert recovered[KEY_SCORE] == 95

    timezone_record = bytes.fromhex(
        "1a030b14021700007800780514"
        "4b0000001f00000000001c14190c0a0a0a000000"
        "5b030301ffffffff1a"
    )
    timezone_parser = OcleanNotificationParser(
        fixed_timezone(timedelta(hours=1))
    )
    timezone_session = timezone_parser.feed_session(
        _MAGIC + b"\x00\x01" + timezone_record
    )
    assert timezone_session[KEY_LAST_SESSION] == datetime(
        2026, 3, 11, 9, 2, 23, tzinfo=UTC
    )

    cached = {KEY_LAST_SESSION: datetime(2025, 1, 1, tzinfo=UTC), KEY_SCORE: 90}
    merge_update(
        cached,
        {KEY_LAST_SESSION: parsed[KEY_LAST_SESSION], KEY_DURATION: 150},
    )
    assert KEY_SCORE not in cached
