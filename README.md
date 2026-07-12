# Oclean for Home Assistant

Early Home Assistant support for Oclean Bluetooth toothbrushes.

The integration discovers nearby Oclean toothbrushes and exposes their standard
Bluetooth battery level. Communication stays local and works through Home
Assistant Bluetooth adapters and connectable ESPHome Bluetooth proxies.

## Supported devices

Tested with:

- Oclean X Pro Elite (`OCLEANY3P`)
- Oclean X (`OCLEANY3S`)
- Oclean X (`OCLEANY3M`)

Oclean X Ultra discovery works, but its battery connection has not yet been
verified.

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

## Current scope

- Bluetooth discovery
- Battery percentage, refreshed every 30 minutes
- Last successful battery reading retained while a toothbrush is unreachable

Brushing duration, mode, score, pressure, history, and brush-head life require
more protocol research and are not exposed yet.

## License

MIT
