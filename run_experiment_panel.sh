#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"
USER_ARGS=("$@")
AUTO_ARGS=()
want_bota=0

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

for ((i=0; i<${#USER_ARGS[@]}; i++)); do
  arg="${USER_ARGS[$i]}"
  case "$arg" in
    --enable-bota)
      want_bota=1
      ;;
    --disable-bota)
      want_bota=0
      ;;
  esac
done

if [[ "${want_bota}" -eq 1 && "${EUID}" -ne 0 ]]; then
  echo "Warning: EtherCAT on Linux often needs root privileges."
  echo "If Bota init fails, retry with: sudo -E ./run_experiment_panel.sh"
fi

has_stage_port=0
has_stage_id=0
has_bota_iface=0
for ((i=0; i<${#USER_ARGS[@]}; i++)); do
  arg="${USER_ARGS[$i]}"
  case "$arg" in
    --stage-port)
      has_stage_port=1
      i=$((i+1))
      ;;
    --stage-port=*)
      has_stage_port=1
      ;;
    --stage-id)
      has_stage_id=1
      i=$((i+1))
      ;;
    --stage-id=*)
      has_stage_id=1
      ;;
    --bota-interface)
      has_bota_iface=1
      i=$((i+1))
      ;;
    --bota-interface=*)
      has_bota_iface=1
      ;;
  esac
done

if [[ "${has_stage_port}" -eq 0 ]]; then
  stage_port=""
  if [[ -d /dev/serial/by-id ]]; then
    while IFS= read -r entry; do
      resolved="$(readlink -f "$entry" || true)"
      if [[ "$resolved" == /dev/ttyUSB* || "$resolved" == /dev/ttyACM* ]]; then
        stage_port="$entry"
        break
      fi
    done < <(ls -1 /dev/serial/by-id/* 2>/dev/null || true)
  fi

  if [[ -z "$stage_port" ]]; then
    for dev in /dev/ttyUSB* /dev/ttyACM*; do
      if [[ -e "$dev" ]]; then
        stage_port="$dev"
        break
      fi
    done
  fi

  if [[ -n "$stage_port" ]]; then
    echo "Auto stage port: ${stage_port}"
    AUTO_ARGS+=(--stage-port "$stage_port")
  fi
fi

if [[ "${has_stage_id}" -eq 0 && -n "${STAGE_MODULE_ID:-}" ]]; then
  AUTO_ARGS+=(--stage-id "${STAGE_MODULE_ID}")
fi

if [[ "${want_bota}" -eq 1 && "${has_bota_iface}" -eq 0 && -z "${BOTA_NETWORK_INTERFACE:-}" ]]; then
  bota_iface=""
  while read -r iface state _; do
    if [[ "$iface" == "lo" ]]; then
      continue
    fi
    if [[ "$state" == "UP" && "$iface" == en* ]]; then
      bota_iface="$iface"
      break
    fi
  done < <(ip -br link 2>/dev/null || true)

  if [[ -z "$bota_iface" ]]; then
    while read -r iface state _; do
      if [[ "$iface" == "lo" ]]; then
        continue
      fi
      if [[ "$state" == "UP" ]]; then
        bota_iface="$iface"
        break
      fi
    done < <(ip -br link 2>/dev/null || true)
  fi

  if [[ -n "$bota_iface" ]]; then
    echo "Auto Bota interface: ${bota_iface}"
    AUTO_ARGS+=(--bota-interface "$bota_iface")
  fi
fi

if ! "$PYTHON_BIN" -c "import serial, PyQt5" >/dev/null 2>&1; then
  if command -v conda >/dev/null 2>&1 && conda run -n whisker python -c "import serial, PyQt5" >/dev/null 2>&1; then
    echo "Using whisker conda environment..."
    exec conda run -n whisker bash -lc \
      'export LD_LIBRARY_PATH="$CONDA_PREFIX/lib:${LD_LIBRARY_PATH:-}"; python "$@"' _ \
      "${ROOT_DIR}/experiment_panel.py" "${AUTO_ARGS[@]}" "${USER_ARGS[@]}"
  fi
fi

exec "$PYTHON_BIN" "${ROOT_DIR}/experiment_panel.py" "${AUTO_ARGS[@]}" "${USER_ARGS[@]}"
