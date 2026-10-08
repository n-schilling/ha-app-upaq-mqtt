# Changelog

All notable changes to this app are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the app uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.4.0] - 2026-10-08

### Added

- Option `certificate_check` with `pin` (default), `accept_any` and `public_ca`. `pin` trusts the console's certificate seen at the first connection and from then on only that one, so the Protect password is protected without copying a fingerprint from the log
- Bridge device *UP-AirQuality MQTT Bridge* in `pin` mode: *Certificate changed* (problem, with the pinned and the presented fingerprint) and the button *Accept new certificate*, which pins the new certificate and connects again at once
- Documentation: the three checks side by side, why there is no plain HTTP

### Changed

- `protect_host` takes what people type: a name, an IP address, a URL with scheme and path, a port, IPv6; before, `https://…` made the bridge build a broken address
- Without a fingerprint set, existing installations now pin the console's certificate at the first start of 2.4.0 instead of accepting any; choose `accept_any` to keep the old behaviour
- `certificate_fingerprint` is optional and pins its fingerprint whatever `certificate_check` says
- `verify_ssl` is replaced by `certificate_check`; `true` still means `public_ca`

### Fixed

- `public_ca` with an IP address and a certificate no public CA vouches for end in a message that says what to change instead of a TLS error

## 2.3.1 - 2026-10-07

### Fixed

- A reading switched off under *Events to Capture* turns unknown at once; before, it kept its last value. Its *Outside Safe Zone* turns unknown as well
- *Vape Detected* turns unknown, not on, while vape is switched off in Protect

## 2.3.0 - 2026-10-07

### Added

- *Vape Detected*: on when Protect rates the vape index anything but `safe`
- *Reading* Outside Safe Zone: for every reading with a safe zone set in Protect, on while the reading is outside of it; sent at once, regardless of `min_interval`. Zones added or removed in Protect add or remove the sensor
- With `enable_controls`: the *Events to Capture* switch per reading and the vape sensitivity

## 2.2.0 - 2026-10-07

### Added

- NOx index thresholds, and any further threshold a sensor offers

### Changed

- The threshold controls follow what each sensor reports in Protect instead of a fixed list; their ranges are the measurement ranges of Ubiquiti's data sheet

### Removed

- Duplicate definitions and an unused function in the Protect client

## 2.1.0 - 2026-10-07

### Changed

- Device based MQTT discovery: one retained message per sensor announces it with all its entities. The entities announced one by one before are handed over with their entity IDs and history, and their old topics are removed
- Switched-off controls are removed through the device message
- `log_level` offers `info`, `warning` or `error`, like the other apps

### Removed

- `log_level: debug`; set `info` if you used it

## 2.0.1 - 2026-10-07

### Fixed

- The log shows the console's certificate fingerprint to pin; 2.0.0 could not read it

## 2.0.0 - 2026-10-07

A bridge of its own replaces the UPAQ-MQTT bridge. Entity IDs, unique IDs and history carry over.

**Breaking: option names changed.** Re-enter the Protect host, user and password after the update: `PROTECT_HOST` → `protect_host`, `PROTECT_USER` → `protect_username`, `PROTECT_PASS` → `protect_password`, `ENABLE_CONTROLS` → `enable_controls`, `MIN_PUBLISH_INTERVAL` → `min_interval`, `DISCOVERY_PREFIX` → `discovery_prefix`, `DEBUG_LOG` → `log_level`. The MQTT broker now comes from the Supervisor; `mqtt_*` options are only needed for another broker.

### Added

- Option `certificate_fingerprint` pins the console's certificate; the log shows the fingerprint to use
- A sensor's entities turn unavailable when it disconnects from Protect or Protect is out of reach
- Sensors added in Protect show up without a restart; removed sensors and switched-off controls are removed from Home Assistant
- Renaming a sensor in Protect renames its device
- Icon, logo, German translation of the options
- Documentation: user setup, certificate pinning, example automation, troubleshooting, known limitations, removing the app

### Changed

- Each reading has its own retained state topic and is only sent when it changed; before, every update sent all readings of a sensor, about once a second
- `min_interval` applies per reading and always sends the latest value
- Threshold controls are disabled by default
- The bridge is built from this repository; nothing is downloaded at build time
- Home Assistant base image with s6

### Security

- AppArmor profile

## 1.1.0 - 2026-10-06

### Added

- amd64 support
- Documentation

### Changed

- bridge.py downloaded at build time, pinned to commit dc605c2 and checked against its hash, instead of being part of the repository
- No defaults for hosts and credentials; MQTT_HOST defaults to the Mosquitto broker app
- ENABLE_CONTROLS off by default
- English texts throughout

## 1.0.3 - 2026-10-06

### Added

- First version in this repository

[2.4.0]: https://github.com/n-schilling/ha-app-upaq-mqtt/releases/tag/v2.4.0
