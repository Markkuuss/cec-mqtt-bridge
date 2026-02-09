#!/bin/bash
set -e
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHONPATH="$REPO_ROOT/src" python3 -m cec_mqtt_bridge.bridge -v -f \
  "$REPO_ROOT/config/cec-mqtt-bridge.ini" |& tee -a "$(date +"%Y_%m_%d_%I_%M_%p").log"
