"""Hourly output of the interstate-connected wind and solar plants in Tamil Nadu, FY2025-26.

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
station: matching its schedule against the plants' REMC schedules shows which
(``SELLERS``), so QCA and plant rows must not be added together. Infirm
accounts (power before commercial operation) are dropped when they never
inject.

Writes:

- ``data/model_inputs/tn_ists_re_plant_output_FY2025_26.csv``: one row per
  account and complete hour, MW (``plants()`` gives each account's pooling
  station, technology and model area);
- ``data/model_inputs/tn_ists_re_plant_series_FY2025_26.csv``: one row per
  plant (REMC seller) and hour of the year. ``output_mw`` is the metered
  output from the plant's own account; where that is missing, its part of a
  QCA account (the QCA's metered output less its members' own metered output,
  split by schedule); otherwise its REMC schedule times a correction
  (``source`` says which). Schedules run too high in calm months (metered /
  scheduled 0.35-0.8 from October to April, about 1 from May to September),
  so a schedule-only hour is scaled by the metered / scheduled ratio of the
  same month: the plant's own where it has metered hours that month,
  otherwise that of the metered plants of its group (same technology,
  Tuticorin or inland), otherwise 1. ``scheduled_mw`` is the REMC schedule
  (``commercial_remc_schedule.csv``);
- ``data/model_inputs/tn_ists_re_plants_FY2025_26.csv``: one row per plant
  with technology, pooling station, model area of the pooling station (as in
  the transformer inventory), capacity and energy.

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
SERIES_OUTPUT = ROOT / "data" / "model_inputs" / "tn_ists_re_plant_series_FY2025_26.csv"
PLANTS_OUTPUT = ROOT / "data" / "model_inputs" / "tn_ists_re_plants_FY2025_26.csv"
REMC_FILE = "commercial_remc_schedule.csv"
REMC_SELLER = r"(?i)^(tuticorin|karur|pglr|ttgs)_"
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
    "jswrenew_karur": ("Karur", "wind", "Coimbatore"),
    "jswrenew_karur_infirm": ("Karur", "wind", "Coimbatore"),
    "tpv_karur": ("Karur", "wind", "Coimbatore"),
    "mal_karur_qca": ("Karur", "wind", "Coimbatore"),
    "spring_pugalur": ("Pugalur", "wind", "Trichy"),
    "saupl_pglr": ("Pugalur", "wind", "Trichy"),
}
COLUMNS = ["snapshot", "account", "actual_mw", "scheduled_mw", "available_mw"]
# REMC seller -> (its own deviation account or None, QCA accounts that include it,
# technology, pooling station). QCA membership was found by matching each QCA's
# schedule against the sellers' REMC schedules (non-negative least squares,
# R2 >= 0.975, coefficients 0.94-1.02). Infirm sellers (Tuticorin_JSWRETwo_I,
# TTGS_JSWRenew_W_I) are left out; they inject almost nothing.
SELLERS = {
    "Tuticorin_BETAMWIND": ("Betam_tuticorin", ("mal_tuticorin_qca", "mal_tuticorin_qca6"), "wind", "Tuticorin"),
    "Tuticorin_GIREL": ("girel_tuticorin", ("mal_tuticorin_qca", "mal_tuticorin_qca6"), "wind", "Tuticorin"),
    "Tuticorin_Orange": ("orange_tuticorin", ("mal_tuticorin_qca", "mal_tuticorin_qca6"), "wind", "Tuticorin"),
    "Tuticorin_Mytrah": ("mytrah_tuticorin", ("mal_tuticorin_qca6",), "wind", "Tuticorin"),
    "Tuticorin_JSWRETwo": ("jsw_renew2_tuticorin", ("mal_tuticorin_qca6",), "wind", "Tuticorin"),
    "Tuticorin_JSWRenew_W": (None, ("mal_tuticorin_qca6",), "wind", "Tuticorin"),
    "Tuticorin_NTPCETPRM": ("ntpc_ettayapuram_tuticorin", ("mal_tuticorin_qca_s",), "solar", "Tuticorin"),
    "Tuticorin_GRTJIPL": ("grtjipl_tuticorin", ("mal_tuticorin_qca_s",), "solar", "Tuticorin"),
    "KARUR_JSWRENEW_W": ("jswrenew_karur", ("mal_karur_qca",), "wind", "Karur"),
    "KARUR_JSWRETwo_W": (None, ("mal_karur_qca",), "wind", "Karur"),
    "KARUR_TPVARDHAMAN_W": ("tpv_karur", (), "wind", "Karur"),
    "PGLR_SREPL": ("spring_pugalur", (), "wind", "Pugalur"),
    "PGLR_SAUPL_W": ("saupl_pglr", (), "wind", "Pugalur"),
}
# Model area of each pooling station, as in the transformer inventory
# (data/source/srldc_transmission_elements/tn_icts_2026-03.csv).
POOL_AREAS = {"Tuticorin": "Tirunelveli", "Karur": "Coimbatore", "Pugalur": "Trichy"}
# Plants whose metered / scheduled ratio corrects each other's schedule-only months.
POOL_GROUPS = {"Tuticorin": "Tuticorin", "Karur": "inland", "Pugalur": "inland"}
MIN_SCHEDULED_MWH = 100.0  # a ratio needs at least this much scheduled energy in the month


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


def read_remc_schedules(source_dir: Path = SOURCE_DIR, inventory: Path = INVENTORY) -> pd.DataFrame:
    """Hourly REMC schedule (MW) of every seller in ``SELLERS``."""
    listing = pd.read_csv(inventory)
    members = listing[(listing.filename == REMC_FILE) & ~listing.is_provisional]
    if len(members) != 24:
        raise ValueError(f"Expected 24 final half-month {REMC_FILE} members, found {len(members)}")
    cache: dict = {}
    parts = []
    for member in members.itertuples():
        frame = pd.read_csv(io.BytesIO(read_archive_member(source_dir, member.virtual_path, cache)),
                            usecols=range(14), dtype=str, low_memory=False)
        frame = frame[frame.from_entity.eq("SRLDC") & frame.fromembd.fillna("").str.match(REMC_SELLER)]
        parts.append(pd.DataFrame({"stamp": _stamps(frame).to_numpy(), "seller": frame.fromembd.to_numpy(),
                                   "mw": pd.to_numeric(frame.sch, errors="coerce").fillna(0.0).to_numpy()}))
    blocks = pd.concat(parts).groupby(["stamp", "seller"]).mw.sum().unstack(fill_value=0.0)
    hourly_mw = blocks.groupby(blocks.index.floor("h")).sum() / 4.0
    missing = set(SELLERS) - set(hourly_mw.columns)
    if missing:
        raise ValueError(f"Sellers without a REMC schedule: {sorted(missing)}")
    return hourly_mw.reindex(HOURS, fill_value=0.0)[list(SELLERS)]


def schedule_correction(output: pd.DataFrame, schedules: pd.DataFrame, source: pd.DataFrame) -> pd.DataFrame:
    """Hourly factor for schedule-only hours: metered / scheduled of the same month (own, else group, else 1)."""
    metered = source.ne("schedule")
    month = output.index.to_period("M")
    actual = output.where(metered).groupby(month).sum()
    scheduled = schedules.where(metered).groupby(month).sum()
    own = (actual / scheduled).where(scheduled >= MIN_SCHEDULED_MWH)
    groups = pd.Series({seller: (spec[2], POOL_GROUPS[spec[3]]) for seller, spec in SELLERS.items()})
    ratio = own.copy()
    for _, members in groups.groupby(groups).groups.items():
        members = list(members)
        group_actual, group_scheduled = actual[members].sum(axis=1), scheduled[members].sum(axis=1)
        group = (group_actual / group_scheduled).where(group_scheduled >= MIN_SCHEDULED_MWH)
        ratio[members] = ratio[members].apply(lambda column: column.fillna(group))
    return ratio.fillna(1.0).reindex(month).set_axis(output.index)


def seller_series(accounts: pd.DataFrame, schedules: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Hourly output of each seller (long table) and the seller table with capacity and energy."""
    actual = accounts.pivot_table(index="snapshot", columns="account", values="actual_mw").reindex(HOURS)
    available = accounts.pivot_table(index="snapshot", columns="account", values="available_mw").reindex(HOURS)
    sellers = list(SELLERS)
    output = pd.DataFrame(float("nan"), index=HOURS, columns=sellers)
    source = pd.DataFrame("schedule", index=HOURS, columns=sellers)
    for seller, (own, *_rest) in SELLERS.items():
        if own in actual:
            have = actual[own].notna()
            output.loc[have, seller] = actual.loc[have, own]
            source.loc[have, seller] = "metered"
    qcas = sorted({qca for _, members, *_ in SELLERS.values() for qca in members})
    mismatch = {}
    for qca in qcas:
        members = [seller for seller, spec in SELLERS.items() if qca in spec[1]]
        rows = actual.index[actual[qca].notna()] if qca in actual else HOURS[:0]
        known = output.loc[rows, members]
        unknown = known.isna()
        remainder = (actual.loc[rows, qca] - known.sum(axis=1)).clip(lower=0.0)
        complete = ~unknown.any(axis=1)
        mismatch[qca] = float(remainder[complete].sum() / max(actual.loc[rows, qca].sum(), 1.0))
        weights = schedules.loc[rows, members].where(unknown, 0.0)
        share = weights.div(weights.sum(axis=1).where(lambda s: s > 0), axis=0).fillna(0.0)
        output.loc[rows, members] = known.where(~unknown, share.mul(remainder, axis=0))
        source.loc[rows, members] = source.loc[rows, members].where(~unknown, "qca")
    ratio = schedule_correction(output, schedules, source)
    corrected = schedules * ratio
    output = output.fillna(corrected)
    source = source.where(source.ne("schedule"), "schedule_corrected")
    capacity = {}
    for seller, (own, members, *_rest) in SELLERS.items():
        candidates = [output[seller].max(), schedules[seller].max()]
        if own in available:
            candidates.append(available[own].max())
        for qca in members:
            others = [SELLERS[s][0] for s in SELLERS if qca in SELLERS[s][1] and s != seller]
            if own is None and qca in available and all(o in available for o in others):
                candidates.append((available[qca] - available[others].sum(axis=1, min_count=len(others))).max())
        capacity[seller] = max(c for c in candidates if pd.notna(c))
    series = pd.DataFrame({
        "snapshot": output.index.repeat(len(sellers)), "seller": sellers * len(HOURS),
        "output_mw": output.to_numpy().ravel(), "scheduled_mw": schedules.to_numpy().ravel(),
        "source": source.to_numpy().ravel(),
    })
    table = pd.DataFrame({
        "technology": [SELLERS[s][2] for s in sellers], "pool": [SELLERS[s][3] for s in sellers],
        "area": [POOL_AREAS[SELLERS[s][3]] for s in sellers], "capacity_mw": pd.Series(capacity).round(1),
        "output_gwh": (output.sum() / 1e3).round(1), "scheduled_gwh": (schedules.sum() / 1e3).round(1),
        "metered_share": (output.where(source.ne("schedule_corrected")).sum() / output.sum()).round(3),
    }, index=pd.Index(sellers, name="seller"))
    table.attrs["qca_mismatch"] = mismatch
    return series, table


def plants() -> pd.DataFrame:
    """Pooling station, technology and model area of each account; ``qca`` marks aggregates."""
    table = pd.DataFrame(PLANTS, index=["pool", "technology", "area"]).T.rename_axis("account")
    return table.assign(qca=table.index.str.contains("_qca"))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--series-output", type=Path, default=SERIES_OUTPUT)
    parser.add_argument("--plants-output", type=Path, default=PLANTS_OUTPUT)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    table = hourly(read_blocks(args.source_dir, args.inventory))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.output, index=False, float_format="%.2f")
    series, sellers = seller_series(table, read_remc_schedules(args.source_dir, args.inventory))
    series.to_csv(args.series_output, index=False, float_format="%.1f")
    sellers.to_csv(args.plants_output)
    print(sellers.to_string())
    print("QCA output not explained by its members' own accounts (hours where all members report):",
          {qca: f"{share:.1%}" for qca, share in sellers.attrs["qca_mismatch"].items()})
    summary = table.join(plants(), on="account").groupby(["account", "technology"]).agg(
        hours=("snapshot", "size"), first=("snapshot", "min"), last=("snapshot", "max"),
        capacity_mw=("available_mw", "max"), actual_gwh=("actual_mw", lambda x: x.sum() / 1e3))
    print(summary.to_string(float_format="%.1f"))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
