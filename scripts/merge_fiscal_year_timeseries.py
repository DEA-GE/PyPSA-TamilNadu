"""Merge two calendar-year time series into one April-March fiscal year."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


TIMESTAMP_COLUMN = "timestamp"
FISCAL_YEAR_START_MONTH = 4


def read_timeseries(path: Path) -> tuple[int, pd.DataFrame]:
    if path.suffix.casefold() == ".csv":
        frame = pd.read_csv(path)
    elif path.suffix.casefold() in {".xlsx", ".xls"}:
        frame = pd.read_excel(path)
    else:
        raise ValueError(f"Unsupported file type: {path.suffix}")

    if TIMESTAMP_COLUMN not in frame.columns:
        raise ValueError(f"{path.name} must contain a {TIMESTAMP_COLUMN!r} column")

    frame[TIMESTAMP_COLUMN] = pd.to_datetime(frame[TIMESTAMP_COLUMN], errors="raise")
    years = frame[TIMESTAMP_COLUMN].dt.year.unique()
    if len(years) != 1:
        raise ValueError(f"{path.name} must contain exactly one calendar year")
    return int(years[0]), frame


def merge_fiscal_year(
    first_path: Path,
    second_path: Path,
    output_path: Path | None = None,
) -> tuple[Path, pd.DataFrame]:
    first_year, first = read_timeseries(first_path)
    second_year, second = read_timeseries(second_path)

    start_year, end_year = sorted((first_year, second_year))
    if end_year != start_year + 1:
        raise ValueError(
            f"Inputs must represent consecutive years, got {first_year} and {second_year}"
        )
    if list(first.columns) != list(second.columns):
        raise ValueError("Input files must have identical columns")

    earlier, later = (first, second) if first_year == start_year else (second, first)
    fiscal_start = pd.Timestamp(start_year, FISCAL_YEAR_START_MONTH, 1)
    fiscal_end = pd.Timestamp(end_year, FISCAL_YEAR_START_MONTH, 1)

    combined = pd.concat([earlier, later], ignore_index=True)
    fiscal = combined.loc[
        combined[TIMESTAMP_COLUMN].ge(fiscal_start)
        & combined[TIMESTAMP_COLUMN].lt(fiscal_end)
    ].copy()
    fiscal.sort_values(TIMESTAMP_COLUMN, inplace=True)
    fiscal.insert(1, "fiscal_year", f"FY{start_year}-{end_year}")
    fiscal.reset_index(drop=True, inplace=True)

    if fiscal.empty:
        raise ValueError(f"No data found for FY{start_year}-{end_year}")

    destination = output_path or first_path.parent / (
        f"FY{start_year}-{end_year}_timeseries.csv"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    fiscal.to_csv(destination, index=False)
    return destination, fiscal


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge two timestamped calendar-year files into an April-March fiscal year."
    )
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    destination, fiscal = merge_fiscal_year(args.first, args.second, args.output)
    print(f"Wrote {destination}")
    print(f"Fiscal year: {fiscal.fiscal_year.iat[0]}")
    print(f"Rows: {len(fiscal):,}")
    print(f"Timespan: {fiscal.timestamp.min()} to {fiscal.timestamp.max()}")


if __name__ == "__main__":
    main()
