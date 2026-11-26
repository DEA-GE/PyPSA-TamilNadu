"""Monthly wind factors per area from the interstate wind plants' real output (seasonal shape only).

The area wind profiles (LAVA ``AggrPCWiderCUF``) have the right hourly timing
but a too-flat seasonal shape: compared with the metered output of the
interstate wind plants, they are too low in the monsoon and too high from
December to April (``scripts/validate_re_profiles.py``). This script turns the
plants' output into one factor per area and month that moves energy between
months and keeps each area's annual energy.

For a group of plants and an area profile:

    factor(month) = (plants' mean capacity factor in the month / over the year)
                  / (profile's mean in the month / over the year)

so that the profile times the factor has the plants' monthly shape and, before
any capping at 1, the profile's annual energy. Two groups:

- Tuticorin (6 plants, 1.77 GW) for the Tirunelveli area, where the plants are;
- Karur and Pugalur (JSW Renew Karur 1 and 2, Sprng; 586 MW) for every other
  area. They are the only interstate plants inland, so this is the weaker
  part: Coimbatore, Madurai and Trichy get the shape of three plants.

Only plants that run the whole year are used (TP Vardhaman starts in October
2025, SAUPL in July 2025). The output is the series of
``scripts/prepare_ists_re_plant_output.py``: metered where available, the REMC
schedule otherwise. The factors are for FY2025-26 only.

Writes ``data/model_inputs/wind_seasonal_factors_FY2025_26.csv``.

    python scripts/prepare_wind_seasonal_factors.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SERIES = ROOT / "data" / "model_inputs" / "tn_ists_re_plant_series_FY2025_26.csv"
PLANTS = ROOT / "data" / "model_inputs" / "tn_ists_re_plants_FY2025_26.csv"
PROFILES = ROOT / "data" / "model_inputs" / "renewable_profiles.csv"
OUTPUT = ROOT / "data" / "model_inputs" / "wind_seasonal_factors_FY2025_26.csv"
GROUPS = {
    "Tuticorin": [
        "Tuticorin_BETAMWIND", "Tuticorin_GIREL", "Tuticorin_Orange", "Tuticorin_Mytrah",
        "Tuticorin_JSWRETwo", "Tuticorin_JSWRenew_W",
    ],
    "Karur and Pugalur": ["KARUR_JSWRENEW_W", "KARUR_JSWRETwo_W", "PGLR_SREPL"],
}
AREA_GROUP = {"Tirunelveli": "Tuticorin"}  # every other area: "Karur and Pugalur"


def group_capacity_factor(series: pd.DataFrame, plants: pd.DataFrame, sellers: list[str]) -> pd.Series:
    output = series.pivot_table(index="snapshot", columns="seller", values="output_mw")[sellers]
    return output.sum(axis=1) / plants.loc[sellers, "capacity_mw"].sum()


def seasonal_factors(series: pd.DataFrame, plants: pd.DataFrame, profiles: pd.DataFrame) -> pd.DataFrame:
    rows = []
    observed = {name: group_capacity_factor(series, plants, sellers) for name, sellers in GROUPS.items()}
    for column in [c for c in profiles.columns if c.endswith("_wind")]:
        area = column[: -len("_wind")].replace("_", " ").title()
        group = AREA_GROUP.get(area, "Karur and Pugalur")
        obs = observed[group].reindex(profiles.index)
        if obs.isna().any():
            raise ValueError(f"The {group} plants have no output for {int(obs.isna().sum())} model hour(s)")
        profile = profiles[column]
        month = profiles.index.to_period("M")
        obs_shape = obs.groupby(month).mean() / obs.mean()
        profile_shape = profile.groupby(month).mean() / profile.mean()
        factor = obs_shape / profile_shape
        adjusted = profile * factor.reindex(month).to_numpy()
        clipped = (adjusted - adjusted.clip(upper=1.0)).groupby(month).sum() / adjusted.groupby(month).sum()
        rows.append(pd.DataFrame({
            "area": area, "month": factor.index.astype(str), "group": group,
            "plants_cf": obs.groupby(month).mean().to_numpy(), "profile_cf": profile.groupby(month).mean().to_numpy(),
            "factor": factor.to_numpy(), "clipped_share": clipped.reindex(factor.index).to_numpy(),
        }))
    return pd.concat(rows, ignore_index=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    series = pd.read_csv(SERIES, parse_dates=["snapshot"])
    plants = pd.read_csv(PLANTS, index_col="seller")
    profiles = pd.read_csv(PROFILES, parse_dates=["snapshot"]).set_index("snapshot")
    table = seasonal_factors(series, plants, profiles)
    table.to_csv(args.output, index=False, float_format="%.4f")
    print(table.pivot_table(index="month", columns="area", values="factor").to_string(float_format="{:.2f}".format))
    print("largest share of a month's energy lost by capping at 1:", f"{table.clipped_share.max():.2%}")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
