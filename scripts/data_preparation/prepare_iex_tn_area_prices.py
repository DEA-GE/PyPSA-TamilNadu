"""Merge the IEX Tamil Nadu area-price exports (``IEX Price*.csv``) into one hourly FY2025-26 file.

    python scripts/data_preparation/prepare_iex_tn_area_prices.py --import-from <folder> [--market gdam]

Each hour is the mean of its four 15-minute blocks. Writes
data/source/iex_dam_fy2025_26/iex_<market>_tamil_nadu_hourly_fy2025_26.csv
(``timestamp`` = start of the hour, IST) and reports missing days per month
and missing hours within days that are otherwise present.

The exports name neither the market nor the bid area, so keep each market's
exports in their own folder: a block that appears in two exports with
different values stops the merge.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = {market: ROOT / "data" / "source" / "iex_dam_fy2025_26" / f"iex_{market}_tamil_nadu_hourly_fy2025_26.csv"
           for market in ("dam", "gdam")}
FY_START, FY_END = pd.Timestamp("2025-04-01"), pd.Timestamp("2026-04-01")
COLUMNS = {"Cleared Buy (MW)": "cleared_buy_mw", "Cleared Sell (MW)": "cleared_sell_mw",
           "Price (Rs./MWh)": "price_inr_per_mwh"}


def merge(folder: Path) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """Hourly FY2025-26 values from all exports in ``folder``, and the hours with no data."""
    raw = pd.concat(pd.read_csv(path) for path in sorted(folder.glob("IEX Price*.csv"))).drop_duplicates()
    conflicts = raw[raw.duplicated(["Delivery Date", "Time Period"], keep=False)]
    if not conflicts.empty:
        first = conflicts.iloc[0]
        raise ValueError(f"{conflicts['Delivery Date'].nunique()} days have blocks with different values in two "
                         f"exports (first: {first['Delivery Date']} {first['Time Period']}); "
                         "are exports of two markets in the same folder?")
    hour = pd.to_datetime(raw["Delivery Date"], format="%d/%m/%Y") + pd.to_timedelta(raw["Time Period"].str[:2].astype(int), unit="h")
    table = raw[list(COLUMNS)].rename(columns=COLUMNS).groupby(hour.rename("timestamp")).mean()
    table = table.loc[FY_START: FY_END - pd.Timedelta(hours=1)]
    hours = pd.date_range(FY_START, FY_END, freq="h", inclusive="left")
    return table, hours.difference(table.index)


def describe_gaps(missing: pd.DatetimeIndex) -> list[str]:
    """Missing whole days grouped by month, then days with only some hours missing."""
    per_day = pd.Series(1, index=missing).groupby(missing.normalize()).size()
    whole = per_day[per_day == 24]
    lines = [f"{month.strftime('%b %Y')}: {len(days)} days missing ({', '.join(f'{d:%d}' for d in days.index)})"
             for month, days in whole.groupby(whole.index.to_period("M"))]
    lines += [f"{day:%d %b %Y}: {n} of 24 hours missing" for day, n in per_day[per_day < 24].items()]
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--import-from", type=Path, required=True, help="folder with the IEX Price CSV exports")
    parser.add_argument("--market", choices=sorted(OUTPUTS), default="dam",
                        help="dam: day-ahead market; gdam: green day-ahead market (sets the default output)")
    parser.add_argument("--output", type=Path, help="overrides the market's default output file")
    args = parser.parse_args()
    output = args.output or OUTPUTS[args.market]

    table, missing = merge(args.import_from)
    table.to_csv(output, date_format="%Y-%m-%d %H:%M", float_format="%.2f", lineterminator="\n")
    print(f"Wrote {output.name}: {len(table)} of {len(table) + len(missing)} hours")
    for line in describe_gaps(missing):
        print(f"  Missing: {line}")


if __name__ == "__main__":
    main()
