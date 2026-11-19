"""Run the 9-balancing-area model from a YAML config, then build the results report.

    python scripts/run_model.py config/runs/daily_demo.yaml
    python scripts/run_model.py config/runs/rolling_relaxed.yaml --set run.name=relaxed_test
    python scripts/run_model.py config/runs/rolling_relaxed.yaml --dry-run

The run config is merged on top of ``config/defaults.yaml``. After the solve,
the resolved config is saved as ``run_config.yaml`` in the run folder (it can be
passed back to this script to repeat the run), and, unless disabled,
``build_results_report.py`` writes ``results_report.html`` there.

Exit codes: 0 success; 1 invalid config or failed run; 2 run succeeded but the
results analysis failed (the run outputs are kept).
"""

from __future__ import annotations

import argparse
import importlib.metadata
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from run_config import (  # noqa: E402
    ConfigError,
    dump_config,
    load_run_config,
    resolve_path,
    run_selection_kwargs,
)


class _Tee:
    """Copy everything written to a stream into a log file as well."""

    def __init__(self, stream: TextIO, log: TextIO) -> None:
        self._stream = stream
        self._log = log

    def write(self, text: str) -> int:
        self._stream.write(text)
        self._log.write(text)
        return len(text)

    def flush(self) -> None:
        self._stream.flush()
        self._log.flush()

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


def _start_console_log(config: dict) -> tuple[Path, TextIO] | None:
    if not config["logging"]["console_log"]:
        return None
    log_dir = resolve_path(config["paths"]["results_root"]) / "console_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = log_dir / f"{config['run']['name']}_{stamp}.log"
    log = path.open("w", encoding="utf-8", buffering=1)
    sys.stdout = _Tee(sys.__stdout__, log)
    sys.stderr = _Tee(sys.__stderr__, log)
    return path, log


def _stop_console_log(handle: tuple[Path, TextIO] | None) -> None:
    if handle is None:
        return
    sys.stdout.flush()
    sys.stderr.flush()
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
    handle[1].close()


def _save_resolved_config(
    output_dir: Path, config: dict, source: Path | None, overrides: list[str], started: datetime
) -> str:
    finished = datetime.now(timezone.utc)
    header = [
        "# Resolved configuration of this run, written by scripts/run_model.py.",
        "# Repeat the run with: python scripts/run_model.py <this file>",
        f"# Source config: {source if source is not None else '(defaults only)'}",
        f"# Overrides: {' '.join(overrides) if overrides else '(none)'}",
        f"# Started:  {started:%Y-%m-%d %H:%M:%S} UTC",
        f"# Finished: {finished:%Y-%m-%d %H:%M:%S} UTC",
        "",
    ]
    text = "\n".join(header) + dump_config(config)
    (output_dir / "run_config.yaml").write_text(text, encoding="utf-8")
    return text


def _print_environment(config: dict) -> None:
    print("Python:", sys.executable)
    for package in ("pypsa", "linopy", "highspy"):
        try:
            print(f"{package}:", importlib.metadata.version(package))
        except importlib.metadata.PackageNotFoundError:
            print(f"{package}: not installed")
    print("Resolved configuration:")
    print(dump_config(config), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the 9-BA model from a YAML config and build the results report."
    )
    parser.add_argument(
        "config",
        type=Path,
        nargs="?",
        help="Run config (YAML) merged on top of config/defaults.yaml",
    )
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="SECTION.KEY=VALUE",
        help="Override one setting, e.g. --set run.name=test --set solver.threads=4",
    )
    parser.add_argument(
        "--profile",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Turn detailed logging and profiling (profile/ folder with run.log and HiGHS "
        "logs) on or off for this run; overrides logging.profile",
    )
    parser.add_argument(
        "--no-analysis", action="store_true", help="Skip the results analysis and report"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate and print the resolved config only"
    )
    args = parser.parse_args(argv)
    if args.profile is not None:
        # Applied last, so the flag wins over the config file and --set.
        args.overrides.append(f"logging.profile={'true' if args.profile else 'false'}")

    try:
        config = load_run_config(args.config, args.overrides)
    except ConfigError as error:
        print(f"Config error: {error}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(dump_config(config), end="")
        return 0

    started = datetime.now(timezone.utc)
    log_handle = _start_console_log(config)
    try:
        if log_handle is not None:
            print(f"Console log: {log_handle[0]}")
        print(
            "Detailed logging and profiling: "
            + (f"on (level {config['logging']['level']})" if config["logging"]["profile"] else "off")
        )
        _print_environment(config)

        import run_9ba_selection

        run_started = time.perf_counter()
        try:
            result = run_9ba_selection.run_selection(**run_selection_kwargs(config))
        except Exception:
            traceback.print_exc()
            print("Run failed; no results report was built.", file=sys.stderr, flush=True)
            return 1
        output_dir = Path(result["output_dir"]).resolve()
        print(
            f"Run finished in {(time.perf_counter() - run_started) / 60.0:,.1f} min: {output_dir}",
            flush=True,
        )
        config_text = _save_resolved_config(
            output_dir, config, args.config, args.overrides, started
        )

        if args.no_analysis or not config["analysis"]["enabled"]:
            print("Results analysis disabled.")
            return 0

        from build_results_report import build_report

        try:
            report = build_report(
                output_dir,
                compare_observed=config["analysis"]["compare_observed"],
                config_text=config_text,
            )
        except Exception:
            traceback.print_exc()
            print(
                "Results analysis failed; run outputs are kept. Retry with:\n"
                f'  python scripts/build_results_report.py "{output_dir}"',
                file=sys.stderr,
                flush=True,
            )
            return 2
        print(f"Open the report: {report.as_uri()}", flush=True)
        return 0
    finally:
        _stop_console_log(log_handle)


if __name__ == "__main__":
    raise SystemExit(main())
