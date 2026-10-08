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
| `protect_host` | | Host name or IP address of the console running Protect, e.g. `192.168.1.1`; `https://192.168.1.1/` or `192.168.1.1:8443` work too |
| `protect_username` | | Local Protect user |
| `protect_password` | | Its password |
| `certificate_check` | `pin` | How the console's certificate is checked: `pin`, `accept_any` or `public_ca` (see below) |
| `certificate_fingerprint` | | Optional: the SHA-256 fingerprint to pin, instead of the one seen first |
| `enable_controls` | `false` | Create LED and threshold controls; the Protect user then needs write access |
| `min_interval` | `60` | Seconds between two published values of one reading; `0` publishes every change |
| `discovery_prefix` | `homeassistant` | MQTT discovery prefix |
| `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password` | | Only for a broker the Supervisor does not know; shown with *Show unused optional configuration options* |
| `log_level` | `info` | How much the app writes to its log: `info`, `warning` or `error` |

### The console's certificate

UniFi consoles use a self-signed certificate. The connection is always encrypted; `certificate_check` decides how the bridge makes sure it talks to your console and not to someone in between, who would then receive the Protect password.

| `certificate_check` | Accepts | Protects the password against a man in the middle |
|---|---|---|
| `pin` (default) | The certificate seen at the first connection, and from then on only that one | Yes, from the first connection on |
| `accept_any` | Any certificate, also self-signed or expired | No |
| `public_ca` | A certificate from a public CA (e.g. Let's Encrypt) for the name in `protect_host`; not with an IP address | Yes |

With `pin` the bridge keeps the fingerprint in its private data and writes it to the log. To pin a known fingerprint from the start, set `certificate_fingerprint`; a fingerprint set there is always the one pinned, whatever `certificate_check` says.

**When the console gets a new certificate** (rare; e.g. after a reset or a new domain), the app stops without sending the password, and its log says:

> Certificate mismatch, configured fingerprint: EA:37:… found fingerprint: 5C:90:…

Check that the change is expected, e.g. in your browser on the console's page, then copy the found fingerprint into `certificate_fingerprint` and start the app. Only admins can change the app's options, so nobody else can make the bridge trust another certificate.

`verify_ssl` of versions before 2.4.0 is still understood: `true` means `public_ca`.

### Why not plain HTTP

UniFi consoles answer plain HTTP only with a redirect to HTTPS, for the login and the Protect API alike, so the bridge always uses HTTPS. Without it the Protect password would cross your network in clear text.

## Entities

Per sensor, named after the sensor in Protect:

| Entity | Meaning |
|---|---|
| AQI, CO2, PM1.0, PM2.5, PM4.0, PM10, TVOC Index, VOC Index, NOx Index, Vape Index, … | Readings; the `status` attribute holds Protect's rating (e.g. `good`, `moderate`) |
| Vape Detected | On when Protect rates the vape index anything but `safe` |
| *Reading* Outside Safe Zone | Only for readings with a safe zone set in Protect (*Add Safe Zone* under *Events to Capture*); on while the reading is below or above it, at once and regardless of `min_interval`. The attributes hold the zone. Read access is enough, so the zones you keep in UniFi drive your automations |
| Firmware Version | Installed firmware (diagnostic) |

The bridge's own device *UP-AirQuality MQTT Bridge* has *Protect connection*: on while the bridge is logged in to Protect and follows its updates, off only while Protect cannot be reached (network or HTTP error), unavailable while the app is stopped. Starting or stopping the app, a refused login or a certificate mismatch do not turn it off; Protect answered in those cases, the log says why the bridge does not use it. An automation can warn you, e.g. when it has not been on for ten minutes:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.up_airquality_mqtt_bridge_protect_connection
    not_to: "on"
    for: "00:10:00"
```
| Firmware Update Available | On when Protect offers newer firmware (diagnostic) |
| LED Brightness, LED Metric, Status Light, Night Mode, Night Mode Brightness | Only with `enable_controls` |
| *Reading* Events | Only with `enable_controls`: the *Events to Capture* switch of a reading in Protect |
| Vape Sensitivity | Only with `enable_controls`: Protect's vape sensitivity in % |
| *Reading* Low / High Threshold | Only with `enable_controls`, for every reading the sensor offers alert thresholds for; disabled by default, enable the ones you need. Their ranges are the measurement ranges of Ubiquiti's data sheet; TVOC is not in it and gets a wide range |

A sensor's entities turn unavailable when it disconnects from Protect, when Protect is out of reach, or when the bridge stops. A reading you switch off under *Events to Capture* in Protect turns unknown, and so do its *Outside Safe Zone* and, for vape, *Vape Detected*; switched on again, the value is back at once. Readings are retained, so they are back right after a restart of Home Assistant.

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

Notify when vaping is detected in a room:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.kids_room_vape_detected
    to: "on"
actions:
  - action: notify.notify
    data:
      message: "Vaping detected in the kids' room."
```

Air the room while CO2 is outside the safe zone you set in UniFi:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.living_room_co2_outside_safe_zone
    to: "on"
    for: "00:05:00"
actions:
  - action: notify.notify
    data:
      message: "CO2 in the living room is outside its safe zone: {{ states('sensor.living_room_co2') }} ppm."
```

The entity IDs follow the sensor names in Protect; adjust them to yours.

## Troubleshooting

- **"login refused"**: user or password is wrong, or the user is not a local user. Cloud (UI account) users with two-factor authentication cannot log in.
- **"Certificate mismatch"**: see *When the console gets a new certificate* above.
- **"no trusted CA vouches for the console's certificate"**: `public_ca` needs a certificate from a public CA; choose `pin` for the console's own certificate.
- **"public_ca needs protect_host to be the name"**: a certificate names the console, not its IP address; enter the name or choose `pin`.
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
