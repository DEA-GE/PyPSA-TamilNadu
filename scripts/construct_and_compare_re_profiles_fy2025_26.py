"""Construct FY2025-26 RE profiles from calendar-year files and compare them.

The source directory contains one wind and one solar capacity-factor CSV for
each balancing area and calendar year. This script selects 2025-04-01 through
2026-03-31 (inclusive), applies the five-hour shift, and compares the complete
8,760-hour fiscal year with the archived previous model input. It also checks
that the corrected series are now active in the model.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "model" / "TamilNadu9BAs_profiles_2025_2026"
MODEL_INPUT = ROOT / "data" / "model_inputs" / "renewable_profiles.csv"
PREVIOUS_MODEL_INPUT = (
    ROOT / "data" / "model_inputs" / "renewable_profiles_before_external_FY2025_26.csv"
)
MODEL_GENERATORS = ROOT / "model" / "generators.csv"
MODEL_GENERATOR_PROFILES = ROOT / "model" / "generators-p_max_pu.csv"
CONSTRUCTED_OUTPUT = SOURCE_ROOT / "renewable_profiles_FY2025_26.csv"
RESULTS_DIR = ROOT / "results" / "renewable_profile_comparison"

AREA_SOURCE_NAMES = {
    "chennai_north": "ChennaiNorth",
    "chennai_south": "ChennaiSouth",
    "coimbatore": "Coimbatore",
    "erode": "Erode",
    "madurai": "Madurai",
    "tirunelveli": "Tirunelveli",
    "trichy": "Trichy",
    "vellore": "Vellore",
    "villupuram": "Villupuram",
}
TECHNOLOGIES = {"wind": "onshorewind", "solar": "solar"}
FY_SNAPSHOTS = pd.date_range("2025-04-01 00:00:00", "2026-03-31 23:00:00", freq="h")
# The supplied profiles must be scheduled five hours later to share the model's
# hourly convention.  The final five FY hours therefore wrap to its beginning.
SOURCE_TO_MODEL_SHIFT_HOURS = 5


def read_calendar_profile(area_slug: str, technology: str, year: int) -> pd.Series:
    """Read and validate one supplied area/technology source profile."""
    area = AREA_SOURCE_NAMES[area_slug]
    source_technology = TECHNOLOGIES[technology]
    path = SOURCE_ROOT / str(year) / area / f"{area}_{source_technology}_9BA_profile_{year}.csv"
    frame = pd.read_csv(path)
    if list(frame.columns) != ["time", area]:
        raise ValueError(f"Unexpected columns in {path}: {list(frame.columns)}")

    index = pd.DatetimeIndex(pd.to_datetime(frame["time"], errors="raise"))
    # The 2026 inputs deliberately extend only to 30 April; FY construction
    # needs their January--March portion.  Keep that supplied coverage
    # explicit so a truncated or expanded source cannot pass unnoticed.
    expected_end = f"{year}-12-31 23:00:00" if year == 2025 else "2026-04-30 23:00:00"
    expected = pd.date_range(f"{year}-01-01", expected_end, freq="h")
    if not index.equals(expected):
        raise ValueError(f"{path} does not contain the expected ordered source period")

    values = pd.to_numeric(frame[area], errors="raise")
    if values.isna().any() or ((values < 0) | (values > 1)).any():
        raise ValueError(f"{path} contains missing or out-of-range capacity factors")
    return pd.Series(values.to_numpy(), index=index, name=f"{area_slug}_{technology}")


def construct_fiscal_year() -> pd.DataFrame:
    """Return the nine-area, two-technology FY2025-26 profile table."""
    constructed = pd.DataFrame(index=FY_SNAPSHOTS)
    for area_slug in AREA_SOURCE_NAMES:
        for technology in TECHNOLOGIES:
            calendar = pd.concat(
                [
                    read_calendar_profile(area_slug, technology, 2025),
                    read_calendar_profile(area_slug, technology, 2026),
                ]
            )
            fiscal_year_values = calendar.reindex(FY_SNAPSHOTS).to_numpy()
            constructed[f"{area_slug}_{technology}"] = np.roll(
                fiscal_year_values, SOURCE_TO_MODEL_SHIFT_HOURS
            )

    if constructed.isna().any().any():
        raise ValueError("The constructed fiscal year contains missing hours")
    constructed.insert(0, "snapshot", constructed.index.strftime("%Y-%m-%d %H:%M:%S"))
    return constructed.reset_index(drop=True)


def compare(model: pd.DataFrame, constructed: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return per-series and monthly metrics for aligned capacity factors."""
    if model["snapshot"].tolist() != constructed["snapshot"].tolist():
        raise ValueError("Model and constructed profiles do not share the same FY2025-26 snapshots")
    columns = list(constructed.columns[1:])
    missing = sorted(set(columns) - set(model.columns))
    if missing:
        raise ValueError(f"Model input is missing constructed profile columns: {missing}")

    model_values = model[columns].apply(pd.to_numeric, errors="raise")
    source_values = constructed[columns]
    difference = source_values - model_values
    lag_metrics = {}
    for column in columns:
        correlations = {
            lag: model_values[column].corr(source_values[column].shift(lag))
            for lag in range(-12, 13)
        }
        best_lag = max(correlations, key=correlations.get)
        lag_metrics[column] = (correlations[best_lag], best_lag)
    per_series = pd.DataFrame(
        {
            "model_mean_cf": model_values.mean(),
            "constructed_mean_cf": source_values.mean(),
            "mean_cf_difference": difference.mean(),
            "mae": difference.abs().mean(),
            "rmse": np.sqrt((difference**2).mean()),
            "max_abs_difference": difference.abs().max(),
            "pearson_correlation": model_values.corrwith(source_values),
            "best_correlation_within_12h": pd.Series(
                {column: lag_metrics[column][0] for column in columns}
            ),
            "constructed_shift_for_best_correlation_h": pd.Series(
                {column: lag_metrics[column][1] for column in columns}
            ),
            "matching_hours": difference.eq(0).sum(),
            "matching_hours_pct": difference.eq(0).mean() * 100,
        }
    )
    per_series.index.name = "profile"

    monthly = difference.copy()
    monthly.index = pd.DatetimeIndex(model["snapshot"])
    monthly = monthly.resample("MS").mean().T
    monthly.index.name = "profile"
    monthly.columns = [timestamp.strftime("%Y-%m") for timestamp in monthly.columns]
    return per_series, monthly


def fleet_potential_comparison(model: pd.DataFrame, constructed: pd.DataFrame) -> pd.DataFrame:
    """Compare annual wind/solar potential using the capacities now in model/."""
    generators = pd.read_csv(MODEL_GENERATORS)
    renewable = generators.loc[generators["carrier"].isin(TECHNOLOGIES)].copy()
    renewable["profile"] = (
        renewable["bus"].str.lower().str.replace(" ", "_", regex=False)
        + "_"
        + renewable["carrier"]
    )
    capacities = renewable.groupby(["carrier", "profile"])["p_nom"].sum()
    profiles = list(capacities.index.get_level_values("profile"))
    if missing := sorted(set(profiles) - set(constructed.columns)):
        raise ValueError(f"No constructed profile for renewable generator groups: {missing}")

    rows = []
    for carrier in sorted(TECHNOLOGIES):
        carrier_capacities = capacities.loc[carrier]
        capacity_mw = float(carrier_capacities.sum())
        model_potential_mwh = float(
            model[carrier_capacities.index].mul(carrier_capacities, axis=1).sum().sum()
        )
        constructed_potential_mwh = float(
            constructed[carrier_capacities.index]
            .mul(carrier_capacities, axis=1)
            .sum()
            .sum()
        )
        rows.append(
            {
                "carrier": carrier,
                "model_capacity_mw": capacity_mw,
                "model_potential_gwh": model_potential_mwh / 1_000,
                "constructed_potential_gwh": constructed_potential_mwh / 1_000,
                "potential_difference_gwh": (constructed_potential_mwh - model_potential_mwh)
                / 1_000,
                "potential_difference_pct": (
                    (constructed_potential_mwh / model_potential_mwh - 1) * 100
                ),
            }
        )
    return pd.DataFrame(rows).set_index("carrier")


def validate_active_model_profile_provenance(model: pd.DataFrame) -> int:
    """Confirm the active input is the profile materialized on RE generators."""
    generators = pd.read_csv(MODEL_GENERATORS)
    generator_profiles = pd.read_csv(MODEL_GENERATOR_PROFILES, index_col=0)
    renewable = generators.loc[generators["carrier"].isin(TECHNOLOGIES)].copy()
    renewable["profile"] = (
        renewable["bus"].str.lower().str.replace(" ", "_", regex=False)
        + "_"
        + renewable["carrier"]
    )
    for row in renewable.itertuples(index=False):
        if row.name not in generator_profiles:
            raise ValueError(f"Missing active model generator profile: {row.name}")
        if not np.allclose(
            generator_profiles[row.name].to_numpy(dtype=float),
            model[row.profile].to_numpy(dtype=float),
            rtol=0,
            atol=1e-12,
        ):
            raise ValueError(f"Active model profile differs from its input: {row.name}")
    return len(renewable)


def main() -> None:
    constructed = construct_fiscal_year()
    previous_model = pd.read_csv(PREVIOUS_MODEL_INPUT)
    active_model = pd.read_csv(MODEL_INPUT)
    if len(previous_model) != len(FY_SNAPSHOTS) or len(active_model) != len(FY_SNAPSHOTS):
        raise ValueError("Expected 8,760 rows in both previous and active model inputs")
    if active_model["snapshot"].tolist() != constructed["snapshot"].tolist():
        raise ValueError("Active model timestamps differ from the constructed fiscal year")
    columns = list(constructed.columns[1:])
    if not np.allclose(
        active_model[columns].to_numpy(dtype=float),
        constructed[columns].to_numpy(dtype=float),
        rtol=0,
        atol=1e-12,
    ):
        raise ValueError("Active model input differs from the corrected external profiles")

    per_series, monthly = compare(previous_model, constructed)
    fleet = fleet_potential_comparison(previous_model, constructed)
    renewable_generator_count = validate_active_model_profile_provenance(active_model)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    constructed.to_csv(CONSTRUCTED_OUTPUT, index=False)
    per_series.to_csv(RESULTS_DIR / "FY2025_26_profile_comparison_by_series.csv")
    monthly.to_csv(RESULTS_DIR / "FY2025_26_profile_mean_difference_by_month.csv")
    fleet.to_csv(RESULTS_DIR / "FY2025_26_fleet_potential_comparison.csv")

    print(f"Constructed: {CONSTRUCTED_OUTPUT}")
    print(f"Rows and profiles: {len(constructed):,} x {len(constructed.columns) - 1}")
    print(f"Verified active-profile provenance for {renewable_generator_count} renewable generators")
    print(f"Per-series comparison: {RESULTS_DIR / 'FY2025_26_profile_comparison_by_series.csv'}")
    print(f"Monthly mean differences: {RESULTS_DIR / 'FY2025_26_profile_mean_difference_by_month.csv'}")
    print(f"Fleet potential comparison: {RESULTS_DIR / 'FY2025_26_fleet_potential_comparison.csv'}")
    print(per_series.round(6).to_string())
    print(fleet.round(3).to_string())


if __name__ == "__main__":
    main()
