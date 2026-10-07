# UP-AirQuality MQTT Bridge

Follows the UP-AirQuality sensors in UniFi Protect and brings their readings into Home Assistant through MQTT discovery: one device per sensor, one entity per reading the sensor reports (AQI, CO2, PM1.0 to PM10, TVOC, VOC and NOx index, vape index, and temperature and humidity where reported). Optionally it also controls the LED ring and the alert thresholds.

The bridge keeps one connection to Protect open and receives every change as it happens; it does not poll.

## Requirements

- A UniFi console running Protect with at least one UP-AirQuality sensor
- A local Protect user for the bridge: in UniFi OS, **Admins & Users → Create New**, *Restrict to local access only*, role *View Only* for Protect (*Full Management* if `enable_controls` is on)
- An MQTT broker, e.g. the Mosquitto broker app with the MQTT integration

## Options

| Option | Default | Meaning |
|---|---|---|
| `protect_host` | | Host name or IP address of the console running Protect |
| `protect_username` | | Local Protect user |
| `protect_password` | | Its password |
| `certificate_fingerprint` | | SHA-256 fingerprint of the console's certificate (see below) |
| `verify_ssl` | `false` | Check the certificate against the trusted CAs instead; only for a console with a certificate from a public CA |
| `enable_controls` | `false` | Create LED and threshold controls; the Protect user then needs write access |
| `min_interval` | `60` | Seconds between two published values of one reading; `0` publishes every change |
| `discovery_prefix` | `homeassistant` | MQTT discovery prefix |
| `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password` | | Only for a broker the Supervisor does not know; shown with *Show unused optional configuration options* |
| `log_level` | `info` | How much the app writes to its log: `info`, `warning` or `error` |

### Pinning the console's certificate

UniFi consoles use a self-signed certificate, which a CA check rejects. Without `certificate_fingerprint` the connection is encrypted, but the bridge cannot tell the console from someone pretending to be it, who would then receive the Protect password. While the fingerprint is not set, the log shows the console's at every start:

> The console's certificate is not checked. Set certificate_fingerprint to 3A:1F:…

Copy it into `certificate_fingerprint`. When the console gets a new certificate, the bridge stops with a mismatch error; check that the change is expected and set the new fingerprint.

## Entities

Per sensor, named after the sensor in Protect:

| Entity | Meaning |
|---|---|
| AQI, CO2, PM1.0, PM2.5, PM4.0, PM10, TVOC Index, VOC Index, NOx Index, Vape Index, … | Readings; the `status` attribute holds Protect's rating (e.g. `good`, `moderate`) |
| Firmware Version | Installed firmware (diagnostic) |
| Firmware Update Available | On when Protect offers newer firmware (diagnostic) |
| LED Brightness, LED Metric, Status Light, Night Mode, Night Mode Brightness | Only with `enable_controls` |
| *Reading* Low / High Threshold | Only with `enable_controls`, for every reading the sensor offers alert thresholds for; disabled by default, enable the ones you need. Their ranges are the measurement ranges of Ubiquiti's data sheet; TVOC is not in it and gets a wide range |

A sensor's entities turn unavailable when it disconnects from Protect, when Protect is out of reach, or when the bridge stops. Readings are retained, so they are back right after a restart of Home Assistant.

## Example automation

Notify when the CO2 level stays above 1200 ppm for ten minutes:

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.living_room_co2
    above: 1200
    for: "00:10:00"
actions:
  - action: notify.notify
    data:
      message: "CO2 in the living room is {{ states('sensor.living_room_co2') }} ppm, time to air the room."
```

The entity IDs follow the sensor names in Protect; adjust them to yours.

## Troubleshooting

- **"login refused"**: user or password is wrong, or the user is not a local user. Cloud (UI account) users with two-factor authentication cannot log in.
- **"certificate does not match"**: the console presented another certificate than the pinned one. If you renewed it, set the new fingerprint from the log; if not, check your network.
- **No entities**: the log lists the sensors found at every start. Check that the MQTT integration is set up and uses the discovery prefix of the app.
- **Values change only once a minute**: that is `min_interval`; set it lower, at the cost of a bigger recorder database.
- **Controls do nothing**: the Protect user needs write access; the log shows each change and any error.

## Known limitations

- Uses the private API of the Protect application, which Ubiquiti may change with a Protect update.
- A UniFi console with several Protect instances is not supported.
- Readings arrive about once a second; `min_interval` thins them out per reading, so a short spike between two publications may not show up.

## Removing the app

The entities are announced with retained MQTT messages and stay in Home Assistant after the app is removed. Delete the sensors' devices under **Settings → Devices & services → MQTT**; Home Assistant then removes the retained messages as well. Sensors you remove from Protect while the app runs are removed from Home Assistant at the next start of the app.

## Support

Questions, bugs and ideas: open an issue at https://github.com/n-schilling/ha-app-upaq-mqtt/issues. Please add the app version and the app log.
