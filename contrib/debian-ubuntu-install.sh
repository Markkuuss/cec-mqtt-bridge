#!/bin/sh

PREFIX="/usr/local"
VENV_DIR="/opt/cec-mqtt-bridge/venv"

cd ..

python3 -m build

sudo mkdir -p /opt/cec-mqtt-bridge
sudo python3 -m venv "$VENV_DIR"
sudo "$VENV_DIR/bin/python" -m pip install --upgrade pip
sudo "$VENV_DIR/bin/pip" install --force-reinstall dist/cec_mqtt_bridge-*.whl

if [ -f /etc/cec-mqtt-bridge.ini ]; then
  echo "/etc/cec-mqtt-bridge.ini exists, did not copy new config, you may need to edit existing!" 
else
  sudo cp config.ini.default /etc/cec-mqtt-bridge.ini
fi

sudo install -m 644 -C debian/cec-mqtt-bridge.service /etc/systemd/system/cec-mqtt-bridge.service

sudo systemctl enable cec-mqtt-bridge
sudo systemctl daemon-reload
sudo systemctl start cec-mqtt-bridge


