"""Merge the IEX Tamil Nadu area-price exports (``IEX Price*.csv``) into one FY2025-26 file.

    python scripts/data_preparation/prepare_iex_tn_area_prices.py --import-from <folder>

Writes data/source/iex_dam_fy2025_26/iex_dam_tamil_nadu_15min_fy2025_26.csv
(15-minute blocks, ``block_start`` in IST) and lists any missing days.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "data" / "source" / "iex_dam_fy2025_26" / "iex_dam_tamil_nadu_15min_fy2025_26.csv"
FY_START, FY_END = pd.Timestamp("2025-04-01"), pd.Timestamp("2026-04-01")
COLUMNS = {"Cleared Buy (MW)": "cleared_buy_mw", "Cleared Sell (MW)": "cleared_sell_mw",
           "Price (Rs./MWh)": "price_inr_per_mwh"}


def merge(folder: Path) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """FY2025-26 blocks from all exports in ``folder``, and the days with no data."""
    raw = pd.concat(pd.read_csv(path) for path in sorted(folder.glob("IEX Price*.csv")))
    date = pd.to_datetime(raw["Delivery Date"], format="%d/%m/%Y")
    table = raw[list(COLUMNS)].rename(columns=COLUMNS)
    table.index = pd.DatetimeIndex(date + pd.to_timedelta(raw["Time Period"].str[:5] + ":00"), name="block_start")
    table = table[~table.index.duplicated()].sort_index().loc[FY_START: FY_END - pd.Timedelta(minutes=15)]
    table.insert(0, "block", table.index.hour * 4 + table.index.minute // 15 + 1)
    table.insert(0, "date", table.index.date)
    days = pd.date_range(FY_START, FY_END, freq="D", inclusive="left")
    return table, days.difference(table.index.normalize())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--import-from", type=Path, required=True, help="folder with the IEX Price CSV exports")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    table, missing = merge(args.import_from)
    table.to_csv(args.output, date_format="%Y-%m-%d %H:%M", float_format="%.2f", lineterminator="\n")
    print(f"Wrote {args.output.name}: {table['date'].nunique()} days, {len(table)} blocks")
    if len(missing):
        print(f"Missing {len(missing)} days: " + ", ".join(f"{d:%d %b %Y}" for d in missing))


if __name__ == "__main__":
    main()
