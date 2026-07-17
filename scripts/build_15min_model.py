"""Build the 15-minute FY 2025-26 variant of the Tamil Nadu model."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "inputs" / "tamil_nadu_ra_2025_26"
OUTPUT_DIR = ROOT / "inputs" / "tamil_nadu_ra_2025_26_15min"
ADDITIONAL_DATA = ROOT / "additional_data"

FY_START = pd.Timestamp("2025-04-01 00:00:00")
FY_END = pd.Timestamp("2026-04-01 00:00:00")
FREQUENCY = "15min"
STEP_HOURS = 0.25
EXPECTED_SNAPSHOTS = 35_040
# The supplied "equal hourly-to-15min" files allocate each hourly MWh value
# equally across four quarters. Convert those quarter-hour energy shares back
# to average MW; snapshot weights perform the MW-to-MWh conversion in PyPSA.
DEMAND_POWER_SCALE = 4.0

DEMAND_FILES = {
    2025: ADDITIONAL_DATA
    / "Tamilnadu_Telangana_Yearly Demand Profile_2025__Hourly_Demand_Met_in_MW__equal__hourly-to-15min.csv",
    2026: ADDITIONAL_DATA
    / "Tamilnadu_Telangana_Yearly Demand Profile_2026__Hourly_Demand_Met_in_MW__equal__hourly-to-15min.csv",
}

STATIC_FILES = (
    "buses.csv",
    "carriers.csv",
    "component_metadata.csv",
    "crs.json",
    "loads.csv",
    "meta.json",
    "operational_technology_capacities.csv",
    "storage_units.csv",
)


def read_tamil_nadu_demand(path: Path, year: int) -> pd.Series:
    """Read one calendar-year file and construct its quarter-hour timestamps."""
    frame = pd.read_csv(path)
    required = {"quarter", "State", "Date", "Hourly Demand Met (in MW)"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")

    state = f"Tamil Nadu - {year}"
    frame = frame.loc[frame["State"].eq(state)].copy()
    if frame.empty:
        raise ValueError(f"No rows found for {state!r} in {path.name}")

    quarter = pd.to_numeric(frame["quarter"], errors="raise").astype(int)
    if not quarter.between(1, 4).all():
        raise ValueError(f"Quarter numbers outside 1-4 in {path.name}")

    hour = pd.to_datetime(
        frame["Date"].str.strip() + f"-{year}",
        format="%d-%b %I%p-%Y",
        errors="raise",
    )
    timestamp = hour + pd.to_timedelta((quarter - 1) * 15, unit="min")
    values = (
        pd.to_numeric(frame["Hourly Demand Met (in MW)"], errors="raise")
        * DEMAND_POWER_SCALE
    )
    return pd.Series(values.to_numpy(), index=timestamp, name="Tamil_Nadu_demand")


def build_fiscal_year_demand() -> pd.Series:
    demand = pd.concat(
        [read_tamil_nadu_demand(path, year) for year, path in DEMAND_FILES.items()]
    ).sort_index()
    demand = demand.loc[(demand.index >= FY_START) & (demand.index < FY_END)]

    expected_index = pd.date_range(FY_START, FY_END, freq=FREQUENCY, inclusive="left")
    if len(demand) != EXPECTED_SNAPSHOTS:
        raise ValueError(
            f"Expected {EXPECTED_SNAPSHOTS:,} fiscal-year demand rows, found {len(demand):,}"
        )
    if demand.index.has_duplicates:
        duplicates = demand.index[demand.index.duplicated()].unique()
        raise ValueError(f"Duplicate demand timestamps found: {duplicates[:5].tolist()}")
    if not demand.index.equals(expected_index):
        missing = expected_index.difference(demand.index)
        unexpected = demand.index.difference(expected_index)
        raise ValueError(
            f"Demand timestamps are not continuous; missing={missing[:5].tolist()}, "
            f"unexpected={unexpected[:5].tolist()}"
        )
    if demand.isna().any() or (demand < 0).any():
        raise ValueError("Demand contains missing or negative values")

    hourly = pd.read_csv(SOURCE_DIR / "loads-p_set.csv", index_col=0).iloc[:, 0]
    quarter_hour_blocks = demand.to_numpy().reshape(-1, 4)
    if not np.allclose(quarter_hour_blocks, quarter_hour_blocks[:, [0]]):
        raise ValueError("Supplied equal-allocation demand is not constant within each hour")
    if not np.allclose(quarter_hour_blocks[:, 0], hourly.to_numpy()):
        raise ValueError("15-minute demand does not reproduce the hourly model after unit conversion")
    return demand


def repeat_hourly_profile(filename: str) -> None:
    hourly = pd.read_csv(SOURCE_DIR / filename, index_col=0)
    if len(hourly) != 8_760:
        raise ValueError(f"Expected 8,760 rows in {filename}, found {len(hourly):,}")
    values = np.repeat(hourly.to_numpy(), 4, axis=0)
    pd.DataFrame(values, columns=hourly.columns).to_csv(OUTPUT_DIR / filename)


def convert_generator_parameters() -> None:
    generators = pd.read_csv(SOURCE_DIR / "generators.csv")

    # PyPSA expresses these durations in snapshots, so retain the same hours.
    duration_columns = ("min_up_time", "min_down_time", "up_time_before", "down_time_before")
    for column in duration_columns:
        generators[column] = generators[column] * 4

    # These limits apply between consecutive snapshots; retain the hourly rate.
    for column in ("ramp_limit_up", "ramp_limit_down"):
        generators[column] = generators[column] * STEP_HOURS

    generators.to_csv(OUTPUT_DIR / "generators.csv", index=False)


def write_snapshots(demand: pd.Series) -> None:
    snapshots = pd.DataFrame(
        {
            "snapshot": demand.index,
            "objective": STEP_HOURS,
            "stores": STEP_HOURS,
            "generators": STEP_HOURS,
        }
    )
    snapshots.to_csv(OUTPUT_DIR / "snapshots.csv")
    demand.reset_index(drop=True).to_frame().to_csv(OUTPUT_DIR / "loads-p_set.csv")


def write_network_metadata() -> None:
    network = pd.read_csv(SOURCE_DIR / "network.csv")
    network.loc[:, "name"] = "Tamil Nadu FY 2025-26 single-node UC inputs (15-minute)"
    network.to_csv(OUTPUT_DIR / "network.csv", index=False)


def write_documentation(demand: pd.Series) -> None:
    peak_time = demand.idxmax()
    text = f"""# Tamil Nadu FY 2025-26 15-minute inputs

This is the 15-minute-resolution alternative to `../tamil_nadu_ra_2025_26`.
It contains {len(demand):,} snapshots from {FY_START} through
{FY_END - pd.Timedelta(minutes=15)}. Build it reproducibly with:

```powershell
python scripts/build_15min_model.py
```

Demand is assembled from the Tamil Nadu rows of the supplied 2025 and 2026
quarter-hour CSV files, then restricted to the Indian fiscal year: April-
December 2025 followed by January-March 2026. The supplied equal-allocation
values are hourly MWh divided among four quarters, so they are multiplied by
four to recover average MW; the 0.25-hour snapshot weights then recover energy.
The resulting series exactly reproduces the original hourly load in each
quarter. The maximum demand is
{demand.max():,.4f} MW at {peak_time}.

All buses, carriers, generating and storage capacities, costs, availability
assumptions, and annual energy limits are inherited from the hourly model.
Hourly generator availability values are held constant over each set of four
quarter-hours. Snapshot objective, store, and generator weights are 0.25 hours.
Minimum up/down and prior-state durations are multiplied by four because PyPSA
stores them as snapshot counts. Normal up/down ramp limits are multiplied by
0.25 because they apply between consecutive snapshots. Start-up and shut-down
ramp limits are unchanged because they describe the transition capacity, not
an elapsed hourly ramp rate.

Load the model with:

```python
import pypsa

n = pypsa.Network("inputs/tamil_nadu_ra_2025_26_15min")
```
"""
    (OUTPUT_DIR / "DATA_DOCUMENTATION.md").write_text(text, encoding="utf-8")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename in STATIC_FILES:
        shutil.copy2(SOURCE_DIR / filename, OUTPUT_DIR / filename)

    demand = build_fiscal_year_demand()
    write_snapshots(demand)
    convert_generator_parameters()
    repeat_hourly_profile("generators-p_max_pu.csv")
    repeat_hourly_profile("technology-p_max-pu.csv")
    write_network_metadata()
    write_documentation(demand)

    print(f"Wrote {OUTPUT_DIR}")
    print(f"Snapshots: {len(demand):,} ({demand.index[0]} to {demand.index[-1]})")
    print(f"Peak demand: {demand.max():,.4f} MW at {demand.idxmax()}")


if __name__ == "__main__":
    main()
