# Integrated Force-FBG-Stage Experiment Panel

This panel runs displacement tests that combine:
- X-axis stage motion (+X only)
- Force reading (`force_z_n`) from either Bota or calibrated Phidget load cell
- FBG interrogator reading (`fbg_1`)

When you click `Start Displacement`, it now runs 5 repeated trials automatically.
Each trial does:
1. Move stage to the configured `Start X` (if not already there)
2. Move stage by the configured `X Displacement`
3. Record start/end `x`, `Fz`, `FBG1` using a 1-second average window while stage is stationary
4. Before the next trial: home the stage, then wait 20 seconds

## Run

```bash
./run_experiment_panel.sh
```

Or directly:

```bash
python experiment_panel.py
```

Optional arguments:

```bash
python experiment_panel.py \
  --output-dir ./experiment_data \
  --bota-config ./bota_driver_config/ethercat_gen0.json \
  --bota-interface enp14s0 \
  --fbg-config ./my_fbg_config.yaml \
  --whisker-name whisker_left_01 \
  --stage-id 1 \
  --stage-port /dev/ttyUSB0
```

Use calibrated Phidget load-cell force (recommended for your setup):

```bash
python experiment_panel.py \
  --enable-loadcell \
  --loadcell-cal ./calibration.json \
  --loadcell-channel 0 \
  --loadcell-rate 200
```

## UI Workflow

1. Click `Connect`
   - Set `Stage ID` to the module you want to drive (1/2/3)
   - Use `Probe IDs` to check positions for IDs 1/2/3 and choose the one that changes with your X-axis movement
   - You can change `Stage ID` after connecting; the panel now rebinds immediately
2. Optional: click `Home X`
3. Set `Start X (mm)` and `X Displacement (mm)`
4. Set `Whisker Name` (used in saved filenames)
5. Set wait times as needed:
   - `Pre-Wait (s)`: wait before motion starts (default `30s`)
   - `Final-Wait (s)`: wait after motion before final FBG/force snapshot (default `30s`)
6. Set `Trial Count` (number of repeated trials per run)
7. Click `Start Displacement`

Current default motion tuning:
- Stage move speed is set to an effective `0.5x` of previous behavior for displacement moves.

## Saved Data

Data is saved into `--output-dir` (default `./experiment_data`).
Each run creates a new folder:

- `<whisker_name>_displacement_YYYYMMDD_HHMMSS/`

- `<whisker_name>_displacement_YYYYMMDD_HHMMSS/trace_trial_01.csv` ... `trace_trial_05.csv`
  - Per-trial phase samples (`initial`, `start_reached`, `end_reached`/`aborted`)
  - `phase, elapsed_s, x_mm, requested_start_x_mm, requested_end_x_mm, force_z_n, fbg1_nm`

- `<whisker_name>_displacement_YYYYMMDD_HHMMSS/summary_table.csv`
  - Contains one row per trial (up to 5 rows)
  - Only these columns are saved:
  - `whisker_name, fbg1_displacement_nm, force_change_n`
  - `force_change_n` is computed from the selected force source (Bota or load cell) as:
  - `end_force - start_force`

## Live Visualization

- The panel includes an `FBG Live Plot` section for `FBG1`.
- Wavelength axes are shown in `nm` (no `k` SI prefix).
- Plot refresh uses the interrogator streaming history and updates continuously while connected.
- FBG plot x-axis uses relative time (`latest=0s`) and trims leading NaN gaps to reduce apparent latency.

## Notes

- Motion is position-based (no PI force control).
- `X Displacement` may be positive or negative; movement is clamped to stage travel range.
- Keep Bota on a dedicated wired NIC; avoid sharing that NIC with other traffic.
- If you see `expected 1, got 2`, your selected `Stage ID` is wrong for that axis.  
  Switch `Stage ID` to match the hardware module ID.
- On Linux, EtherCAT may require root:
  - `sudo -E ./run_experiment_panel.sh`
