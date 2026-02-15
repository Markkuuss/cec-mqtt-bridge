#!/usr/bin/env bash
set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "Please run as root (e.g. sudo ./install.sh)"
  exit 1
fi

APP_NAME="cec-mqtt-bridge"
INSTALL_DIR="/usr/local/lib/${APP_NAME}"
BIN_SYMLINK="/usr/local/bin/${APP_NAME}"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
CFG_DIR="/etc/${APP_NAME}"
CFG_FILE="${CFG_DIR}/config.ini"
LEGACY_CFG_FILE="/etc/${APP_NAME}.ini"

REPO_ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"

# Keep installer invocation from anywhere deterministic.
cd "${REPO_ROOT}"

echo "[1/8] Install dependencies"
apt-get update
apt-get install -y git python3 python3-paho-mqtt

echo "[2/8] Install/ensure CEC Python bindings"
if ! python3 - <<'PY' >/dev/null 2>&1
import cec
PY
then
  # Use a private temp build root to avoid /tmp path hijacking.
  BUILD_ROOT="$(mktemp -d /tmp/cec-mqtt-bridge-build.XXXXXX)"
  trap 'rm -rf "${BUILD_ROOT}"' EXIT
  PLATFORM_SRC="${BUILD_ROOT}/platform"
  LIBCEC_SRC="${BUILD_ROOT}/libcec"

  # Try distro package first.
  if apt-get install -y python3-cec; then
    if ! python3 - <<'PY' >/dev/null 2>&1
import cec
PY
    then
      echo "ERROR: python3-cec installed, but Python module 'cec' is not importable." >&2
      exit 1
    fi
  else
    echo "python3-cec not available, building libcec with Python bindings..."
    apt-get install -y cmake build-essential swig python3-dev \
      libudev-dev libxrandr-dev libx11-dev libgl1-mesa-dev

    # libcec needs p8-platform. Prefer distro package, otherwise build it.
    if ! apt-get install -y libp8-platform-dev; then
      echo "libp8-platform-dev not available, building p8-platform from source..."
      git clone https://github.com/Pulse-Eight/platform.git "${PLATFORM_SRC}"
      mkdir -p "${PLATFORM_SRC}/build"
      cd "${PLATFORM_SRC}/build"
      cmake ..
      make -j"$(getconf _NPROCESSORS_ONLN)"
      make install
      ldconfig
    fi

    git clone https://github.com/Pulse-Eight/libcec.git "${LIBCEC_SRC}"
    mkdir -p "${LIBCEC_SRC}/build"
    cd "${LIBCEC_SRC}/build"
    cmake .. -DHAVE_LINUX_API=1 -DHAVE_RPI_API=0
    make -j"$(getconf _NPROCESSORS_ONLN)"
    make install
    ldconfig

    if ! python3 - <<'PY' >/dev/null 2>&1
import cec
PY
    then
      echo "ERROR: libcec build finished, but Python module 'cec' is still not importable." >&2
      exit 1
    fi
  fi
fi

# Ensure subsequent relative paths resolve from repo root.
cd "${REPO_ROOT}"

echo "[3/8] Create install dir: ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"

echo "[4/8] Copy application"
cp -f "src/cec_mqtt_bridge.py" "${INSTALL_DIR}/cec_mqtt_bridge.py"
chmod 755 "${INSTALL_DIR}/cec_mqtt_bridge.py"

echo "[5/8] Install default config (only if missing): ${CFG_FILE}"
mkdir -p "${CFG_DIR}"
if [ ! -f "${CFG_FILE}" ]; then
  if [ -f "${LEGACY_CFG_FILE}" ]; then
    mv -f "${LEGACY_CFG_FILE}" "${CFG_FILE}"
    chmod 644 "${CFG_FILE}"
    echo "Migrated legacy config to ${CFG_FILE}"
  else
    cp -f "config.ini" "${CFG_FILE}"
    chmod 644 "${CFG_FILE}"
    echo "Installed new config at ${CFG_FILE}"
  fi
else
  echo "Config already exists, keeping: ${CFG_FILE}"
fi

echo "[6/8] Create symlink: ${BIN_SYMLINK}"
ln -sf "${INSTALL_DIR}/cec_mqtt_bridge.py" "${BIN_SYMLINK}"

echo "[7/8] Install systemd service"
install -m 644 -C "cec-mqtt-bridge.service" "${SERVICE_FILE}"

echo "[8/8] Reload and enable service"
systemctl daemon-reload
systemctl enable --now "${APP_NAME}.service"

echo "Done."
echo "Edit config: sudo nano ${CFG_FILE}"
echo "Status:      systemctl status ${APP_NAME}.service"
echo "Logs:        journalctl -u ${APP_NAME}.service -f"
