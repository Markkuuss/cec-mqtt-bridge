#!/bin/sh

set -e  # Stop on first error.

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"  # Absolute path to this script's directory.
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"  # Repo root (one level up from scripts/).

echo "Updating repository..."
cd "$REPO_ROOT"
git pull

echo "Installing base dependencies..."
sudo apt-get update
sudo apt-get install -y git python3 python3-paho-mqtt mosquitto

echo "Installing/ensuring CEC Python bindings..."
if ! python3 - <<'PY' >/dev/null 2>&1
import cec
PY
then
  # Try distro package first (if available)
  if ! sudo apt-get install -y python3-cec; then
    echo "python3-cec not available, building libcec with Python bindings..."
    sudo apt-get install -y cmake build-essential swig python3-dev \
      libudev-dev libxrandr-dev libx11-dev libgl1-mesa-dev

    # libcec needs p8-platform. Prefer distro package, otherwise build it.
    if ! sudo apt-get install -y libp8-platform-dev; then
      echo "libp8-platform-dev not available, building p8-platform from source..."
      if [ -d /tmp/platform/.git ]; then
        git -C /tmp/platform pull --ff-only
      else
        git clone https://github.com/Pulse-Eight/platform.git /tmp/platform
      fi
      mkdir -p /tmp/platform/build
      cd /tmp/platform/build
      cmake ..
      make -j"$(getconf _NPROCESSORS_ONLN)"
      sudo make install
      sudo ldconfig
    fi

    if [ -d /tmp/libcec/.git ]; then
      git -C /tmp/libcec pull --ff-only
    else
      git clone https://github.com/Pulse-Eight/libcec.git /tmp/libcec
    fi
    mkdir -p /tmp/libcec/build
    cd /tmp/libcec/build
    cmake .. -DHAVE_LINUX_API=1 -DHAVE_RPI_API=0
    make -j"$(getconf _NPROCESSORS_ONLN)"
    sudo make install
    sudo ldconfig

    if ! python3 - <<'PY' >/dev/null 2>&1
import cec
PY
    then
      echo "ERROR: libcec build finished, but Python module 'cec' is still not importable." >&2
      exit 1
    fi
  fi
fi

if [ -f /etc/cec-mqtt-bridge.ini ]; then
  echo "/etc/cec-mqtt-bridge.ini exists, did not copy new config, you may need to edit existing!"
else
  sudo cp "$REPO_ROOT/config/cec-mqtt-bridge.ini" /etc/cec-mqtt-bridge.ini
fi

sudo install -m 644 -C "$REPO_ROOT/systemd/cec-mqtt-bridge.service" /etc/systemd/system/cec-mqtt-bridge.service

sudo systemctl daemon-reload
sudo systemctl enable cec-mqtt-bridge
sudo systemctl start cec-mqtt-bridge
