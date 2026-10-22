"""Run the twelve monthly audits in parallel, then merge their outputs."""

from __future__ import annotations

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .mappings import MONTH_ARCHIVES
from .merge_outputs import merge


def _run_month(month: str, source: Path, parts: Path, gpkg: Path) -> tuple[str, int, str]:
    destination = parts / month
    destination.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "scripts.rea_fy2025_26.pipeline",
        "--source-dir", str(source), "--output-dir", str(destination),
        "--gpkg", str(gpkg), "--months", month,
    ]
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (destination / "run.log").write_text(completed.stdout, encoding="utf-8")
    return month, completed.returncode, completed.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path("data/source/REA data"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/rea_fy2025_26"))
    parser.add_argument("--gpkg", type=Path)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--months", nargs="*", help="Optional YYYY-MM subset to regenerate before merging all available parts")
    args = parser.parse_args()
    gpkg = args.gpkg or args.source_dir / "TamilNadu_9BAs.gpkg"
    parts = args.output_dir / "_parts"
    parts.mkdir(parents=True, exist_ok=True)
    failures = []
    selected = set(args.months) if args.months else {month for month, _ in MONTH_ARCHIVES}
    unknown = selected - {month for month, _ in MONTH_ARCHIVES}
    if unknown:
        raise SystemExit(f"Unknown months: {', '.join(sorted(unknown))}")
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(_run_month, month, args.source_dir, parts, gpkg): month
            for month, _ in MONTH_ARCHIVES if month in selected
        }
        for future in as_completed(futures):
            month, code, log = future.result()
            tail = "\n".join(log.splitlines()[-3:])
            print(f"[{month}] exit={code}\n{tail}", flush=True)
            if code:
                failures.append(month)
    if failures:
        raise SystemExit(f"Monthly audits failed: {', '.join(sorted(failures))}")
    merge(parts, args.output_dir)
    print(f"Annual audit complete: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
