# UP-AirQuality MQTT Bridge app for Home Assistant

[![Release](https://img.shields.io/github/v/release/n-schilling/ha-app-upaq-mqtt)](https://github.com/n-schilling/ha-app-upaq-mqtt/releases)
[![CI](https://github.com/n-schilling/ha-app-upaq-mqtt/actions/workflows/ci.yaml/badge.svg?branch=main)](https://github.com/n-schilling/ha-app-upaq-mqtt/actions/workflows/ci.yaml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
![Home Assistant app](https://img.shields.io/badge/Home%20Assistant-app-41BDF5?logo=homeassistant&logoColor=white)
![Supports aarch64](https://img.shields.io/badge/aarch64-yes-green.svg)
![Supports amd64](https://img.shields.io/badge/amd64-yes-green.svg)

![UP-AirQuality MQTT Bridge](upaq_mqtt/logo.png)

A Home Assistant app (formerly add-on) that brings UniFi Protect UP-AirQuality sensors into Home Assistant through MQTT discovery: every reading as a sensor, optionally the LED ring and alert thresholds as controls. It follows Protect's update stream instead of polling and sends each reading only when it changed.

## Installation

1. Add the repository to your Home Assistant instance:

   [![Add the repository to My Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fn-schilling%2Fha-app-upaq-mqtt)

   Or add it manually: **Settings → Apps → Install app → ⋮ → Repositories** (before Home Assistant 2026.2: **Settings → Add-ons → Add-on store**), then paste:

   ```text
   https://github.com/n-schilling/ha-app-upaq-mqtt
   ```

2. Open the app from the app store.
3. Install **UP-AirQuality MQTT Bridge**, fill in the options and start it.

See [the documentation](upaq_mqtt/DOCS.md) for the options, entities, example automations and troubleshooting. Changes are listed in the [changelog](upaq_mqtt/CHANGELOG.md).

## Development

The bridge is a Python package in [`upaq_mqtt/rootfs/usr/lib/upaq_bridge`](upaq_mqtt/rootfs/usr/lib/upaq_bridge). Tests:

```bash
pip install -r upaq_mqtt/requirements.txt pytest pytest-aiohttp
pytest
```

## Support

Questions, bugs and ideas: [open an issue](https://github.com/n-schilling/ha-app-upaq-mqtt/issues/new/choose) in this repository. Security problems: see [SECURITY.md](SECURITY.md).

## License

[Apache License 2.0](LICENSE)
