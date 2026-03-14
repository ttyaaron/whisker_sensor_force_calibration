from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Optional

from .config import DEFAULT_CONFIG, FBGConfig, load_config
from .plotting import LivePlotWindow, create_application
from .streaming import FBGStreamReader


def run_live_plot(config: Optional[FBGConfig] = None, wait_ready_timeout: float = 5.0) -> None:
    """Launch the live FBG plotter."""
    cfg = copy.deepcopy(config or DEFAULT_CONFIG)

    app = create_application()
    reader = FBGStreamReader(
        cfg.interrogator,
        history_seconds=cfg.plot.history_seconds,
    )
    reader.start()

    window = LivePlotWindow(
        reader=reader,
        plot_cfg=cfg.plot,
        interr_cfg=cfg.interrogator,
        recording_cfg=cfg.recording,
    )
    window.show()

    if not reader.wait_until_ready(timeout=wait_ready_timeout):
        print(
            "[FBG] Warning: interrogator connection not confirmed before timeout.",
            file=sys.stderr,
        )

    exit_code = app.exec_()
    reader.stop()
    sys.exit(exit_code)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live plot and record FBG data.")
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to YAML configuration file. Uses built-in defaults when omitted.",
    )
    parser.add_argument(
        "--wait-ready-timeout",
        type=float,
        default=5.0,
        help="Seconds to wait for the interrogator connection before continuing.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config) if args.config else DEFAULT_CONFIG
    run_live_plot(cfg, wait_ready_timeout=args.wait_ready_timeout)


if __name__ == "__main__":
    main()
