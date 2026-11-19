"""Summarize or compare the run profiles written by ``run_selection``.

Usage (from the repository root)::

    python scripts/summarize_run_profile.py results/run_results/<label>/<run_name>
    python scripts/summarize_run_profile.py <run A> <run B> [...]

A path may be a run folder or its ``profile/`` folder. One path prints that
run's summary (and rewrites ``summary.json``/``summary.txt``). Several paths
print a side-by-side comparison of the solve-time breakdown and the main
phases, which is the quickest way to measure an efficiency change.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_profiling import PROFILE_DIRNAME, format_summary, write_summary  # noqa: E402


def _profile_dir(path: Path) -> Path:
    if (path / "events.jsonl").exists() or path.name == PROFILE_DIRNAME:
        return path
    if (path / PROFILE_DIRNAME).is_dir():
        return path / PROFILE_DIRNAME
    raise SystemExit(f"No run profile found in {path}")


def _compare(summaries: list[dict], names: list[str], phases: int) -> str:
    width = max(14, *(len(name) for name in names))
    lines = []

    def row(label: str, values: list[object], fmt: str = "{:>{w}.1f}") -> None:
        cells = []
        for value in values:
            cells.append(f"{'-':>{width}}" if value is None else fmt.format(value, w=width))
        lines.append(f"{label:<44}" + " ".join(cells))

    lines.append(f"{'':<44}" + " ".join(f"{name[-width:]:>{width}}" for name in names))
    row("run wall s", [summary.get("run", {}).get("wall_s") for summary in summaries])
    row("run CPU s", [summary.get("run", {}).get("cpu_s") for summary in summaries])
    row("optimize wall s", [(summary.get("solves") or {}).get("optimize_wall_s") for summary in summaries])
    row(
        "solver attempts",
        [(summary.get("solves") or {}).get("attempts") for summary in summaries],
        "{:>{w}d}",
    )
    keys = sorted({
        key for summary in summaries for key in (summary.get("solves") or {}).get("breakdown_s", {})
    })
    for key in keys:
        row(
            f"  {key[:-2]} s",
            [(summary.get("solves") or {}).get("breakdown_s", {}).get(key) for summary in summaries],
        )
    ranked: dict[str, float] = {}
    for summary in summaries:
        for phase in summary.get("phases", []):
            ranked[phase["path"]] = max(ranked.get(phase["path"], 0.0), phase["total_wall_s"])
    lines.append("")
    lines.append("Phase totals (s):")
    for path in sorted(ranked, key=ranked.get, reverse=True)[:phases]:
        values = []
        for summary in summaries:
            match = next((phase for phase in summary.get("phases", []) if phase["path"] == path), None)
            values.append(None if match is None else match["total_wall_s"])
        row(f"  {path[-42:]}", values)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", type=Path, help="run folders or their profile/ folders")
    parser.add_argument("--phases", type=int, default=25, help="number of phases to show")
    args = parser.parse_args(argv)
    profile_dirs = [_profile_dir(path) for path in args.runs]
    summaries = [write_summary(path) for path in profile_dirs]
    if len(summaries) == 1:
        print(format_summary(summaries[0], top_phases=args.phases))
        return
    names = [path.parent.name for path in profile_dirs]
    print(_compare(summaries, names, args.phases))


if __name__ == "__main__":
    main()
