#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"

if [[ -n "${CONDA_PREFIX:-}" && -x "${CONDA_PREFIX}/bin/python" ]]; then
  PYTHON_BIN="${CONDA_PREFIX}/bin/python"
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  fi
fi

if [[ -n "${CONDA_PREFIX:-}" && -d "${CONDA_PREFIX}/lib" ]]; then
  export LD_LIBRARY_PATH="${CONDA_PREFIX}/lib:${LD_LIBRARY_PATH:-}"
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Warning: EtherCAT on Linux often requires root privileges."
  echo "If init fails, run: sudo -E ./run_bota_realtime.sh"
fi

declare -a iface_candidates
if [[ -n "${BOTA_NETWORK_INTERFACE:-}" ]]; then
  iface_candidates=("${BOTA_NETWORK_INTERFACE}")
else
  while IFS= read -r iface; do
    iface_candidates+=("$iface")
  done < <(ip -br link | awk '$1!="lo" && $2=="UP" && $1 ~ /^en/ {print $1}')

  if [[ ${#iface_candidates[@]} -eq 0 ]]; then
    while IFS= read -r iface; do
      iface_candidates+=("$iface")
    done < <(ip -br link | awk '$1!="lo" && $2=="UP" {print $1}')
  fi
fi

if [[ ${#iface_candidates[@]} -eq 0 ]]; then
  echo "No active network interface found."
  exit 1
fi

declare -a config_candidates
if [[ -n "${BOTA_CONFIG_PATH:-}" ]]; then
  config_candidates=("${BOTA_CONFIG_PATH}")
else
  config_candidates=(
    "${ROOT_DIR}/bota_driver_config/ethercat_gen0.json"
    "${ROOT_DIR}/bota_driver_config/ethercat.json"
  )
fi

for config_path in "${config_candidates[@]}"; do
  if [[ ! -f "$config_path" ]]; then
    continue
  fi

  for iface in "${iface_candidates[@]}"; do
    echo "Trying interface=${iface} config=$(basename "$config_path")"
    if BOTA_NETWORK_INTERFACE="$iface" BOTA_CONFIG_PATH="$config_path" "$PYTHON_BIN" "${ROOT_DIR}/bota_sensor/visualize_realtime.py"; then
      exit 0
    fi
    echo "Attempt failed: interface=${iface} config=$(basename "$config_path")"
  done
done

echo "All interface/config attempts failed."
echo "Set a dedicated NIC and verify sensor power/cable, then retry with sudo -E ./run_bota_realtime.sh"
exit 1
