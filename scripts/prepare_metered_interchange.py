"""Hourly physical net import of the Tamil Nadu area from metered drawal, FY2025-26.

Tamil Nadu's control area draws from the interstate grid through metered
interface points; SRPC's deviation account
(``commercial_dev2022_tamilnadu.csv`` in each month's Regional Energy Account,
``act_total``, MWh per 15-minute block) gives the actual drawal. Plants inside
Tamil Nadu that are connected to the interstate grid inject into that grid,
not into the state's control area, so their output reaches the state as
drawal. The physical net import of the Tamil Nadu area, which the model's
interstate loads must deliver, is therefore

    actual drawal - output of the interstate-grid plants inside Tamil Nadu

with that output taken from the same accounts:

- central stations in Tamil Nadu (Vallur, NTPL, NNTPS, NLC TPS-II Stage I and
  II, TPS-I and TPS-II Expansion, Kudankulam 1 and 2, MAPS): station-wide
  schedules in ``commercial_isgs.csv``;
- Coastal Energen (Muthiara) and IL&FS (ITPCL): their schedules to Tamil Nadu
  in ``tneb_imp.csv``;
- interstate-grid wind and solar at Tuticorin, Karur and Pugalur: all their
  schedules in ``commercial_remc_schedule.csv``.

Writes ``data/model_inputs/tn_metered_interchange_FY2025_26.csv`` (hourly MW).
The archives are read from ``data/source/REA data`` (gitignored), located
with the REA pipeline's inventory.

    python scripts/prepare_metered_interchange.py [--source-dir "data/source/REA data"]
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from prepare_declared_capacity import HOURS, INVENTORY, SOURCE_DIR, read_archive_member  # noqa: E402

OUTPUT = ROOT / "data" / "model_inputs" / "tn_metered_interchange_FY2025_26.csv"
CENTRAL_STATIONS = [
    "vallur", "ntpl", "nntpp", "nlc21", "nlc22", "nlcexp", "nlc2exp", "kknpp", "kknppU2", "maps",
]
TN_IPPS = ["COASTGEN", "ILFS"]
# Interstate-grid wind and solar pooling stations inside Tamil Nadu.
TN_RE_PATTERN = r"(?i)^(tuticorin|karur|pglr|ttgs)_"
COLUMNS = [
    "actual_drawal_mw", "scheduled_drawal_mw", "central_stations_mw", "tn_ipps_mw",
    "interstate_re_mw", "net_import_mw",
]


def _blocks(frame: pd.DataFrame) -> pd.Series:
    """Start time of each 15-minute block (``time`` is HH:MM:SS or block 1-96)."""
    date = pd.to_datetime(frame["date"])
    time = frame["time"].astype(str)
    if time.str.contains(":").all():
        return date + pd.to_timedelta(time)
    return date + pd.to_timedelta((pd.to_numeric(time) - 1) * 15, unit="min")


def _hourly(values: pd.Series, stamps: pd.Series) -> pd.Series:
    """Hourly mean MW from 15-minute MW values."""
    return pd.Series(values.to_numpy(dtype=float), index=pd.DatetimeIndex(stamps)).groupby(
        pd.DatetimeIndex(stamps).floor("h")
    ).sum() / 4.0


def read_metered_interchange(source_dir: Path = SOURCE_DIR, inventory: Path = INVENTORY) -> pd.DataFrame:
    listing = pd.read_csv(inventory)
    final = listing[~listing.is_provisional]
    cache: dict = {}
    parts: dict[str, list[pd.Series]] = {c: [] for c in COLUMNS[:-1]}

    def members(name: str):
        rows = final[final.filename == name].sort_values("date_start")
        if len(rows) != 24:
            raise ValueError(f"Expected 24 final half-month {name} members, found {len(rows)}")
        for row in rows.itertuples():
            yield row, read_archive_member(source_dir, row.virtual_path, cache)

    for _, data in members("commercial_dev2022_tamilnadu.csv"):
        frame = pd.read_csv(io.BytesIO(data))
        stamps = _blocks(frame)
        # MWh per 15-minute block -> MW.
        parts["actual_drawal_mw"].append(_hourly(frame["act_total"] * 4.0, stamps))
        parts["scheduled_drawal_mw"].append(_hourly(frame["sch_total"] * 4.0, stamps))
    for _, data in members("commercial_isgs.csv"):
        frame = pd.read_csv(io.BytesIO(data))
        total = sum(pd.to_numeric(frame[f"{s}_sch"], errors="raise") for s in CENTRAL_STATIONS)
        parts["central_stations_mw"].append(_hourly(total, _blocks(frame)))
    for _, data in members("tneb_imp.csv"):
        frame = pd.read_csv(io.BytesIO(data), usecols=range(14), dtype=str)
        frame = frame[frame["fromembd"].isin(TN_IPPS)]
        mw = pd.to_numeric(frame["sch"], errors="coerce").fillna(0.0)
        parts["tn_ipps_mw"].append(_hourly(mw, _blocks(frame)))
    for _, data in members("commercial_remc_schedule.csv"):
        frame = pd.read_csv(io.BytesIO(data), usecols=range(14), dtype=str, low_memory=False)
        frame = frame[frame["from_entity"].eq("SRLDC") & frame["fromembd"].fillna("").str.match(TN_RE_PATTERN)]
        mw = pd.to_numeric(frame["sch"], errors="coerce").fillna(0.0)
        parts["interstate_re_mw"].append(_hourly(mw, _blocks(frame)))

    table = pd.DataFrame({
        name: pd.concat(series).groupby(level=0).sum().reindex(HOURS).fillna(0.0)
        for name, series in parts.items()
    })
    for name in ("actual_drawal_mw", "central_stations_mw"):
        if (table[name] == 0).sum() > 24:
            raise ValueError(f"{name}: more than a day of hours without data")
    table["net_import_mw"] = (
        table.actual_drawal_mw - table.central_stations_mw - table.tn_ipps_mw - table.interstate_re_mw
    )
    table.index.name = "snapshot"
    return table[COLUMNS]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    args = parser.parse_args(argv)
    table = read_metered_interchange(args.source_dir)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(OUTPUT, float_format="%.3f")
    monthly = table.groupby(table.index.to_period("M")).sum().div(1000.0).round(0)
    print(f"Wrote {OUTPUT.relative_to(ROOT)} ({len(table)} hours). GWh by month:")
    print(monthly.to_string())
    print("Year (TWh):", table.sum().div(1e6).round(2).to_dict())


if __name__ == "__main__":
    main()
