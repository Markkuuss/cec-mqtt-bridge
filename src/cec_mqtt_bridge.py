#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Main HDMI-CEC to MQTT bridge (single-file build)."""

import argparse
import configparser as ConfigParser
import logging
import math
import os
import re
import threading
import time
from typing import Callable, List, Optional

import paho.mqtt.client as mqtt

try:
    import cec
except ModuleNotFoundError:
    cec = None

LOGGER = logging.getLogger("bridge")

CEC_DEFAULT_CONFIGURATION = {
    "port": "Linux",
    "devices": "0,1,2,3,4,5,6,7,8,9,10,11,12,13,14",
    "name": "CEC Bridge",
    "refresh": "30",
    "keypress_duration_ms": "0",
    "keypress_gap_ms": "200",
}

DEFAULT_CONFIGURATION = {
    "mqtt": {
        "broker": "localhost",
        "name": "CEC Bridge",
        "port": 1883,
        "prefix": "cec-mqtt",
        "user": "",
        "password": "",
        "tls": 0,
    },
    "cec": CEC_DEFAULT_CONFIGURATION,
}


class HdmiCec:
    """HDMI CEC interface class."""

    def __init__(
        self,
        port: str,
        name: str,
        devices: List[int],
        mqtt_send: Callable,
        keypress_duration_ms: int = 0,
        keypress_gap_ms: int = 200,
    ):
        if cec is None:
            raise RuntimeError("CEC support is not available (python-cec not installed).")

        self._mqtt_send = mqtt_send
        self.devices = devices
        self.volume_correction = 1
        self.keypress_duration = max(0.0, keypress_duration_ms / 1000.0)
        self.keypress_gap = max(0.0, keypress_gap_ms / 1000.0)

        self.setting_volume = False
        self.refreshing = False
        self.volume_update = threading.Event()
        self.volume_update.clear()
        self._key_lock = threading.Lock()

        self.cec_config = cec.libcec_configuration()
        self.cec_config.strDeviceName = name
        self.cec_config.bActivateSource = 0
        self.cec_config.deviceTypes.Add(cec.CEC_DEVICE_TYPE_RECORDING_DEVICE)
        self.cec_config.clientVersion = cec.LIBCEC_VERSION_CURRENT
        self.cec_config.SetLogCallback(self._on_log_callback)
        self.cec_config.SetKeyPressCallback(self._on_key_press_callback)
        self.cec_config.SetCommandCallback(self._on_command_callback)

        self.cec_client = cec.ICECAdapter.Create(self.cec_config)
        if not port:
            if os.path.exists("/dev/cec0"):
                port = "/dev/cec0"
            elif os.path.exists("/dev/cec1"):
                port = "/dev/cec1"
            else:
                port = "RPI"
            LOGGER.info("Auto-selected CEC port %s", port)

        LOGGER.info("Opening HDMI-CEC device %s", port)
        if not self.cec_client.Open(port):
            raise ConnectionError(f"Could not connect to CEC adapter {port}")

        self.device_id = self.cec_client.GetLogicalAddresses().primary
        LOGGER.info("Connected to HDMI-CEC with ID %d", self.device_id)
        self.scan()

    def _on_log_callback(self, level, _time, message):
        level_map = {
            cec.CEC_LOG_ERROR: "ERROR",
            cec.CEC_LOG_WARNING: "WARNING",
            cec.CEC_LOG_NOTICE: "NOTICE",
            cec.CEC_LOG_TRAFFIC: "TRAFFIC",
            cec.CEC_LOG_DEBUG: "DEBUG",
        }
        LOGGER.debug("LOG: [%s] %s", level_map.get(level), message)

        if not self.refreshing:
            match = re.search(
                r"\(([0-9a-fA-F])\): power status changed from \'.*\' to \'(.*)\'",
                message,
            )
            if match:
                device = int(match.group(1), 16)
                power = match.group(2)
                self._mqtt_send(f"cec/device/{device}/power", power)

    def _on_key_press_callback(self, key, duration):
        LOGGER.debug("_on_key_press_callback %s %s", key, duration)
        return self.cec_client.KeyPressCallback(key, duration)

    def _on_command_callback(self, cmd):
        initiator = int(cmd[3:4], base=16)
        destination = int(cmd[4:5], base=16)
        opcode = int(cmd[6:8], base=16)
        LOGGER.debug(
            "_on_command_callback %02x %s %x -> %x %s",
            opcode,
            self.cec_client.OpcodeToString(opcode),
            initiator,
            destination,
            cmd,
        )
        self._mqtt_send("cec/rx", cmd[3:])

        if not self.refreshing:
            if opcode == cec.CEC_OPCODE_REPORT_POWER_STATUS:
                power = int(cmd[9:], base=16)
                self._mqtt_send(
                    f"cec/device/{initiator}/power",
                    self.cec_client.PowerStatusToString(power),
                )
            elif opcode == cec.CEC_OPCODE_DEVICE_VENDOR_ID:
                vendor_id = int((cmd[9:]).replace(":", ""), base=16)
                self._mqtt_send(
                    f"cec/device/{initiator}/vendor",
                    self.cec_client.VendorIdToString(vendor_id),
                )
            elif opcode == cec.CEC_OPCODE_REPORT_PHYSICAL_ADDRESS:
                physical_address = int((cmd[9:14]).replace(":", ""), base=16)
                self._mqtt_send(f"cec/device/{initiator}/address", f"{physical_address:04x}")
            elif opcode == cec.CEC_OPCODE_REPORT_AUDIO_STATUS:
                mute, volume = self.decode_volume(int(cmd[9:], base=16))
                self._mqtt_send("cec/audio/volume", volume)
                self._mqtt_send("cec/audio/mute", "on" if mute else "off")
                self.volume_update.set()
            elif opcode == cec.CEC_OPCODE_SET_SYSTEM_AUDIO_MODE:
                self._mqtt_send("cec/device/5/power", "on" if int(cmd[9:], base=16) == 1 else "standby")

        return self.cec_client.CommandCallback(cmd)

    def power_on(self, device: int):
        LOGGER.debug("Power on device %d", device)
        self.cec_client.PowerOnDevices(device)
        threading.Thread(
            target=self._publish_verified_power_status,
            args=(device,),
            daemon=True,
        ).start()

    def power_off(self, device: int):
        LOGGER.debug("Power off device %d", device)
        self.cec_client.StandbyDevices(device)
        threading.Thread(
            target=self._publish_verified_power_status,
            args=(device,),
            daemon=True,
        ).start()

    def _publish_verified_power_status(
        self,
        device: int,
        initial_delay: float = 1.5,
        poll_gap: float = 1.0,
        max_polls: int = 8,
    ):
        time.sleep(max(initial_delay, 0.0))

        previous_status = None
        stable_reads = 0
        last_status = None

        for poll_idx in range(max(1, max_polls)):
            power = self.cec_client.GetDevicePowerStatus(device)
            current_status = self.cec_client.PowerStatusToString(power)
            last_status = current_status

            if current_status == previous_status:
                stable_reads += 1
            else:
                stable_reads = 1
                previous_status = current_status

            if stable_reads >= 2:
                LOGGER.debug(
                    "Verified stable power status for device %d after poll %d: %s",
                    device,
                    poll_idx + 1,
                    current_status,
                )
                self._mqtt_send(f"cec/device/{device}/power", current_status)
                return

            if poll_idx < max_polls - 1:
                time.sleep(max(poll_gap, 0.0))

        LOGGER.warning(
            "Power status for device %d remained unstable after %d polls (last=%s); skipping forced publish",
            device,
            max_polls,
            last_status,
        )

    def _parse_key_code(self, key: str) -> int:
        key_value = key.strip()
        if not key_value:
            raise ValueError("CEC key is empty")

        if key_value.lower().startswith("0x"):
            key_code = int(key_value, 16)
        elif re.fullmatch(r"[0-9a-fA-F]{1,2}", key_value) and re.search(r"[a-fA-F]", key_value):
            key_code = int(key_value, 16)
        elif key_value.isdigit():
            key_code = int(key_value, 10)
        else:
            const_key = re.sub(r"[^A-Z0-9]+", "_", key_value.upper())
            const_name = f"CEC_USER_CONTROL_CODE_{const_key}"
            if cec is not None and hasattr(cec, const_name):
                key_code = int(getattr(cec, const_name))
            else:
                raise ValueError(f"Unknown CEC key '{key}'")

        if not 0 <= key_code <= 0xFF:
            raise ValueError(f"CEC key out of range: {key_code}")

        return key_code

    def key_press(self, device: int, key: str, duration: Optional[float] = None):
        key_code = self._parse_key_code(key)
        LOGGER.debug("Key press %s (%02x) to device %d", key, key_code, device)
        if duration is None:
            duration = self.keypress_duration
        with self._key_lock:
            self.tx_command(f"44:{key_code:02x}", device)
            if duration > 0:
                time.sleep(max(duration, 0.01))
                self.tx_command("45", device)
            if self.keypress_gap > 0:
                time.sleep(self.keypress_gap)

    def volume_up(self, amount=1, update=True):
        if amount >= 10:
            LOGGER.debug("Volume up fast with %d", amount)
            for i in range(amount):
                self.cec_client.VolumeUp(i == amount - 1)
                time.sleep(0.1)
        else:
            LOGGER.debug("Volume up with %d", amount)
            for _ in range(amount):
                self.cec_client.VolumeUp()
                time.sleep(0.1)

        if update:
            self.tx_command("71", 5)

    def volume_down(self, amount=1, update=True):
        if amount >= 10:
            LOGGER.debug("Volume down fast with %d", amount)
            for i in range(amount):
                self.cec_client.VolumeDown(i == amount - 1)
                time.sleep(0.1)
        else:
            LOGGER.debug("Volume down with %d", amount)
            for _ in range(amount):
                self.cec_client.VolumeDown()
                time.sleep(0.1)

        if update:
            self.tx_command("71", 5)

    def volume_mute(self):
        LOGGER.debug("Mute AVR")
        self._mqtt_send("cec/audio/mute", "on")
        self.cec_client.AudioMute()

    def volume_unmute(self):
        LOGGER.debug("Unmute AVR")
        self._mqtt_send("cec/audio/mute", "off")
        self.cec_client.AudioUnmute()

    def volume_set(self, requested_volume: int):
        LOGGER.debug("Set volume to %d", requested_volume)
        self.setting_volume = True

        attempts = 0
        while attempts < 10:
            LOGGER.debug("Attempt %d to set volume", attempts)
            self.volume_update.clear()
            self.tx_command("71", device=5)

            LOGGER.debug("Waiting for response...")
            if not self.volume_update.wait(0.2):
                LOGGER.warning("No response received. Retrying...")
                attempts += 1
                continue

            _, current_volume = self.decode_volume(self.cec_client.AudioStatus())
            if current_volume == requested_volume:
                break

            diff = abs(current_volume - requested_volume)
            LOGGER.debug("Difference in volume is %s", diff)

            if diff >= 10:
                diff = math.ceil(diff / 2)
                LOGGER.debug("Changing fast with %d", diff)
                for i in range(diff):
                    if current_volume < requested_volume:
                        self.cec_client.VolumeUp(i == diff - 1)
                    elif current_volume > requested_volume:
                        self.cec_client.VolumeDown(i == diff - 1)
            else:
                LOGGER.debug("Changing slow with %d", diff)
                for _ in range(diff):
                    if current_volume < requested_volume:
                        self.cec_client.VolumeUp()
                    elif current_volume > requested_volume:
                        self.cec_client.VolumeDown()
                    time.sleep(0.1)

            attempts += 1

        self.setting_volume = False

    def decode_volume(self, audio_status) -> tuple[bool, int]:
        mute = audio_status > 127
        volume = audio_status - 128 if mute else audio_status
        real_volume = int(math.ceil(volume * self.volume_correction))

        LOGGER.debug(
            "Audio Status = %s -> Mute = %s, Volume = %s, Real Volume = %s",
            audio_status,
            mute,
            volume,
            real_volume,
        )
        return mute, real_volume

    def tx_command(self, command: str, device: int = None):
        full_command = command if device is None else f"{self.device_id * 16 + device:x}:{command}"
        LOGGER.debug("Sending %s", full_command)
        self.cec_client.Transmit(self.cec_client.CommandFromString(full_command))

    def refresh(self):
        if self.setting_volume:
            return

        LOGGER.debug("Refreshing HDMI-CEC...")
        self.refreshing = True
        for device in self.devices:
            physical_address = self.cec_client.GetDevicePhysicalAddress(device)
            if physical_address != 0xFFFF:
                power = self.cec_client.GetDevicePowerStatus(device)
                power_str = self.cec_client.PowerStatusToString(power)
                LOGGER.debug(
                    "device %d %04x %-12s power %d %s",
                    device,
                    physical_address,
                    self.cec_client.LogicalAddressToString(device),
                    power,
                    power_str,
                )
                self._mqtt_send(f"cec/device/{device}/power", power_str)

        mute, volume = self.decode_volume(self.cec_client.AudioStatus())
        self._mqtt_send("cec/audio/volume", volume)
        self._mqtt_send("cec/audio/mute", "on" if mute else "off")
        self.refreshing = False

    def scan(self):
        LOGGER.debug("requesting CEC bus information ...")
        self.refreshing = True
        for device in self.devices:
            physical_address = self.cec_client.GetDevicePhysicalAddress(device)
            if physical_address != 0xFFFF:
                vendor_id = self.cec_client.GetDeviceVendorId(device)
                active = self.cec_client.IsActiveSource(device)
                cec_version = self.cec_client.GetDeviceCecVersion(device)
                power = self.cec_client.GetDevicePowerStatus(device)
                osd_name = self.cec_client.GetDeviceOSDName(device)

                self._mqtt_send(f"cec/device/{device}/type", self.cec_client.LogicalAddressToString(device))
                self._mqtt_send(f"cec/device/{device}/address", f"{physical_address:04x}")
                self._mqtt_send(f"cec/device/{device}/active", str(active))
                self._mqtt_send(f"cec/device/{device}/vendor", self.cec_client.VendorIdToString(vendor_id))
                self._mqtt_send(f"cec/device/{device}/osd", osd_name)
                self._mqtt_send(f"cec/device/{device}/cecver", self.cec_client.CecVersionToString(cec_version))
                self._mqtt_send(f"cec/device/{device}/power", self.cec_client.PowerStatusToString(power))

        mute, volume = self.decode_volume(self.cec_client.AudioStatus())
        self._mqtt_send("cec/audio/volume", volume)
        self._mqtt_send("cec/audio/mute", "on" if mute else "off")
        self.refreshing = False


class Bridge:
    """Main bridge class."""

    def __init__(self, config: dict):
        self.config = config
        self.cec_class = None

        LOGGER.info("Initialising MQTT...")
        self.mqtt_client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.config["mqtt"]["name"],
        )
        self.mqtt_client.on_connect = self.mqtt_on_connect
        self.mqtt_client.on_message = self.mqtt_on_message
        self.mqtt_client.on_disconnect = self.mqtt_on_disconnect
        self.mqtt_client.on_subscribe = self.mqtt_on_subscribe
        self.mqtt_client.on_publish = self.mqtt_on_publish

        if self.config["mqtt"]["user"]:
            self.mqtt_client.username_pw_set(
                self.config["mqtt"]["user"], password=self.config["mqtt"]["password"]
            )
        if int(self.config["mqtt"]["tls"]) == 1:
            self.mqtt_client.tls_set()

        self.mqtt_client.will_set(
            self.config["mqtt"]["prefix"] + "/bridge/status",
            "offline",
            qos=1,
            retain=True,
        )

        tries = 30
        while tries > 0:
            tries -= 1
            try:
                self.mqtt_client.connect(
                    self.config["mqtt"]["broker"], int(self.config["mqtt"]["port"]), 60
                )
                break
            except ConnectionRefusedError:
                LOGGER.error("Connection was refused by the server")
            except OSError as err:
                LOGGER.error("OS error: %s", str(err))

            if tries > 0:
                LOGGER.debug("Retrying in 10 seconds... (%d tries left)", tries)
                time.sleep(10)
        else:
            LOGGER.error("Failed to connect to the MQTT broker after multiple attempts")
            raise ConnectionError("MQTT connect retries exhausted. Can't continue.")

        self.mqtt_client.loop_start()

        LOGGER.info("Initialising CEC...")
        self.cec_class = HdmiCec(
            port=self.config["cec"]["port"],
            name=self.config["cec"]["name"],
            devices=[int(x) for x in self.config["cec"]["devices"].split(",")],
            mqtt_send=self.mqtt_publish,
            keypress_duration_ms=int(self.config["cec"]["keypress_duration_ms"]),
            keypress_gap_ms=int(self.config["cec"]["keypress_gap_ms"]),
        )

    @staticmethod
    def load_config(filename="config.ini"):
        config = {
            "mqtt": dict(DEFAULT_CONFIGURATION["mqtt"]),
            "cec": dict(DEFAULT_CONFIGURATION["cec"]),
        }
        LOGGER.info("Loading config %s", filename)

        config_parser = ConfigParser.ConfigParser()
        if config_parser.read(filename):
            for section in config_parser.sections():
                if section not in config:
                    LOGGER.warning("Ignoring unknown config section: %s", section)
                    continue
                config[section].update(dict(config_parser.items(section)))

        for section, key_values in config.items():
            for key, value in key_values.items():
                env = os.getenv(section.upper() + "_" + key.upper())
                if env:
                    config[section][key] = type(value)(env)

        return config

    def mqtt_on_connect(self, client: mqtt, _userdata, _flags, reason_code, _properties):
        if reason_code == 0:
            LOGGER.info("Connected successfully")
        else:
            LOGGER.error("Connection failed with code %s", reason_code)

        client.subscribe(
            [
                (self.config["mqtt"]["prefix"] + "/cec/device/+/power/set", 0),
                (self.config["mqtt"]["prefix"] + "/cec/device/+/key/set", 0),
                (self.config["mqtt"]["prefix"] + "/cec/audio/volume/set", 0),
                (self.config["mqtt"]["prefix"] + "/cec/audio/mute/set", 0),
                (self.config["mqtt"]["prefix"] + "/cec/tx", 0),
                (self.config["mqtt"]["prefix"] + "/cec/refresh", 0),
                (self.config["mqtt"]["prefix"] + "/cec/scan", 0),
            ]
        )

        self.mqtt_publish("bridge/status", "online", qos=1, retain=True)

    def mqtt_on_disconnect(self, _client: mqtt, _userdata, _disconnect_flags, reason_code, _properties):
        if reason_code == 0:
            LOGGER.info("Disconnected cleanly")
        else:
            LOGGER.warning("Disconnected with reason code %s", reason_code)

    def mqtt_on_subscribe(self, _client: mqtt, _userdata, mid, reason_codes, _properties):
        LOGGER.debug("Subscribed (mid=%s) reason_codes=%s", mid, reason_codes)

    def mqtt_on_publish(self, _client: mqtt, _userdata, mid, reason_code, _properties):
        LOGGER.debug("Published (mid=%s) reason_code=%s", mid, reason_code)

    def mqtt_publish(self, topic, message=None, qos=0, retain=True):
        LOGGER.debug("Send to topic %s: %s", topic, message)
        self.mqtt_client.publish(
            self.config["mqtt"]["prefix"] + "/" + topic, message, qos=qos, retain=retain
        )

    def mqtt_on_message(self, _client: mqtt, _userdata, message):
        try:
            prefix = self.config["mqtt"]["prefix"] + "/"
            if not message.topic.startswith(prefix):
                LOGGER.debug("Ignoring topic outside prefix: %s", message.topic)
                return

            topic = message.topic[len(prefix):].split("/")
            action = message.payload.decode()
            LOGGER.debug("Command received: %s (%s)", topic, message.payload)

            if self.cec_class is None:
                LOGGER.warning(
                    "Ignoring MQTT command before CEC is ready: %s",
                    message.topic,
                )
                return

            if not topic or topic[0] != "cec":
                return

            if topic[0] == "cec":
                if len(topic) < 2:
                    LOGGER.debug("Ignoring incomplete CEC topic: %s", message.topic)
                    return

                if topic[1] == "device":
                    if len(topic) < 4:
                        LOGGER.debug("Ignoring incomplete device topic: %s", message.topic)
                        return
                    device = int(topic[2])
                    if topic[3] == "power":
                        if action == "on":
                            self.cec_class.power_on(device)
                        elif action == "standby":
                            self.cec_class.power_off(device)
                        else:
                            LOGGER.warning("Unknown power command: %s %s", topic, action)
                    elif topic[3] == "key":
                        try:
                            self.cec_class.key_press(device, action)
                        except ValueError:
                            LOGGER.warning("Unknown key command: %s %s", topic, action)

                elif topic[1] == "audio":
                    if len(topic) < 3:
                        LOGGER.debug("Ignoring incomplete audio topic: %s", message.topic)
                        return
                    if topic[2] == "volume":
                        if action == "up":
                            self.cec_class.volume_up()
                        elif action == "down":
                            self.cec_class.volume_down()
                        elif action.isdigit() and int(action) <= 100:
                            self.cec_class.volume_set(int(action))
                        else:
                            LOGGER.warning("Unknown volume command: %s %s", topic, action)

                    if topic[2] == "mute":
                        if action == "on":
                            self.cec_class.volume_mute()
                        elif action == "off":
                            self.cec_class.volume_unmute()
                        else:
                            LOGGER.warning("Unknown mute command: %s %s", topic, action)

                elif topic[1] == "tx":
                    commands = message.payload.decode().split(",")
                    for command in commands:
                        self.cec_class.tx_command(command)

                elif topic[1] == "refresh":
                    self.cec_class.refresh()

                elif topic[1] == "scan":
                    self.cec_class.scan()
        except Exception:
            LOGGER.exception("Failed to handle MQTT message: %s", message.topic)

    def cleanup(self):
        self.mqtt_client.loop_stop()
        self.mqtt_publish("bridge/status", "offline", qos=1, retain=True)
        self.mqtt_client.disconnect()


def main():
    parser = argparse.ArgumentParser(description="HDMI-CEC to MQTT bridge")
    parser.add_argument("-v", "--verbose", action="count", help="increase output verbosity")
    parser.add_argument("-f", "--configfile")
    parser.add_argument("-t", "--refreshtime", type=int)

    args = parser.parse_args()
    log_level = logging.INFO if not args.verbose else logging.DEBUG

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(funcName)s: %(message)s",
    )

    if args.configfile:
        config_file = args.configfile
    elif os.path.isfile("/etc/cec-mqtt-bridge/config.ini"):
        config_file = "/etc/cec-mqtt-bridge/config.ini"
    else:
        config_file = "config.ini"

    config = Bridge.load_config(config_file)
    if args.refreshtime is not None:
        config["cec"]["refresh"] = str(args.refreshtime)

    bridge = Bridge(config)

    refresh_delay = int(bridge.config["cec"]["refresh"])
    if 0 < refresh_delay < 10:
        refresh_delay = 10

    LOGGER.debug("refresh delay %d", refresh_delay)

    try:
        while True:
            if bridge.cec_class and refresh_delay:
                bridge.cec_class.refresh()
                time.sleep(refresh_delay)
            else:
                time.sleep(3600)
    except (KeyboardInterrupt, RuntimeError):
        bridge.cleanup()


if __name__ == "__main__":
    main()
