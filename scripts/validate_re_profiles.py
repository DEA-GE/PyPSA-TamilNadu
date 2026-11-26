"""Compare the model's wind and solar availability with metered plant output, FY2025-26.

The interstate-connected wind and solar plants at Tuticorin, Karur and
Pugalur are metered by SRPC (``data/model_inputs/tn_ists_re_plant_output_FY2025_26.csv``,
``scripts/prepare_ists_re_plant_output.py``). Each group of plants is compared,
over the hours it reports, with the availability the model gives the area it
sits in (``model/generators-p_max_pu.csv``; solar divided by the installed
share of the fleet, so both are output per installed MW):

- capacity factor per month: observed (metered output / the plant's highest
  declared available capacity, taken as its installed capacity) and model;
- ``model/obs`` and ``model/obs normalised``: the ratio of the two, and the
  same ratio after dividing each series by its own mean over the group's
  hours. The normalised ratio tests the seasonal shape alone: the plants are
  recent, tall turbines and high-DC/AC solar, so their level is above the
  fleet's;
- hourly and daily correlation, the lag with the best hourly correlation, and
  the hourly correlation with every area's profile;
- the mean diurnal profile.

Writes CSVs to ``results/re_profile_validation/`` and prints the summary.

    python scripts/validate_re_profiles.py [--output-dir results/re_profile_validation]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from prepare_ists_re_plant_output import OUTPUT as PLANT_OUTPUT  # noqa: E402
from solar_capacity_trajectory import solar_capacity_mw  # noqa: E402

MODEL = ROOT / "model"
OUTPUT_DIR = ROOT / "results" / "re_profile_validation"
# Group -> (accounts, model generator whose availability represents their area).
GROUPS = {
    "Tuticorin wind (Betam, GIREL, Mytrah, Orange)": (
        ["Betam_tuticorin", "girel_tuticorin", "mytrah_tuticorin", "orange_tuticorin"],
        "tamil_nadu_thoothukkudi__aggregate_01"),
    "Tuticorin wind (QCA, 1.7 GW)": (["mal_tuticorin_qca6"], "tamil_nadu_thoothukkudi__aggregate_01"),
    "Pugalur wind (Sprng)": (["spring_pugalur"], "tamil_nadu_karur__aggregate_01"),
    "Tuticorin solar (NTPC Ettayapuram, GRT)": (
        ["ntpc_ettayapuram_tuticorin", "grtjipl_tuticorin"], "tamil_nadu_thoothukkudi_28__aggregate_01"),
}


def model_availability() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Availability per installed MW of every generator, and of every area profile, on timestamps."""
    snapshots = pd.DatetimeIndex(pd.read_csv(MODEL / "snapshots.csv")["snapshot"])
    p_max_pu = pd.read_csv(MODEL / "generators-p_max_pu.csv", index_col=0)
    p_max_pu.index = snapshots
    generators = pd.read_csv(MODEL / "generators.csv", index_col=0)
    solar = generators.index[generators.carrier.eq("solar")].intersection(p_max_pu.columns)
    share = solar_capacity_mw(snapshots) / generators.loc[generators.carrier.eq("solar"), "p_nom"].sum()
    p_max_pu[solar] = p_max_pu[solar].div(share, axis=0)
    profiles = pd.read_csv(ROOT / "data/model_inputs/renewable_profiles.csv", parse_dates=["snapshot"]).set_index("snapshot")
    return p_max_pu, profiles


def observed(table: pd.DataFrame, accounts: list[str]) -> pd.Series:
    """Capacity factor of a group: hours in which every account reporting that month reports."""
    rows = table[table.account.isin(accounts)]
    capacity = rows.groupby("account").available_mw.max()
    rows = rows.assign(capacity=rows.account.map(capacity))
    hours = rows.groupby("snapshot").agg(actual=("actual_mw", "sum"), capacity=("capacity", "sum"))
    full = hours.capacity.groupby(hours.index.to_period("M")).transform("max")
    hours = hours[hours.capacity.eq(full)]
    return (hours.actual / hours.capacity).rename("observed")


def compare(obs: pd.Series, model: pd.Series, profiles: pd.DataFrame, technology: str) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    model = model.reindex(obs.index)
    month = obs.index.to_period("M")
    monthly = pd.DataFrame({
        "hours": obs.groupby(month).size(), "obs_cf": obs.groupby(month).mean(), "model_cf": model.groupby(month).mean(),
    })
    monthly["model/obs"] = monthly.model_cf / monthly.obs_cf
    monthly["model/obs normalised"] = (monthly.model_cf / model.mean()) / (monthly.obs_cf / obs.mean())
    lags = {lag: obs.corr(model.shift(lag)) for lag in range(-3, 4)}
    area_corr = {column.rsplit("_", 1)[0]: obs.corr(profiles[column].reindex(obs.index))
                 for column in profiles.columns if column.endswith(technology)}
    summary = {
        "hours": len(obs), "obs_cf": obs.mean(), "model_cf": model.mean(),
        "hourly_corr": obs.corr(model), "daily_corr": obs.resample("D").mean().corr(model.resample("D").mean()),
        "best_lag_h": max(lags, key=lags.get), "best_area": max(area_corr, key=area_corr.get),
        "best_area_corr": max(area_corr.values()),
    }
    diurnal = pd.DataFrame({"obs_cf": obs, "model_cf": model}).groupby(obs.index.hour).mean().rename_axis("hour")
    return summary, monthly, diurnal


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plants", type=Path, default=PLANT_OUTPUT)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    table = pd.read_csv(args.plants, parse_dates=["snapshot"])
    availability, profiles = model_availability()
    summaries, monthly, diurnal = {}, [], []
    for group, (accounts, generator) in GROUPS.items():
        technology = "solar" if "solar" in group else "wind"
        summary, months, hours = compare(observed(table, accounts), availability[generator], profiles, technology)
        summaries[group] = summary
        monthly.append(months.assign(group=group))
        diurnal.append(hours.assign(group=group))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = pd.DataFrame(summaries).T.infer_objects()
    summary.to_csv(args.output_dir / "summary.csv", index_label="group")
    monthly = pd.concat(monthly).rename_axis("month").reset_index()
    monthly.to_csv(args.output_dir / "monthly.csv", index=False, float_format="%.4f")
    pd.concat(diurnal).reset_index().to_csv(args.output_dir / "diurnal.csv", index=False, float_format="%.4f")
    pd.set_option("display.width", 200)
    print(summary.to_string(float_format="{:.3f}".format))
    for group, months in monthly.groupby("group", sort=False):
        print(f"\n{group}")
        print(months.drop(columns="group").set_index("month").to_string(float_format="{:.3f}".format))
    print(f"\nWrote {args.output_dir}")


if __name__ == "__main__":
    main()
