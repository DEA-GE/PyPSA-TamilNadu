"""Hourly metered output of the interstate-connected wind and solar plants in Tamil Nadu, FY2025-26.

Wind and solar plants on the interstate pooling stations at Tuticorin, Karur
and Pugalur are regional entities: SRPC keeps a deviation account for each
of them (``commercial_dev2022_<plant>.csv`` in the Regional Energy Accounts),
with, per 15-minute block, the available capacity declared by the plant, its
schedule and its metered injection (MWh per block). These are real plant
output data, used to validate the model's wind and solar profiles
(``scripts/validate_re_profiles.py``).

The accounts are weekly for some plants; the weeks are spread over the
final and the provisional archives, so both are read. Blocks present in more
than one archive must agree (the final archive is kept). A QCA account
(qualified coordinating agency) aggregates several plants of one pooling
station and is kept as one row per hour; whether it overlaps a plant's own
account is not known, so do not add QCA and plant rows together. Infirm
accounts (power before commercial operation) are dropped when they never
inject.

Writes ``data/model_inputs/tn_ists_re_plant_output_FY2025_26.csv``: one row
per account and complete hour, MW (``plants()`` gives each account's pooling
station, technology and model area).

    python scripts/prepare_ists_re_plant_output.py [--source-dir "data/source/REA data"]
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

OUTPUT = ROOT / "data" / "model_inputs" / "tn_ists_re_plant_output_FY2025_26.csv"
PREFIX = "commercial_dev2022_"
# Account name -> (pooling station, technology, model area of the pooling station).
PLANTS = {
    "Betam_tuticorin": ("Tuticorin", "wind", "Tirunelveli"),
    "girel_tuticorin": ("Tuticorin", "wind", "Tirunelveli"),
    "mytrah_tuticorin": ("Tuticorin", "wind", "Tirunelveli"),
    "orange_tuticorin": ("Tuticorin", "wind", "Tirunelveli"),
    "jsw_renew2_tuticorin": ("Tuticorin", "wind", "Tirunelveli"),
    "jsw_renew2_tuticorin_infirm": ("Tuticorin", "wind", "Tirunelveli"),
    "mal_tuticorin_qca": ("Tuticorin", "wind", "Tirunelveli"),
    "mal_tuticorin_qca6": ("Tuticorin", "wind", "Tirunelveli"),
    "mal_tuticorin_qca_s": ("Tuticorin", "solar", "Tirunelveli"),
    "ntpc_ettayapuram_tuticorin": ("Tuticorin", "solar", "Tirunelveli"),
    "grtjipl_tuticorin": ("Tuticorin", "solar", "Tirunelveli"),
    "jswrenew_karur": ("Karur", "wind", "Trichy"),
    "jswrenew_karur_infirm": ("Karur", "wind", "Trichy"),
    "tpv_karur": ("Karur", "wind", "Trichy"),
    "mal_karur_qca": ("Karur", "wind", "Trichy"),
    "spring_pugalur": ("Pugalur", "wind", "Trichy"),
    "saupl_pglr": ("Pugalur", "wind", "Trichy"),
}
COLUMNS = ["snapshot", "account", "actual_mw", "scheduled_mw", "available_mw"]


def _stamps(frame: pd.DataFrame) -> pd.Series:
    time = frame["time"].astype(str)
    date = pd.to_datetime(frame["date"], format="%Y-%m-%d")
    if time.str.contains(":").all():
        return date + pd.to_timedelta(time)
    return date + pd.to_timedelta((pd.to_numeric(time) - 1) * 15, unit="min")


def read_blocks(source_dir: Path = SOURCE_DIR, inventory: Path = INVENTORY) -> pd.DataFrame:
    listing = pd.read_csv(inventory)
    listing = listing[listing.filename.isin([f"{PREFIX}{name}.csv" for name in PLANTS])]
    found = {name[len(PREFIX):-4] for name in listing.filename}
    if found != set(PLANTS):
        raise ValueError(f"Accounts missing from the REA inventory: {sorted(set(PLANTS) - found)}")
    cache: dict = {}
    frames = []
    for member in listing.itertuples():
        frame = pd.read_csv(io.BytesIO(read_archive_member(source_dir, member.virtual_path, cache)), dtype=str)
        frames.append(pd.DataFrame({
            "stamp": _stamps(frame),
            "account": member.filename[len(PREFIX):-4],
            "final": not member.is_provisional,
            # MWh per 15-minute block -> MW
            **{name: pd.to_numeric(frame[column], errors="raise") * 4.0
               for name, column in (("actual_mw", "act_total"), ("scheduled_mw", "sch_total"),
                                    ("available_mw", "available_capacity"))},
        }))
    blocks = pd.concat(frames, ignore_index=True)
    spread = blocks.groupby(["account", "stamp"])[["actual_mw", "scheduled_mw", "available_mw"]].agg(
        lambda x: x.max() - x.min()) > 1e-6
    if spread[["actual_mw", "scheduled_mw"]].any(axis=None):
        raise ValueError("Metered or scheduled output differs between archives")
    if spread.available_mw.any():
        # Known case: 64 blocks of spring_pugalur in early May 2025, where the
        # final account revised the available capacity to 0.
        print(f"Available capacity differs between archives in {int(spread.available_mw.sum())} "
              "block(s); the final archive is kept")
    return (blocks.sort_values("final").drop_duplicates(["account", "stamp"], keep="last")
            .drop(columns="final").sort_values(["account", "stamp"]))


def hourly(blocks: pd.DataFrame) -> pd.DataFrame:
    blocks = blocks[blocks.stamp.ge(HOURS[0]) & blocks.stamp.lt(HOURS[-1] + pd.Timedelta(hours=1))]
    grouped = blocks.groupby(["account", blocks.stamp.dt.floor("h").rename("snapshot")])
    table = grouped[["actual_mw", "scheduled_mw", "available_mw"]].mean()[grouped.size() == 4].reset_index()
    silent = table.groupby("account").actual_mw.max().le(0)
    return table[~table.account.isin(silent.index[silent])][COLUMNS]


def plants() -> pd.DataFrame:
    """Pooling station, technology and model area of each account; ``qca`` marks aggregates."""
    table = pd.DataFrame(PLANTS, index=["pool", "technology", "area"]).T.rename_axis("account")
    return table.assign(qca=table.index.str.contains("_qca"))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    table = hourly(read_blocks(args.source_dir, args.inventory))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.output, index=False, float_format="%.2f")
    summary = table.join(plants(), on="account").groupby(["account", "technology"]).agg(
        hours=("snapshot", "size"), first=("snapshot", "min"), last=("snapshot", "max"),
        capacity_mw=("available_mw", "max"), actual_gwh=("actual_mw", lambda x: x.sum() / 1e3))
    print(summary.to_string(float_format="%.1f"))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
