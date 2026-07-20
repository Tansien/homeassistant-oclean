# Oclean for Home Assistant

Early Home Assistant support for Oclean Bluetooth toothbrushes.

The integration discovers nearby Oclean toothbrushes and reads battery and
brushing-session data locally through Home Assistant Bluetooth adapters and
connectable ESPHome Bluetooth proxies.

## Supported devices

Tested with:

- Oclean X Pro Elite (`OCLEANY3P`)
- Oclean X (`OCLEANY3S`)
- Oclean X (`OCLEANY3M`)
- Oclean X Ultra (`OCLEANV1a`; battery, timestamp, program, and duration)

## Installation with HACS

1. Open HACS in Home Assistant.
2. Open the menu and select **Custom repositories**.
3. Add `https://github.com/Tansien/homeassistant-oclean` as an **Integration**.
4. Install **Oclean** and restart Home Assistant.
5. Open **Settings > Devices & services**. Confirm a discovered toothbrush, or
   select **Add integration** and choose **Oclean**.

The toothbrush must be visible to a connectable Bluetooth adapter or ESPHome
Bluetooth proxy when it is added and when the battery is read.

## Manual installation

Copy `custom_components/oclean` into the `custom_components` directory in your
Home Assistant configuration, then restart Home Assistant.

## Sensors

- Battery percentage
- Last session timestamp
- Last session duration
- Last session score, when retained by the toothbrush
- Last session program ID

Values refresh every 30 minutes. The last successful values are retained while
the toothbrush is asleep or unreachable.

Pressure, tooth-zone mappings, full history, and brush-head life remain excluded
until they are verified against our device firmware.

## Protocol research

The Type-1 session protocol implementation is based on the APK analysis and
real-device captures published by the MIT-licensed
[ha-oclean-integration](https://github.com/deniskie/ha-oclean-integration)
project.

## License

MIT
