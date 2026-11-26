"""Merge IEX Day-Ahead Market "Market Snapshot" exports into one FY2025-26 table.

IEX's website (Market Data -> Day-Ahead Market -> Market Snapshot, hourly
interval) exports one Excel file per selected period, at most about a month at
a time. This script reads every ``*.xlsx`` export in a folder, keeps the hourly
rows, skips exact duplicate exports, refuses conflicting ones, and writes the
complete fiscal year (1 April 2025 to 31 March 2026, 8,760 hours) to

    data/source/iex_dam_fy2025_26/iex_dam_hourly_fy2025_26.csv

with the columns

    timestamp, date, hour, purchase_bid_mwh, sell_bid_mwh, mcv_mwh,
    final_scheduled_volume_mwh, mcp_inr_per_mwh, weighted_mcp_inr_per_mwh

``hour`` is IEX's hour number (1 = 00:00-01:00 IST); ``timestamp`` is the start
of that hour in IST, matching the model's hourly snapshots. Exports covering
other periods are reported and ignored. Each day's hourly MCP is checked
against the export's own daily "Avg" summary row.

    python scripts/data_preparation/prepare_iex_dam_prices.py --import-from <folder>
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "data" / "source" / "iex_dam_fy2025_26" / "iex_dam_hourly_fy2025_26.csv"
FY_START, FY_END = pd.Timestamp("2025-04-01"), pd.Timestamp("2026-04-01")

COLUMNS = {
    "Purchase Bid (MWh)": "purchase_bid_mwh",
    "Sell Bid (MWh)": "sell_bid_mwh",
    "MCV (MWh)": "mcv_mwh",
    "Final Scheduled Volume (MWh)": "final_scheduled_volume_mwh",
    "MCP (Rs/MWh) *": "mcp_inr_per_mwh",
    "Weighted MCP (Rs/MWh)": "weighted_mcp_inr_per_mwh",
}


def is_snapshot(raw: pd.DataFrame) -> bool:
    """Whether a sheet is an IEX Market Snapshot export (titled in column A)."""
    return raw[0].head(5).eq("Market Snapshot").any()


def period(raw: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    """First and last day named in the export's ``Date: d1 [to d2]`` line."""
    line = raw[0].head(5).dropna()
    line = line.loc[line.str.startswith("Date:")]
    if line.empty:
        raise ValueError("no 'Date:' line")
    parts = [part.strip() for part in line.iloc[0].removeprefix("Date:").split("to")]
    days = pd.to_datetime(parts, format="%d-%m-%Y")
    return days.min(), days.max()


def read_export(path: Path, raw: pd.DataFrame | None = None) -> pd.DataFrame:
    """Hourly rows of one Market Snapshot export, indexed by IST hour start."""
    if raw is None:
        raw = pd.read_excel(path, header=None, dtype=str)
    header_rows = raw.index[raw[0].eq("Date") & raw[1].eq("Hour")]
    if len(header_rows) != 1:
        raise ValueError(f"{path.name}: no unique 'Date | Hour' header row")
    header = raw.loc[header_rows[0]].tolist()
    missing = {"Date", "Hour", *COLUMNS} - set(header)
    if missing:
        raise ValueError(f"{path.name}: missing columns {sorted(missing)}")
    body = raw.loc[header_rows[0] + 1 :].set_axis(header, axis=1)

    hourly = body.loc[body["Hour"].fillna("").str.fullmatch(r"\d{1,2}")]
    frame = hourly[list(COLUMNS)].astype(float).rename(columns=COLUMNS)
    date = pd.to_datetime(hourly["Date"], format="%d-%m-%Y")
    hour = hourly["Hour"].astype(int)
    if not hour.between(1, 24).all():
        raise ValueError(f"{path.name}: hour numbers outside 1-24")
    frame.insert(0, "hour", hour.to_numpy())
    frame.insert(0, "date", date.dt.date.to_numpy())
    frame.index = pd.DatetimeIndex(date + pd.to_timedelta(hour - 1, unit="h"), name="timestamp")
    if frame.index.duplicated().any():
        raise ValueError(f"{path.name}: repeated hours (not an hourly export?)")
    days = frame.groupby("date").size()
    if not days.eq(24).all():
        raise ValueError(f"{path.name}: incomplete days {list(days.index[days.ne(24)])}")

    averages = body.loc[body["Hour"].eq("Avg")]
    expected = pd.Series(averages["MCP (Rs/MWh) *"].astype(float).to_numpy(),
                         index=pd.to_datetime(averages["Date"], format="%d-%m-%Y").dt.date)
    actual = frame.groupby("date")["mcp_inr_per_mwh"].mean()
    gap = (actual - expected.reindex(actual.index)).abs()
    if expected.empty or gap.isna().any() or (gap > 0.01).any():
        raise ValueError(f"{path.name}: hourly MCP does not match the daily 'Avg' rows")
    return frame.sort_index()


def merge(folder: Path) -> pd.DataFrame:
    """Combine all exports in ``folder``; return the FY2025-26 hourly table."""
    parts: list[pd.DataFrame] = []
    for path in sorted(folder.glob("*.xlsx")):
        raw = pd.read_excel(path, header=None, dtype=str)
        if raw.empty or not is_snapshot(raw):
            continue  # other downloads in the same folder
        start, end = period(raw)
        if end < FY_START or start >= FY_END:
            print(f"Ignoring {path.name}: {start:%d %b %Y} to {end:%d %b %Y} is outside FY2025-26")
            continue
        frame = read_export(path, raw)
        inside = frame.loc[(frame.index >= FY_START) & (frame.index < FY_END)]
        overlap = [p for p in parts if p.index.intersection(inside.index).size]
        if overlap:
            common = overlap[0].index.intersection(inside.index)
            if len(common) == len(inside) and overlap[0].loc[common].equals(inside.loc[common]):
                print(f"Skipping {path.name}: duplicate of an export already read")
                continue
            raise ValueError(f"{path.name}: overlaps another export with different data")
        parts.append(inside)
        print(f"{path.name}: {start:%d %b %Y} to {end:%d %b %Y} ({len(inside)} hours)")
    if not parts:
        raise ValueError(f"No FY2025-26 exports in {folder}")
    table = pd.concat(parts).sort_index()
    expected = pd.date_range(FY_START, FY_END, freq="h", inclusive="left", name="timestamp")
    missing = expected.difference(table.index)
    if len(missing):
        months = sorted({f"{t:%b %Y}" for t in missing}, key=lambda m: pd.Timestamp(m))
        raise ValueError(f"FY2025-26 incomplete: {len(missing)} hours missing ({', '.join(months)})")
    return table


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--import-from", type=Path, required=True, help="folder with the IEX .xlsx exports")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    table = merge(args.import_from)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.output, date_format="%Y-%m-%d %H:%M", float_format="%.2f", lineterminator="\n")
    mcp = table["mcp_inr_per_mwh"]
    print(f"Wrote {args.output.relative_to(ROOT) if args.output.is_relative_to(ROOT) else args.output}: "
          f"{len(table)} hours, mean MCP {mcp.mean():.0f} INR/MWh, "
          f"volume-weighted {(mcp * table['mcv_mwh']).sum() / table['mcv_mwh'].sum():.0f}")


if __name__ == "__main__":
    main()
