"""Hourly power-exchange trades of Tamil Nadu, FY2025-26, from the SRPC REA.

SRPC's composite schedule for Tamil Nadu (``commercial_sch_tamilnadu.csv`` in
each half-month Regional Energy Account) splits the state's scheduled drawal
per 15-minute block into its channels: long-term (``lta``), GNA, T-GNA, the
power-exchange products and REMC. The exchange columns are MW per block, at
the regional periphery (before Tamil Nadu's share of transmission losses),
imports positive and exports negative:

    px_imp / px_exp        day-ahead market (DAM)
    gdam_imp / gdam_exp    green day-ahead market
    hpdam_imp / hpdam_exp  high-price day-ahead market
    rtm_imp / rtm_exp      real-time market

Writes ``data/model_inputs/tn_exchange_trades_FY2025_26.csv`` (hourly mean MW,
imports and exports as positive magnitudes). The exchange trading module
(``scripts/exchange_trading.py``) uses it to take the observed exchange trades
out of the fixed interstate schedule and, optionally, as the trade limit.

    python scripts/prepare_exchange_trades.py [--source-dir "data/source/REA data"]
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
from prepare_metered_interchange import _blocks, _hourly  # noqa: E402

OUTPUT = ROOT / "data" / "model_inputs" / "tn_exchange_trades_FY2025_26.csv"
MEMBER = "commercial_sch_tamilnadu.csv"
PRODUCTS = {"dam": "px", "gdam": "gdam", "hpdam": "hpdam", "rtm": "rtm"}


def read_exchange_trades(source_dir: Path = SOURCE_DIR, inventory: Path = INVENTORY) -> pd.DataFrame:
    listing = pd.read_csv(inventory)
    rows = listing[~listing.is_provisional & listing.filename.eq(MEMBER)].sort_values("date_start")
    if len(rows) != 24:
        raise ValueError(f"Expected 24 final half-month {MEMBER} members, found {len(rows)}")
    cache: dict = {}
    parts: dict[str, list[pd.Series]] = {}
    for row in rows.itertuples():
        frame = pd.read_csv(io.BytesIO(read_archive_member(source_dir, row.virtual_path, cache)))
        stamps = _blocks(frame)
        for product, prefix in PRODUCTS.items():
            for direction, suffix, sign in (("import", "imp", 1.0), ("export", "exp", -1.0)):
                values = pd.to_numeric(frame[f"{prefix}_{suffix}"], errors="raise") * sign
                if (values < -1e-6).any():
                    raise ValueError(f"{row.virtual_path}: {prefix}_{suffix} has the wrong sign")
                parts.setdefault(f"{product}_{direction}_mw", []).append(_hourly(values, stamps))
    table = pd.DataFrame({
        name: pd.concat(series).groupby(level=0).sum().reindex(HOURS)
        for name, series in parts.items()
    })
    missing = table.isna().all(axis=1)
    if missing.sum() > 0:
        raise ValueError(f"{int(missing.sum())} hours without {MEMBER} data")
    table = table.fillna(0.0)
    imports = [f"{p}_import_mw" for p in PRODUCTS]
    exports = [f"{p}_export_mw" for p in PRODUCTS]
    table["exchange_import_mw"] = table[imports].sum(axis=1)
    table["exchange_export_mw"] = table[exports].sum(axis=1)
    table["exchange_net_import_mw"] = table.exchange_import_mw - table.exchange_export_mw
    table.index.name = "snapshot"
    return table[imports + exports + ["exchange_import_mw", "exchange_export_mw", "exchange_net_import_mw"]]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    args = parser.parse_args(argv)
    table = read_exchange_trades(args.source_dir, args.inventory)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(OUTPUT, float_format="%.3f")
    monthly = table.groupby(table.index.to_period("M")).sum().div(1000.0).round(0)
    print(f"Wrote {OUTPUT.relative_to(ROOT)} ({len(table)} hours). GWh by month:")
    print(monthly.to_string())
    print("Year (TWh):", table.sum().div(1e6).round(2).to_dict())


if __name__ == "__main__":
    main()
