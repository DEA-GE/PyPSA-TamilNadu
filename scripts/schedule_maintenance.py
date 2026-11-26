"""Schedule coal unit maintenance with PyPSA once, for runs to share.

    python scripts/schedule_maintenance.py config/scenarios/baseline.yaml schedule.csv
    python scripts/schedule_maintenance.py <config> <output> --set outages.base_availability=gross

Uses the config's ``outages`` settings with ``planned: optimise`` on the base
network (``paths.network``) *without* the config's scenario technologies, so
every scenario with the same outage settings gets the same maintenance dates.
The output has the outage-record format; pass it to runs as
``outages.maintenance_schedule``. The Snakemake workflow does this for every
scenario with ``outages.planned: optimise``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402
import pypsa  # noqa: E402

from generator_outages import apply_outages  # noqa: E402
from run_config import ConfigError, load_run_config, resolve_path  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("config", type=Path, help="Run or scenario config")
    parser.add_argument("output", type=Path, help="CSV to write the schedule to")
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="SECTION.KEY=VALUE")
    parser.add_argument("--threads", type=int, default=None, help="Solver threads")
    args = parser.parse_args(argv)
    try:
        # The schedule ignores scenario technologies (and their run-mode rules).
        config = load_run_config(args.config, args.overrides, ignore_sections=("technologies",))
    except ConfigError as error:
        print(f"Config error: {error}", file=sys.stderr)
        return 1
    outages = dict(config["outages"])
    if not outages["enabled"] or outages["planned"] != "optimise":
        print("Config has no outages.planned: optimise; nothing to schedule.", file=sys.stderr)
        return 1
    outages["maintenance_schedule"] = None
    network = pypsa.Network(resolve_path(config["paths"]["network"]))
    result = apply_outages(network, outages, solver_name=config["solver"]["name"], threads=args.threads)
    schedule = result.maintenance_schedule.copy()
    schedule["date"] = pd.to_datetime(schedule.date).dt.strftime("%Y-%m-%d")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    schedule.to_csv(args.output, index=False)
    print(f"Wrote {args.output}: {schedule.outage_days.sum():,.0f} maintenance days for {schedule.component.nunique()} units")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
