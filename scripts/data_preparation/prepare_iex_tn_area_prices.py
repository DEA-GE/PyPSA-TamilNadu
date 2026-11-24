"""Merge IEX Day-Ahead Market area-price exports for Tamil Nadu into one FY2025-26 table.

The exports are the CSV files the IEX website saves as ``IEX Price*.csv``:
15-minute blocks with the columns

    "Delivery Date","Time Period","Cleared Buy (MW)","Cleared Sell (MW)","Price (Rs./MWh)"

The script reads every such CSV in a folder, skips days already read from
another export (for example a partial month re-exported in full), refuses
overlapping exports whose data differ, keeps 1 April 2025 to 31 March 2026,
and writes

    data/source/iex_dam_fy2025_26/iex_dam_tamil_nadu_15min_fy2025_26.csv

with the columns

    block_start, date, block, cleared_buy_mw, cleared_sell_mw, price_inr_per_mwh

``block_start`` is the start of the 15-minute block in IST and ``block`` its
number in the day (1 = 00:00-00:15). Missing days are listed; with
``--require-complete`` they are an error.

    python scripts/data_preparation/prepare_iex_tn_area_prices.py --import-from <folder>
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "data" / "source" / "iex_dam_fy2025_26" / "iex_dam_tamil_nadu_15min_fy2025_26.csv"
FY_START, FY_END = pd.Timestamp("2025-04-01"), pd.Timestamp("2026-04-01")

COLUMNS = {
    "Cleared Buy (MW)": "cleared_buy_mw",
    "Cleared Sell (MW)": "cleared_sell_mw",
    "Price (Rs./MWh)": "price_inr_per_mwh",
}
REQUIRED = ["Delivery Date", "Time Period", *COLUMNS]


def read_export(path: Path) -> pd.DataFrame:
    """15-minute rows of one area-price export, indexed by IST block start."""
    raw = pd.read_csv(path, dtype=str)
    missing = set(REQUIRED) - set(raw.columns)
    if missing:
        raise ValueError(f"{path.name}: missing columns {sorted(missing)}")
    date = pd.to_datetime(raw["Delivery Date"], format="%d/%m/%Y")
    period = raw["Time Period"].str.extract(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$").astype(float)
    if period.isna().any().any():
        raise ValueError(f"{path.name}: unexpected 'Time Period' values")
    start_min = period[0] * 60 + period[1]
    end_min = period[2] * 60 + period[3]
    if not (end_min - start_min).eq(15).all():
        raise ValueError(f"{path.name}: blocks are not 15 minutes long")

    frame = raw[list(COLUMNS)].astype(float).rename(columns=COLUMNS)
    frame.insert(0, "block", (start_min // 15 + 1).astype(int).to_numpy())
    frame.insert(0, "date", date.dt.date.to_numpy())
    frame.index = pd.DatetimeIndex(date + pd.to_timedelta(start_min, unit="min"), name="block_start")
    if frame.index.duplicated().any():
        raise ValueError(f"{path.name}: repeated blocks")
    blocks = frame.groupby("date").size()
    if not blocks.eq(96).all():
        raise ValueError(f"{path.name}: incomplete days {list(blocks.index[blocks.ne(96)])}")
    return frame.sort_index()


def is_area_export(path: Path) -> bool:
    header = pd.read_csv(path, nrows=0).columns.tolist()
    return header[: len(REQUIRED)] == REQUIRED


def merge(folder: Path) -> tuple[pd.DataFrame, list[pd.Timestamp]]:
    """FY2025-26 rows from all exports in ``folder`` and the list of missing days."""
    parts: list[pd.DataFrame] = []
    for path in sorted(folder.glob("*.csv")):
        if not is_area_export(path):
            continue
        frame = read_export(path)
        start, end = frame.index.min(), frame.index.max()
        inside = frame.loc[(frame.index >= FY_START) & (frame.index < FY_END)]
        if inside.empty:
            print(f"Ignoring {path.name}: {start:%d %b %Y} to {end:%d %b %Y} is outside FY2025-26")
            continue
        new = inside
        for part in parts:
            common = part.index.intersection(new.index)
            if len(common) and not part.loc[common].equals(new.loc[common]):
                raise ValueError(f"{path.name}: overlaps another export with different data")
            new = new.drop(common)
        if new.empty:
            print(f"Skipping {path.name}: all its days are already read with the same data")
            continue
        parts.append(new)
        note = "" if len(new) == len(inside) else f", {len(new) // 96} new"
        print(f"{path.name}: {start:%d %b %Y} to {end:%d %b %Y} ({len(inside) // 96} days{note})")
    if not parts:
        raise ValueError(f"No FY2025-26 area-price exports in {folder}")
    table = pd.concat(parts).sort_index()
    days = pd.date_range(FY_START, FY_END, freq="D", inclusive="left")
    missing = list(days.difference(pd.DatetimeIndex(pd.to_datetime(table["date"].unique()))))
    return table, missing


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--import-from", type=Path, required=True, help="folder with the IEX Price CSV exports")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--require-complete", action="store_true", help="fail if any FY2025-26 day is missing")
    args = parser.parse_args()

    table, missing = merge(args.import_from)
    if missing:
        listing = ", ".join(f"{d:%d %b %Y}" for d in missing)
        message = f"{len(missing)} FY2025-26 days missing: {listing}"
        if args.require_complete:
            raise SystemExit(message)
        print(f"Warning: {message}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.output, date_format="%Y-%m-%d %H:%M", float_format="%.2f", lineterminator="\n")
    print(f"Wrote {args.output.name}: {len(table) // 96} days, {len(table)} blocks, "
          f"mean price {table['price_inr_per_mwh'].mean():.0f} INR/MWh")


if __name__ == "__main__":
    main()
