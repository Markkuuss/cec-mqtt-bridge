cec-mqtt-bridge
===============

A HDMI-CEC to MQTT bridge written in Python 3 for connecting your AV-devices to your Home Automation system. You can control and monitor power status and volume.
CEC is required.

# Features
* HDMI-CEC
  * Power control and feedback
  * Volume control (up/down/specific) and feedback
  * Relay HDMI-CEC messages from HDMI to broker (RX)
  * Relay HDMI-CEC messages from broker to HDMI (TX)

# Dependencies

* MQTT broker (Mosquitto)
* python3-paho-mqtt
* libcec + Python bindings (the installer builds them if missing; Debian 13 has no `python3-cec`)
* HDMI-CEC interface device (Raspberry Pi HDMI-CEC or a Pulse-Eight adapter)

# Install on Debian / Raspberry Pi OS (no packaging)

```sh
git clone --branch integration --single-branch https://github.com/Markkuuss/cec-mqtt-bridge.git /opt/cec-mqtt-bridge
cd /opt/cec-mqtt-bridge/scripts
chmod +x install.sh
sudo ./install.sh
```
`install.sh` installs dependencies, updates the repo (`git pull`), installs the systemd unit, and starts the service.

Edit the config:
```sh
sudo nano /etc/cec-mqtt-bridge.ini
```

Start / restart:
```sh
sudo systemctl restart cec-mqtt-bridge
sudo systemctl status cec-mqtt-bridge
```

Update: re-run the installer (it runs `git pull` and restarts the service).
```sh
cd /opt/cec-mqtt-bridge/scripts
chmod +x install.sh
sudo ./install.sh
```

Note: On Debian 13, `python3-cec` is not available; the installer builds libcec
Python bindings automatically if needed.


# MQTT Topics

The bridge subscribes to the following topics:

| topic                       | body                                    | remark                                                                    |
|:----------------------------|-----------------------------------------|---------------------------------------------------------------------------|
| `prefix`/cec/device/`laddr`/power/set | `on` / `standby`              | Turn on/standby device with with logical address `laddr` (0-14).  |
| `prefix`/cec/device/`laddr`/active/set | `yes` / `no`                 | activate/deactivate device with with logical address `laddr` (0-14).  |
| `prefix`/cec/device/`laddr`/key/set | `key`                          | Send a CEC user control `key` to device `laddr` (0-14). Use `0xNN`, decimal, or a `CEC_USER_CONTROL_CODE_*` name without the prefix (e.g. `PLAY`). See https://github.com/Pulse-Eight/libcec/blob/master/include/cectypes.h#L634 |
| `prefix`/cec/audio/volume/set     | `integer (0-100)` / `up` / `down` | Sets the volume level of the audio system to a specific level or up/down. |
| `prefix`/cec/audio/mute/set       | `on` / `off`                      | Mute/Unmute the the audio system.                                         |
| `prefix`/cec/tx             | `commands`                              | Send the specified `commands` to the CEC bus. You can specify multiple commands by separating them with a space. Example: `cec/tx 15:44:41,15:45`. |

The bridge publishes to the following topics:

| topic                          | body                                    | remark                                           |
|:-------------------------------|-----------------------------------------|--------------------------------------------------|
| `prefix`/bridge/status               | `online` / `offline`                    | Report availability status of the bridge.        |
| `prefix`/cec/device/`laddr`/type     | `on` / `off`                            | Report type of device with logical address `laddr` (0-14).      |
| `prefix`/cec/device/`laddr`/address  | `on` / `off`                            | Report physical address of device with logical address `laddr` (0-14).  |
| `prefix`/cec/device/`laddr`/active   | `yes` / `no`                            | Report active source status of device with logical address `laddr` (0-14).  |
| `prefix`/cec/device/`laddr`/vendor   | `string`                            | Report vendor of device with logical address `laddr` (0-14).  |
| `prefix`/cec/device/`laddr`/osd      | `string`                            | Report OSD of device with logical address `laddr` (0-14).  |
| `prefix`/cec/device/`laddr`/cecver   | `string`                            | Report CEC version of device with logical address `laddr` (0-14).  |
| `prefix`/cec/device/`laddr`/power    | `on` / `standby` / `toon` / `tostandby` / `unknown` | Report power status of device with logical address `laddr` (0-14).      |
| `prefix`/cec/device/`laddr`/language | `string`                            | Report langauge of device with logical address `laddr` (0-14).  |
| `prefix`/cec/audio/volume     | `integer (0-100)` /  `unknown = 127`                      | Report volume level of the audio system.         |
| `prefix`/cec/mute/status       | `on` / `off`                            | Report mute status of the audio system.          |
| `prefix`/cec/rx                | `command`                               | Notify that `command` was received.              |

`id` is the address (0-15) of the device on the CEC-bus.

## Examples
* `mosquitto_pub -t cec-mqtt/cec/volup -m ''`
* `mosquitto_pub -t cec-mqtt/cec/tx -m '15:44:42,15:45'`

# Configuration

The service reads `/etc/cec-mqtt-bridge.ini` by default. You can either copy
`config/cec-mqtt-bridge.ini` to `/etc/cec-mqtt-bridge.ini` and adjust its
properties, or alternatively declare any of those as environment variables
using the format `SECTION_KEY` (e.g., `MQTT_USER`).
