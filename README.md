cec-mqtt-bridge
===============

A HDMI-CEC to MQTT bridge written in Python 3 for connecting your AV-devices to your Home Automation system. You can control and monitor power status and volume.
CEC is required; there is no LIRC fallback.

# Features
* HDMI-CEC
  * Power control and feedback
  * Volume control (up/down/specific) and feedback
  * Relay HDMI-CEC messages from HDMI to broker (RX)
  * Relay HDMI-CEC messages from broker to HDMI (TX)

# Dependencies

* MQTT broker (like [Mosquitto](https://mosquitto.org/))
  * "apt-get install mosquitto"

* python paho-mqtt module
  * https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html
  * "apt-get install python3-paho-mqtt"

* python HDMI-CEC module
  * libcec4 with python bindings (https://github.com/Pulse-Eight/libcec)
  * **NOTE: cec python package is not available on pypi**
    * "apt-get install python3-cec" OR compile the bindings yourself
  * HDMI-CEC interface device (like a [Pulse-Eight](https://www.pulse-eight.com/) device, or a Raspberry Pi)

# Install on Raspbian bullseye (no packaging)

If there is not a MQTT broker already on your network
```sh
sudo apt-get install mosquitto
```

Install packages and the bridge
```sh
# Base packages
sudo apt-get update
sudo apt-get install git python3 python3-paho-mqtt mosquitto

# Install the bridge (source only)
git clone https://github.com/Markkuuss/cec-mqtt-bridge.git /opt/cec-mqtt-bridge
cd /opt/cec-mqtt-bridge/scripts/
chmod +x install.sh
./install.sh
sudo vi /etc/cec-mqtt-bridge.ini
sudo systemctl restart cec-mqtt-bridge
```

## CEC Python bindings on Debian 13 (trixie)

Debian 13 does not ship `python3-cec`. The install script will build libcec
with Python bindings automatically if needed. You can also build manually:

```sh
sudo apt-get install -y git cmake build-essential swig python3-dev \
  libudev-dev libxrandr-dev libx11-dev libgl1-mesa-dev libp8-platform-dev

git clone https://github.com/Pulse-Eight/libcec.git /tmp/libcec
cd /tmp/libcec
mkdir -p build && cd build
cmake .. -DSKIP_PYTHON=OFF
make -j$(nproc)
sudo make install
sudo ldconfig
```

to update

```sh
cd /opt/cec-mqtt-bridge/
git pull
sudo systemctl restart cec-mqtt-bridge
```


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
* `mosquitto_pub -t media/cec/volup -m ''`
* `mosquitto_pub -t media/cec/tx -m '15:44:42,15:45'`

# Configuration

You can either copy `config/cec-mqtt-bridge.ini` to `config.ini` and adjust its properties, or alternatively declare any of those as environment variables using the format `SECTION_KEY` (e.g., `MQTT_USER`).


# Interesting links
* https://github.com/nvella/mqtt-cec
* http://www.cec-o-matic.com/
* https://kwikwai.com/knowledge-base/the-hdmi-cec-bus/
* https://www.hdmi.org/docs/Hdmi13aSpecs
* https://github.com/Pulse-Eight/libcec/blob/master/include/cec.h
* https://github.com/Pulse-Eight/libcec/blob/master/src/pyCecClient/pyCecClient.py
