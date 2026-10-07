"""Prepare the FY2025-26 nine-area wind and solar workbook for the model.

The source workbook begins at 2025-04-01 05:00 and ends at 2026-04-01 04:00.
Its final five rows correspond to the model's first five hours. Validate that
alignment explicitly before writing the prepared CSV.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/source/wind_solar_profiles_TN-9BAs-2025-26-1h.xlsx"
SNAPSHOTS = ROOT / "data/build_template/snapshots.csv"
OUTPUT = ROOT / "data/model_inputs/renewable_profiles.csv"
AREAS = (
    "Chennai North",
    "Chennai South",
    "Coimbatore",
    "Erode",
    "Madurai",
    "Tirunelveli",
    "Trichy",
    "Vellore",
    "Villupuram",
)
WRAP_HOURS = 5


def model_snapshots() -> pd.DatetimeIndex:
    snapshots = pd.DatetimeIndex(
        pd.to_datetime(pd.read_csv(SNAPSHOTS)["snapshot"], errors="raise")
    )
    expected = pd.date_range("2025-04-01", periods=8760, freq="h")
    if not snapshots.equals(expected):
        raise ValueError(f"{SNAPSHOTS} does not contain the expected FY2025-26 hours")
    return snapshots


def read_sheet(sheet: str, snapshots: pd.DatetimeIndex) -> pd.DataFrame:
    frame = pd.read_excel(SOURCE, sheet_name=sheet, engine="openpyxl")
    source_columns = [f"{area}0 0" for area in AREAS]
    if list(frame.columns) != ["timestamp", *source_columns]:
        raise ValueError(f"Unexpected columns in {sheet} sheet: {list(frame.columns)}")

    source_hours = pd.DatetimeIndex(pd.to_datetime(frame["timestamp"], errors="raise"))
    expected_hours = snapshots + pd.Timedelta(hours=WRAP_HOURS)
    if not source_hours.equals(expected_hours):
        raise ValueError(f"Unexpected hourly timestamp range in {sheet} sheet")

    values = frame[source_columns].apply(pd.to_numeric, errors="raise")
    if values.isna().any().any() or ((values < 0) | (values > 1)).any().any():
        raise ValueError(f"{sheet} capacity factors must be present and between 0 and 1")

    aligned = pd.concat([values.tail(WRAP_HOURS), values.iloc[:-WRAP_HOURS]], ignore_index=True)
    aligned.columns = [f"{area.lower().replace(' ', '_')}_{sheet.lower()}" for area in AREAS]
    return aligned


def prepare() -> pd.DataFrame:
    snapshots = model_snapshots()
    wind = read_sheet("Wind", snapshots)
    solar = read_sheet("Solar", snapshots)
    prepared = pd.concat([wind, solar], axis=1)
    prepared.insert(0, "snapshot", snapshots.strftime("%Y-%m-%d %H:%M:%S"))
    return prepared


def check(prepared: pd.DataFrame, output: Path) -> None:
    if not output.exists():
        raise FileNotFoundError(f"Prepared profile file does not exist: {output}")
    existing = pd.read_csv(output)
    if list(existing.columns) != list(prepared.columns):
        raise ValueError(f"Prepared profile columns differ from {output}")
    if existing["snapshot"].tolist() != prepared["snapshot"].tolist():
        raise ValueError(f"Prepared profile timestamps differ from {output}")
    if not np.allclose(
        existing.iloc[:, 1:].to_numpy(dtype=float),
        prepared.iloc[:, 1:].to_numpy(dtype=float),
        rtol=0,
        atol=1e-12,
        equal_nan=False,
    ):
        raise ValueError(f"Prepared profile values differ from {output}")
    print(f"Verified {len(prepared):,} hourly rows against {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "--check", action="store_true", help="Compare with the existing CSV without writing"
    )
    args = parser.parse_args()

    prepared = prepare()
    if args.check:
        check(prepared, args.output)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        prepared.to_csv(args.output, index=False)
        print(f"Wrote {len(prepared):,} hourly rows to {args.output}")


if __name__ == "__main__":
    main()
