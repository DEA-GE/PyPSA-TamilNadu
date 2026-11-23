"""Tidy the NITI Dashboard (ICED) monthly Tamil Nadu generation exports.

The raw exports (one per source) are kept in
``data/source/NITI dashboard/monthly plant generation Tamil Nadu/`` with the
dashboard's copyright/disclaimer footer removed. This script validates them and
writes one long table to
``data/model_inputs/niti_iced_tamil_nadu_monthly_generation.csv``:

    source, plant_name, sector, fiscal_year, month, generation_mu

``month`` is the first day of the calendar month. Sources reported only as a
state total (small hydro, bio-power) have an empty ``plant_name`` and ``sector``.

New downloads are taken in with ``--import-from <folder>``: each export is
identified by its ``Source`` column, its footer is removed, its data rows are
copied unchanged to ``<source>.csv`` in the raw folder, and exact duplicate
exports are skipped.

    python scripts/data_preparation/prepare_niti_iced_monthly_generation.py
    python scripts/data_preparation/prepare_niti_iced_monthly_generation.py --import-from <folder>
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "source" / "NITI dashboard" / "monthly plant generation Tamil Nadu"
OUTPUT = ROOT / "data" / "model_inputs" / "niti_iced_tamil_nadu_monthly_generation.csv"

VALUE_COLUMN = "Monthly Generation (In MU)"
MONTHS = (
    "April", "May", "June", "July", "August", "September",
    "October", "November", "December", "January", "February", "March",
)


def read_export(path: Path) -> pd.DataFrame:
    """Data rows of one ICED export (footer rows have no Tamil Nadu state)."""
    frame = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    required = {"Source", "State", "Year", "Month", VALUE_COLUMN}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{path.name}: missing columns {sorted(missing)}")
    return frame.loc[frame["State"].eq("Tamil Nadu")].reset_index(drop=True)


def import_exports(folder: Path, raw_dir: Path = RAW_DIR) -> list[Path]:
    """Copy each export's data rows to ``<source>.csv`` in ``raw_dir``."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, pd.DataFrame] = {}
    for path in sorted(folder.glob("*.csv")):
        frame = read_export(path)
        sources = frame["Source"].unique()
        if len(sources) != 1:
            raise ValueError(f"{path.name}: expected one source, found {list(sources)}")
        source = str(sources[0])
        if source in written:
            if written[source].equals(frame):
                print(f"Skipping {path.name}: duplicate of the {source} export")
                continue
            raise ValueError(f"Two different exports for source {source!r}")
        written[source] = frame
        target = raw_dir / f"{source}.csv"
        frame.to_csv(target, index=False, lineterminator="\n")
        print(f"{path.name} -> {target.relative_to(ROOT)} ({len(frame)} rows)")
    return [raw_dir / f"{source}.csv" for source in written]


def tidy(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    frames = []
    for path in sorted(raw_dir.glob("*.csv")):
        frame = read_export(path)
        if "Plant Name" not in frame:
            frame["Plant Name"] = ""
            frame["Sector"] = ""
        frames.append(frame)
    if not frames:
        raise FileNotFoundError(f"No exports in {raw_dir}")
    data = pd.concat(frames, ignore_index=True)
    unknown = set(data["Month"]) - set(MONTHS)
    if unknown:
        raise ValueError(f"Unknown month names: {sorted(unknown)}")
    values = pd.to_numeric(data[VALUE_COLUMN], errors="coerce")
    if values.isna().any() or (values < 0).any():
        raise ValueError("Generation values must be numeric and non-negative")
    start_year = data["Year"].str.slice(0, 4).astype(int)
    position = data["Month"].map({name: index for index, name in enumerate(MONTHS)})
    calendar_year = start_year + (position >= 9).astype(int)
    calendar_month = (position + 3) % 12 + 1
    tidy_frame = pd.DataFrame({
        "source": data["Source"],
        "plant_name": data["Plant Name"].fillna(""),
        "sector": data["Sector"].fillna(""),
        "fiscal_year": data["Year"],
        "month": pd.to_datetime(
            {"year": calendar_year, "month": calendar_month, "day": 1}
        ).dt.strftime("%Y-%m-%d"),
        "generation_mu": values,
    })
    keys = ["source", "plant_name", "month"]
    if tidy_frame.duplicated(keys).any():
        raise ValueError("Duplicate source/plant/month rows")
    return tidy_frame.sort_values(keys).reset_index(drop=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--import-from", type=Path, default=None,
                        help="Folder of new ICED Generation_Metatable_Data_*.csv exports")
    args = parser.parse_args(argv)
    if args.import_from is not None:
        import_exports(args.import_from)
    table = tidy()
    table.to_csv(OUTPUT, index=False, lineterminator="\n")
    summary = table.groupby("source").agg(
        plants=("plant_name", lambda names: names[names.ne("")].nunique()),
        first=("month", "min"), last=("month", "max"), rows=("month", "size"),
    )
    print(f"Wrote {OUTPUT.relative_to(ROOT)} ({len(table)} rows)")
    print(summary.to_string())


if __name__ == "__main__":
    main()
