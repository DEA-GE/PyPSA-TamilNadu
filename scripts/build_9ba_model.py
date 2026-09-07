"""Build the nine-balancing-area hourly PyPSA unit-commitment model.

The script treats files in ``9_BA`` as data sources only.  It starts from the
existing single-node CSV-folder model, spatially allocates its components, and
writes an independent model to ``9_BA/model``.
"""

from __future__ import annotations

import re
import shutil
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
BASE_MODEL = ROOT / "inputs" / "tamil_nadu_ra_2025_26"
SOURCE_DIR = ROOT / "9_BA"
OUTPUT_DIR = SOURCE_DIR / "model"
AREA_FILE = SOURCE_DIR / "Balancing_areas.txt"
GRID_FILE = SOURCE_DIR / "Grid_capacity.txt"
DEMAND_WORKBOOK = SOURCE_DIR / "tamil_nadu_balancing_area_demand.xlsx"
PROFILE_INPUT = SOURCE_DIR / "renewable_profiles.csv"
ADDITIONAL_CAPACITY_FILE = SOURCE_DIR / "additional_capacity_2026_07.csv"
OBSERVED_GENERATION_FILE = (
    ROOT
    / "additional_data"
    / "TamilNadu_FY2025-2026_Electricity_Power_Generation_daily.xlsx"
)
CAPACITY_WORKBOOK = (
    ROOT
    / "additional_data"
    / "TamilNadu_ICED_all_source_1782910007272_prayas_OSM_validated.xlsx"
)

DISTRICT_ALIASES = {
    "Thiruvallur": "Tiruvallur",
    "Nilgiris": "The Nilgiris",
    "Kanyakumari": "Kanniyakumari",
    "Kanchipuram": "Kancheepuram",
    "Sivaganga": "Sivagangai",
    "Thoothukkudi": "Thoothukudi",
    "Tuticorin": "Thoothukudi",
}
MECHANISM_CARRIERS = {"market_import", "unserved_energy"}
EXPECTED_ADDITIONAL_CAPACITY_MW = {
    "diesel": 211.70,
    "solar": 212.33,
    "wind": 139.9595,
}
EXPECTED_UNASSIGNED_CAPACITY_MW = 49.31
EXPECTED_JULY_2026_TOTAL_CAPACITY_MW = 48_380.41
FY_START = pd.Timestamp("2025-04-01")
FY_END = pd.Timestamp("2026-03-31")
OIL_GAS_BUDGET_CONSTRAINT = "observed_fy2025_26_oil_gas_generation_target"


def slugify(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()


def read_area_mapping() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in AREA_FILE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "---" in line or "District" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) >= 2:
            mapping[cells[0]] = cells[1]
    if len(mapping) != 38:
        raise ValueError(f"Expected 38 district mappings, found {len(mapping)}")
    return mapping


def canonical_district(district: object) -> str:
    value = str(district).strip()
    return DISTRICT_ALIASES.get(value, value)


def read_grid() -> pd.DataFrame:
    records: list[dict[str, object]] = []
    lines = GRID_FILE.read_text(encoding="utf-8").splitlines()
    for line in lines[1:]:
        if not line.strip():
            continue
        cells = [cell.strip() for cell in line.split("\t")]
        if len(cells) != 3:
            raise ValueError(f"Could not parse grid row: {line!r}")
        values = [float(value) for value in re.findall(r"\d+(?:\.\d+)?", cells[2])]
        if not values:
            raise ValueError(f"No capacity found in grid row: {line!r}")
        capacity_gw = float(np.mean(values))
        records.append(
            {
                "name": f"{slugify(cells[0])}__{slugify(cells[1])}",
                "bus0": cells[0],
                "bus1": cells[1],
                "p_nom": capacity_gw * 1_000.0,
                "p_min_pu": -1.0,
                "efficiency": 1.0,
                "carrier": "AC",
                "marginal_cost": 0.0,
                "source_capacity_text": cells[2],
                "capacity_method": "value" if len(values) == 1 else "range midpoint",
            }
        )
    grid = pd.DataFrame(records)
    if len(grid) != 22:
        raise ValueError(f"Expected 22 grid corridors, found {len(grid)}")
    return grid


def read_demand_shares() -> pd.DataFrame:
    energy = pd.read_excel(
        DEMAND_WORKBOOK, sheet_name="FY2025-26 Calibration", header=7, nrows=9
    )
    peak = pd.read_excel(
        DEMAND_WORKBOOK, sheet_name="FY2025-26 Calibration", header=21, nrows=9
    )
    energy = energy.iloc[:, :5].copy()
    peak = peak.iloc[:, :5].copy()
    energy.columns = ["balancing_area", "source_energy_twh", "energy_share", "scaled_energy_twh", "scaled_energy_mu"]
    peak.columns = ["balancing_area", "source_peak_gw", "peak_share", "scaled_peak_gw", "scaled_peak_mw"]
    result = energy.merge(peak, on="balancing_area", validate="one_to_one")
    result["balancing_area"] = result["balancing_area"].astype(str).str.strip()
    if not np.isclose(result["energy_share"].sum(), 1.0):
        raise ValueError("Demand energy shares do not sum to one")
    if not np.isclose(result["peak_share"].sum(), 1.0):
        raise ValueError("Demand peak shares do not sum to one")
    return result


def read_oil_gas_energy_budget() -> pd.DataFrame:
    """Create the FY oil-and-gas daily budget from the observed generation file."""
    raw = pd.read_excel(
        OBSERVED_GENERATION_FILE,
        sheet_name="Electricity_Power_Generation_",
    )
    selected = raw.loc[
        raw["State"].eq("Tamil Nadu")
        & raw["Parameter"].eq("Oil & Gas - Generation (in MU)")
    ]
    if len(selected) != 1:
        raise ValueError(
            "Expected exactly one Tamil Nadu Oil & Gas generation row in the "
            "observed workbook"
        )

    values: dict[pd.Timestamp, float] = {}
    for column, value in selected.iloc[0].items():
        try:
            date = pd.Timestamp(column).normalize()
        except (TypeError, ValueError):
            continue
        if FY_START <= date <= FY_END:
            values[date] = pd.to_numeric(value, errors="coerce")

    expected_dates = pd.date_range(FY_START, FY_END, freq="D", name="date")
    daily = pd.Series(values, name="observed_generation_mu").sort_index()
    daily.index.name = "date"
    if not daily.index.equals(expected_dates):
        raise ValueError("Oil-and-gas observations do not cover every FY2025-26 day")
    if daily.isna().any() or (daily < 0.0).any():
        raise ValueError("Oil-and-gas observations must be non-negative and complete")

    annual_mu = float(daily.sum())
    if annual_mu <= 0.0:
        raise ValueError("Oil-and-gas annual generation must be positive")
    budget = daily.to_frame().reset_index()
    budget["annual_share"] = budget["observed_generation_mu"] / annual_mu
    budget["daily_energy_target_mwh"] = budget["observed_generation_mu"] * 1_000.0
    if not np.isclose(budget["annual_share"].sum(), 1.0):
        raise AssertionError("Oil-and-gas daily shares do not sum to one")
    return budget


def join_unique(values: pd.Series) -> str:
    items = [str(value).strip() for value in values if pd.notna(value) and str(value).strip()]
    return " | ".join(dict.fromkeys(items))


def read_district_allocations(
    metadata: pd.DataFrame, district_to_area: dict[str, str]
) -> tuple[dict[str, list[dict[str, object]]], pd.DataFrame]:
    allocation = pd.read_excel(CAPACITY_WORKBOOK, sheet_name="District Allocation")
    source_district_column = next(
        (
            column
            for column in ["Original District", "Source District (cleaned)"]
            if column in allocation.columns
        ),
        None,
    )
    if source_district_column is None:
        raise ValueError("District Allocation has no source-district column")
    allocation = allocation.rename(columns={source_district_column: "Original District"})
    allocation = allocation.loc[
        allocation["Commissioning Group"].eq("operational")
    ].copy()
    allocation["Original Row"] = allocation["Original Row"].astype(int)

    metadata_by_row = metadata.set_index("excel_row", verify_integrity=True)
    allocation_rows = set(allocation["Original Row"])
    metadata_rows = set(metadata_by_row.index)
    if allocation_rows != metadata_rows:
        missing = sorted(metadata_rows - allocation_rows)
        extra = sorted(allocation_rows - metadata_rows)
        raise ValueError(
            f"District allocation rows differ from operational metadata; "
            f"missing={missing}, extra={extra}"
        )

    allocation["component_id"] = allocation["Original Row"].map(
        metadata_by_row["component_id"]
    )
    allocation["allocated_district"] = allocation["Allocated District"].map(
        canonical_district
    )
    unknown_districts = sorted(
        set(allocation["allocated_district"]) - set(district_to_area)
    )
    if unknown_districts:
        raise ValueError(f"Allocated districts missing from area map: {unknown_districts}")
    allocation["balancing_area"] = allocation["allocated_district"].map(
        district_to_area
    )

    for excel_row, rows in allocation.groupby("Original Row", sort=False):
        meta = metadata_by_row.loc[excel_row]
        if not rows["Source"].eq(meta["source"]).all():
            raise ValueError(f"Source mismatch for workbook row {excel_row}")
        if not np.allclose(
            rows["Original Capacity (MW)"], float(meta["capacity_mw"])
        ):
            raise ValueError(f"Original capacity mismatch for workbook row {excel_row}")
        if not np.isclose(rows["Allocation Share"].sum(), 1.0):
            raise ValueError(f"Allocation shares do not sum to one for row {excel_row}")
        if not np.isclose(rows["Allocated Capacity (MW)"].sum(), meta["capacity_mw"]):
            raise ValueError(f"Allocated capacity does not reconcile for row {excel_row}")

    grouped = allocation.groupby(
        ["component_id", "balancing_area"], sort=False, as_index=False
    ).agg(
        allocation_share=("Allocation Share", "sum"),
        allocated_capacity_mw=("Allocated Capacity (MW)", "sum"),
        allocated_districts=("allocated_district", join_unique),
        allocation_method=("Allocation Method", join_unique),
        allocation_confidence=("Confidence", join_unique),
        allocation_source_url=("Source URL", join_unique),
        allocation_notes=("Notes", join_unique),
    )
    targets = {
        str(component_id): rows.drop(columns="component_id").to_dict("records")
        for component_id, rows in grouped.groupby("component_id", sort=False)
    }

    export = allocation.rename(
        columns={
            "Original Row": "excel_row",
            "Source": "source",
            "Name of Power Plant": "plant_name",
            "Original District": "original_district",
            "Original Capacity (MW)": "original_capacity_mw",
            "Allocation Share": "allocation_share",
            "Allocated Capacity (MW)": "allocated_capacity_mw",
            "Allocation Method": "allocation_method",
            "Source URL": "source_url",
            "Confidence": "confidence",
            "Notes": "notes",
        }
    )[
        [
            "component_id",
            "excel_row",
            "source",
            "plant_name",
            "original_district",
            "allocated_district",
            "balancing_area",
            "original_capacity_mw",
            "allocation_share",
            "allocated_capacity_mw",
            "allocation_method",
            "source_url",
            "confidence",
            "notes",
        ]
    ]
    return targets, export


def scale_component(row: pd.Series, share: float) -> pd.Series:
    result = row.copy()
    for column in ["p_nom", "p_init", "start_up_cost", "shut_down_cost", "e_sum_max"]:
        if column in result.index and pd.notna(result[column]) and np.isfinite(result[column]):
            result[column] = float(result[column]) * share
    return result


def allocated_name(original: str, area: str, split: bool) -> str:
    return f"{original}__{slugify(area)}" if split else original


def allocate_source_components(
    components: pd.DataFrame,
    metadata: pd.DataFrame,
    component_type: str,
    district_allocations: dict[str, list[dict[str, object]]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    source_metadata = metadata.loc[metadata["component_type"].eq(component_type)].set_index("component_id")
    output_rows: list[pd.Series] = []
    metadata_rows: list[dict[str, object]] = []
    for _, component in components.iterrows():
        original_name = str(component["name"])
        meta = source_metadata.loc[original_name]
        targets = district_allocations[original_name]
        for target in targets:
            area = str(target["balancing_area"])
            share = float(target["allocation_share"])
            allocated = scale_component(component, share)
            allocated["name"] = allocated_name(original_name, area, len(targets) > 1)
            allocated["bus"] = area
            output_rows.append(allocated)
            item = meta.to_dict()
            item.update(
                component_id=allocated["name"],
                original_component_id=original_name,
                bus=area,
                allocated_districts=target["allocated_districts"],
                allocation_method=target["allocation_method"],
                allocation_confidence=target["allocation_confidence"],
                allocation_source_url=target["allocation_source_url"],
                allocation_notes=target["allocation_notes"],
                allocation_share=share,
                original_capacity_mw=float(meta["capacity_mw"]),
                capacity_mw=float(allocated["p_nom"]),
            )
            metadata_rows.append(item)
    return pd.DataFrame(output_rows, columns=components.columns), pd.DataFrame(metadata_rows)


def allocate_mechanisms(
    mechanisms: pd.DataFrame, demand: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    output_rows: list[pd.Series] = []
    metadata_rows: list[dict[str, object]] = []
    for _, component in mechanisms.iterrows():
        original_name = str(component["name"])
        for _, area_row in demand.iterrows():
            area = str(area_row["balancing_area"])
            share = float(area_row["energy_share"])
            allocated = scale_component(component, share)
            allocated["name"] = allocated_name(original_name, area, True)
            allocated["bus"] = area
            output_rows.append(allocated)
            metadata_rows.append(
                {
                    "component_type": "Generator",
                    "component_id": allocated["name"],
                    "original_component_id": original_name,
                    "source": component["carrier"],
                    "plant_name": original_name,
                    "district": "statewide mechanism",
                    "bus": area,
                    "allocation_method": "demand energy share",
                    "allocation_share": share,
                    "original_capacity_mw": float(component["p_nom"]),
                    "capacity_mw": float(allocated["p_nom"]),
                }
            )
    return pd.DataFrame(output_rows, columns=mechanisms.columns), pd.DataFrame(metadata_rows)


def read_additional_capacity(
    district_to_area: dict[str, str],
) -> pd.DataFrame:
    """Read and validate the auditable July-2026 capacity overlay."""
    overlay = pd.read_csv(ADDITIONAL_CAPACITY_FILE)
    required = {
        "record_id",
        "technology",
        "carrier",
        "plant_name",
        "district",
        "capacity_mw",
        "unit_count",
        "allocation_status",
        "include_in_model",
        "allocation_method",
        "confidence",
        "source_reference",
        "notes",
    }
    missing = sorted(required - set(overlay.columns))
    if missing:
        raise ValueError(f"Additional-capacity file is missing columns: {missing}")
    if overlay["record_id"].duplicated().any():
        duplicates = overlay.loc[overlay["record_id"].duplicated(), "record_id"].tolist()
        raise ValueError(f"Duplicate additional-capacity record IDs: {duplicates}")

    boolean_values = overlay["include_in_model"].astype(str).str.strip().str.lower()
    if not boolean_values.isin({"true", "false"}).all():
        raise ValueError("include_in_model must contain only true or false")
    overlay["include_in_model"] = boolean_values.eq("true")
    overlay["capacity_mw"] = pd.to_numeric(overlay["capacity_mw"], errors="raise")
    overlay["unit_count"] = pd.to_numeric(
        overlay["unit_count"], errors="raise", downcast="integer"
    )
    if (overlay["capacity_mw"] <= 0).any():
        raise ValueError("Every additional-capacity record must have positive capacity")

    modeled = overlay["include_in_model"]
    if (overlay.loc[modeled, "unit_count"] < 1).any():
        raise ValueError("Modeled additional capacity must have at least one unit")
    if (overlay.loc[~modeled, "unit_count"] != 0).any():
        raise ValueError("Unassigned reporting differences must have zero units")

    overlay["allocated_district"] = overlay["district"].map(canonical_district)
    unknown_districts = sorted(
        set(overlay.loc[modeled, "allocated_district"]) - set(district_to_area)
    )
    if unknown_districts:
        raise ValueError(
            f"Additional-capacity districts missing from area map: {unknown_districts}"
        )
    overlay["balancing_area"] = overlay["allocated_district"].map(district_to_area)

    modeled_totals = (
        overlay.loc[modeled].groupby("carrier")["capacity_mw"].sum().sort_index()
    )
    expected_totals = pd.Series(EXPECTED_ADDITIONAL_CAPACITY_MW).sort_index()
    pd.testing.assert_series_equal(
        modeled_totals,
        expected_totals,
        check_names=False,
        rtol=0.0,
        atol=1e-6,
    )
    unassigned_total = float(overlay.loc[~modeled, "capacity_mw"].sum())
    if not np.isclose(unassigned_total, EXPECTED_UNASSIGNED_CAPACITY_MW):
        raise ValueError(
            "Unassigned reporting difference does not reconcile: "
            f"{unassigned_total} MW"
        )
    return overlay


def build_additional_generators(
    overlay: pd.DataFrame,
    original_generators: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Convert modeled overlay records to PyPSA generators and provenance rows."""
    modeled = overlay.loc[overlay["include_in_model"]].copy()
    template_carrier = {"diesel": "oil_gas", "solar": "solar", "wind": "wind"}
    templates = {
        carrier: original_generators.loc[
            original_generators["carrier"].eq(source_carrier)
        ].iloc[0]
        for carrier, source_carrier in template_carrier.items()
    }
    output_rows: list[pd.Series] = []
    metadata_rows: list[dict[str, object]] = []
    allocation_rows: list[dict[str, object]] = []

    for _, record in modeled.iterrows():
        carrier = str(record["carrier"])
        unit_count = int(record["unit_count"])
        unit_capacity_mw = float(record["capacity_mw"]) / unit_count
        template = templates[carrier]
        start_up_cost_per_mw = float(template["start_up_cost"]) / float(
            template["p_nom"]
        )
        shut_down_cost_per_mw = float(template["shut_down_cost"]) / float(
            template["p_nom"]
        )

        for unit_sequence in range(1, unit_count + 1):
            component = template.copy()
            suffix = f"__unit_{unit_sequence:02d}" if unit_count > 1 else ""
            component_id = f"additional_2026_07__{record['record_id']}{suffix}"
            component["name"] = component_id
            component["bus"] = record["balancing_area"]
            component["p_nom"] = unit_capacity_mw
            component["carrier"] = carrier
            if carrier == "diesel":
                component["start_up_cost"] = start_up_cost_per_mw * unit_capacity_mw
                component["shut_down_cost"] = shut_down_cost_per_mw * unit_capacity_mw
            output_rows.append(component)

            allocation_share = 1.0 / unit_count
            common = {
                "component_id": component_id,
                "original_component_id": str(record["record_id"]),
                "source": str(record["technology"]),
                "plant_name": str(record["plant_name"]),
                "bus": str(record["balancing_area"]),
                "allocation_method": str(record["allocation_method"]),
                "allocation_confidence": str(record["confidence"]),
                "allocation_source_url": str(record["source_reference"]),
                "allocation_notes": str(record["notes"]),
                "allocation_share": allocation_share,
                "original_capacity_mw": float(record["capacity_mw"]),
                "capacity_mw": unit_capacity_mw,
            }
            metadata_rows.append(
                {
                    "component_type": "Generator",
                    **common,
                    "excel_row": np.nan,
                    "record_kind": "july_2026_capacity_overlay",
                    "unit_sequence": unit_sequence,
                    "state": "Tamil Nadu",
                    "district": str(record["allocated_district"]),
                    "commissioning_date": np.nan,
                    "implementing_agency": np.nan,
                    "duplicate_group_size": unit_count,
                    "allocated_districts": str(record["allocated_district"]),
                }
            )
            allocation_rows.append(
                {
                    "component_id": component_id,
                    "excel_row": np.nan,
                    "source": str(record["technology"]),
                    "plant_name": str(record["plant_name"]),
                    "original_district": str(record["district"]),
                    "allocated_district": str(record["allocated_district"]),
                    "balancing_area": str(record["balancing_area"]),
                    "original_capacity_mw": float(record["capacity_mw"]),
                    "allocation_share": allocation_share,
                    "allocated_capacity_mw": unit_capacity_mw,
                    "allocation_method": str(record["allocation_method"]),
                    "source_url": str(record["source_reference"]),
                    "confidence": str(record["confidence"]),
                    "notes": str(record["notes"]),
                }
            )

    generators = pd.DataFrame(output_rows, columns=original_generators.columns)
    if generators["name"].duplicated().any():
        raise AssertionError("Additional-capacity generator names are not unique")
    return generators, pd.DataFrame(metadata_rows), pd.DataFrame(allocation_rows)


def build_loads(demand: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base_load = pd.read_csv(BASE_MODEL / "loads-p_set.csv", index_col=0).iloc[:, 0].astype(float)
    mean = float(base_load.mean())
    peak = float(base_load.max())
    if np.isclose(mean, peak):
        raise ValueError("Cannot construct area profiles from a flat state demand profile")

    loads: list[dict[str, str]] = []
    load_series: dict[str, pd.Series] = {}
    allocation = demand.copy()
    allocation["modeled_annual_energy_mwh"] = 0.0
    allocation["modeled_peak_mw"] = 0.0
    allocation["affine_slope"] = 0.0
    allocation["affine_intercept_mw"] = 0.0
    for index, area_row in allocation.iterrows():
        area = str(area_row["balancing_area"])
        energy_share = float(area_row["energy_share"])
        peak_share = float(area_row["peak_share"])
        slope = (peak_share * peak - energy_share * mean) / (peak - mean)
        intercept = energy_share * mean - slope * mean
        series = slope * base_load + intercept
        if (series < -1e-9).any():
            raise ValueError(f"Synthetic demand became negative for {area}")
        load_name = f"{slugify(area)}_demand"
        loads.append({"name": load_name, "bus": area, "carrier": "electricity"})
        load_series[load_name] = series
        allocation.loc[index, "modeled_annual_energy_mwh"] = float(series.sum())
        allocation.loc[index, "modeled_peak_mw"] = float(series.max())
        allocation.loc[index, "affine_slope"] = slope
        allocation.loc[index, "affine_intercept_mw"] = intercept

    time_series = pd.DataFrame(load_series, index=base_load.index)
    if not np.allclose(time_series.sum(axis=1), base_load, atol=1e-6):
        raise AssertionError("Balancing-area loads do not preserve state demand")
    return pd.DataFrame(loads), time_series, allocation


def placeholder_profiles(
    areas: list[str], snapshots: pd.Series, generators: pd.DataFrame, metadata: pd.DataFrame
) -> pd.DataFrame:
    required_columns = [f"{slugify(area)}_{technology}" for area in areas for technology in ("solar", "wind")]
    if PROFILE_INPUT.exists():
        profiles = pd.read_csv(PROFILE_INPUT)
        if "snapshot" not in profiles.columns:
            raise ValueError(f"{PROFILE_INPUT} must contain a snapshot column")
        if list(profiles["snapshot"].astype(str)) != list(snapshots.astype(str)):
            raise ValueError(f"{PROFILE_INPUT} snapshots do not match the hourly model")
        missing = sorted(set(required_columns) - set(profiles.columns))
        if missing:
            raise ValueError(f"Missing renewable profile columns: {missing}")
        values = profiles[required_columns]
        if values.isna().any().any() or ((values < 0.0) | (values > 1.0)).any().any():
            raise ValueError("Renewable profiles must contain values from zero to one")
        return profiles[["snapshot", *required_columns]]

    technology = pd.read_csv(BASE_MODEL / "technology-p_max-pu.csv", index_col=0)
    meta = metadata.set_index("component_id")
    solar = generators.loc[generators["carrier"].eq("solar"), ["name", "p_nom"]].copy()
    solar["profile_kind"] = solar["name"].map(meta["plant_name"]).astype(str).str.contains(
        r"rooftop|off\s*-?\s*grid", case=False, regex=True
    )
    dre_mw = float(solar.loc[solar["profile_kind"], "p_nom"].sum())
    utility_mw = float(solar.loc[~solar["profile_kind"], "p_nom"].sum())
    solar_profile = (
        technology["solar_DRE_rooftop"] * dre_mw
        + technology["solar_utility"] * utility_mw
    ) / (dre_mw + utility_mw)
    profiles = pd.DataFrame({"snapshot": snapshots.astype(str)})
    for area in areas:
        profiles[f"{slugify(area)}_solar"] = solar_profile.to_numpy()
        profiles[f"{slugify(area)}_wind"] = technology["wind_fleet"].to_numpy()
    profiles.to_csv(PROFILE_INPUT, index=False)
    return profiles


def build_generator_profiles(
    generators: pd.DataFrame,
    expanded_metadata: pd.DataFrame,
    area_profiles: pd.DataFrame,
) -> pd.DataFrame:
    base = pd.read_csv(BASE_MODEL / "generators-p_max_pu.csv", index_col=0)
    meta = expanded_metadata.set_index("component_id")
    output: dict[str, pd.Series | np.ndarray] = {}
    for _, generator in generators.iterrows():
        name = str(generator["name"])
        carrier = str(generator["carrier"])
        if carrier in {"solar", "wind"}:
            area = str(generator["bus"])
            output[name] = area_profiles[f"{slugify(area)}_{carrier}"].to_numpy()
        elif name in meta.index:
            original = str(meta.loc[name, "original_component_id"])
            if original in base.columns:
                output[name] = base[original]
    return pd.DataFrame(output, index=base.index)


def validate_capacity_conservation(
    original_generators: pd.DataFrame,
    original_storage: pd.DataFrame,
    generators: pd.DataFrame,
    storage: pd.DataFrame,
) -> None:
    original = original_generators.loc[
        ~original_generators["carrier"].isin(MECHANISM_CARRIERS)
    ].groupby("carrier")["p_nom"].sum()
    original = original.add(original_storage.groupby("carrier")["p_nom"].sum(), fill_value=0.0)
    allocated = generators.loc[
        ~generators["carrier"].isin(MECHANISM_CARRIERS)
    ].groupby("carrier")["p_nom"].sum()
    allocated = allocated.add(storage.groupby("carrier")["p_nom"].sum(), fill_value=0.0)
    pd.testing.assert_series_equal(
        original.sort_index(), allocated.sort_index(), check_names=False, rtol=1e-10, atol=1e-7
    )


def main() -> None:
    district_to_area = read_area_mapping()
    grid = read_grid()
    demand = read_demand_shares()
    areas = demand["balancing_area"].tolist()
    if set(areas) != set(district_to_area.values()):
        raise ValueError("Balancing areas differ between demand and district inputs")
    if set(grid["bus0"]) | set(grid["bus1"]) != set(areas):
        raise ValueError("Grid endpoints differ from the nine balancing areas")

    metadata = pd.read_csv(BASE_MODEL / "component_metadata.csv")
    original_generators = pd.read_csv(BASE_MODEL / "generators.csv")
    original_storage = pd.read_csv(BASE_MODEL / "storage_units.csv")
    district_allocations, district_allocation_export = read_district_allocations(
        metadata, district_to_area
    )

    source_generators = original_generators.loc[
        ~original_generators["carrier"].isin(MECHANISM_CARRIERS)
    ].copy()
    mechanisms = original_generators.loc[
        original_generators["carrier"].isin(MECHANISM_CARRIERS)
    ].copy()
    generators, generator_metadata = allocate_source_components(
        source_generators, metadata, "Generator", district_allocations
    )
    allocated_mechanisms, mechanism_metadata = allocate_mechanisms(mechanisms, demand)
    generators = pd.concat([generators, allocated_mechanisms], ignore_index=True)
    generator_metadata = pd.concat(
        [generator_metadata, mechanism_metadata], ignore_index=True, sort=False
    )
    storage, storage_metadata = allocate_source_components(
        original_storage, metadata, "StorageUnit", district_allocations
    )
    expanded_metadata = pd.concat(
        [generator_metadata, storage_metadata], ignore_index=True, sort=False
    )
    validate_capacity_conservation(
        original_generators, original_storage, generators, storage
    )

    additional_capacity = read_additional_capacity(district_to_area)
    additional_generators, additional_metadata, additional_allocation = (
        build_additional_generators(additional_capacity, original_generators)
    )
    if set(additional_generators["name"]) & set(generators["name"]):
        raise AssertionError("Additional-capacity generators duplicate base component names")
    generators = pd.concat(
        [generators, additional_generators], ignore_index=True, sort=False
    )
    expanded_metadata = pd.concat(
        [expanded_metadata, additional_metadata], ignore_index=True, sort=False
    )
    district_allocation_export = pd.concat(
        [district_allocation_export, additional_allocation],
        ignore_index=True,
        sort=False,
    )

    loads, load_time_series, demand_allocation = build_loads(demand)
    snapshots_table = pd.read_csv(BASE_MODEL / "snapshots.csv")
    snapshots = snapshots_table["snapshot"]
    oil_gas_budget = read_oil_gas_energy_budget()
    oil_gas_annual_target_mwh = float(
        oil_gas_budget["daily_energy_target_mwh"].sum()
    )
    if not generators["carrier"].eq("oil_gas").any():
        raise ValueError("Cannot apply the oil-and-gas budget without oil-and-gas generators")
    global_constraints = pd.DataFrame(
        [
            {
                "name": OIL_GAS_BUDGET_CONSTRAINT,
                "type": "operational_limit",
                "carrier_attribute": "oil_gas",
                "sense": "==",
                "constant": oil_gas_annual_target_mwh,
            }
        ]
    )
    area_profiles = placeholder_profiles(
        areas, snapshots, generators, expanded_metadata
    )
    generator_profiles = build_generator_profiles(
        generators, expanded_metadata, area_profiles
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    network = pd.read_csv(BASE_MODEL / "network.csv")
    network.loc[0, "name"] = (
        "Tamil Nadu FY 2025-26 nine-balancing-area hourly UC inputs "
        "with July 2026 capacity overlay"
    )
    network.to_csv(OUTPUT_DIR / "network.csv", index=False)
    pd.DataFrame({"name": areas, "v_nom": 230.0, "carrier": "AC"}).to_csv(
        OUTPUT_DIR / "buses.csv", index=False
    )
    grid.drop(columns=["source_capacity_text", "capacity_method"]).to_csv(
        OUTPUT_DIR / "links.csv", index=False
    )
    grid[["name", "bus0", "bus1", "p_nom", "source_capacity_text", "capacity_method"]].to_csv(
        OUTPUT_DIR / "transmission_capacity_metadata.csv", index=False
    )
    loads.to_csv(OUTPUT_DIR / "loads.csv", index=False)
    load_time_series.to_csv(OUTPUT_DIR / "loads-p_set.csv")
    generators.to_csv(OUTPUT_DIR / "generators.csv", index=False)
    generator_profiles.to_csv(OUTPUT_DIR / "generators-p_max_pu.csv")
    storage.to_csv(OUTPUT_DIR / "storage_units.csv", index=False)
    snapshots_table.to_csv(OUTPUT_DIR / "snapshots.csv", index=False)
    global_constraints.to_csv(OUTPUT_DIR / "global_constraints.csv", index=False)
    oil_gas_budget.to_csv(
        OUTPUT_DIR / "oil_gas_daily_energy_budget.csv",
        index=False,
        date_format="%Y-%m-%d",
    )
    pd.DataFrame(
        [
            {
                "source": OBSERVED_GENERATION_FILE.name,
                "fiscal_year": "FY2025-26",
                "observed_generation_mu": oil_gas_annual_target_mwh / 1_000.0,
                "annual_energy_target_mwh": oil_gas_annual_target_mwh,
                "constraint_name": OIL_GAS_BUDGET_CONSTRAINT,
                "constraint_sense": "==",
            }
        ]
    ).to_csv(OUTPUT_DIR / "oil_gas_energy_budget_summary.csv", index=False)
    expanded_metadata.to_csv(OUTPUT_DIR / "component_metadata.csv", index=False)
    district_allocation_export.to_csv(
        OUTPUT_DIR / "district_capacity_allocation.csv", index=False
    )
    demand_allocation.to_csv(OUTPUT_DIR / "demand_allocation.csv", index=False)
    additional_capacity.to_csv(
        OUTPUT_DIR / "additional_capacity_2026_07.csv", index=False
    )

    installed = pd.concat(
        [
            generators.loc[~generators["carrier"].isin(MECHANISM_CARRIERS), ["bus", "carrier", "p_nom"]],
            storage[["bus", "carrier", "p_nom"]],
        ],
        ignore_index=True,
    )
    installed_by_area = installed.groupby(
        ["bus", "carrier"], as_index=False
    )["p_nom"].sum().rename(
        columns={"p_nom": "capacity_mw"}
    )
    installed_by_area.to_csv(
        OUTPUT_DIR / "capacity_by_balancing_area.csv", index=False
    )
    modeled_total_mw = float(installed_by_area["capacity_mw"].sum())
    overlay_total_mw = float(additional_generators["p_nom"].sum())
    baseline_total_mw = modeled_total_mw - overlay_total_mw
    unassigned_total_mw = float(
        additional_capacity.loc[
            ~additional_capacity["include_in_model"], "capacity_mw"
        ].sum()
    )
    if not np.isclose(
        modeled_total_mw + unassigned_total_mw,
        EXPECTED_JULY_2026_TOTAL_CAPACITY_MW,
    ):
        raise AssertionError(
            "Modeled capacity plus the excluded reporting difference does not "
            "reconcile to the July 2026 comparison total"
        )
    pd.DataFrame(
        [
            {
                "item": "validated_workbook_baseline",
                "capacity_mw": baseline_total_mw,
                "treatment": "modeled",
            },
            {
                "item": "july_2026_capacity_overlay",
                "capacity_mw": overlay_total_mw,
                "treatment": "modeled",
            },
            {
                "item": "updated_pypsa_installed_capacity",
                "capacity_mw": modeled_total_mw,
                "treatment": "modeled total",
            },
            {
                "item": "cea_mnre_reporting_difference",
                "capacity_mw": unassigned_total_mw,
                "treatment": "excluded unassigned",
            },
            {
                "item": "july_2026_comparison_total",
                "capacity_mw": EXPECTED_JULY_2026_TOTAL_CAPACITY_MW,
                "treatment": "reconciled total",
            },
        ]
    ).to_csv(OUTPUT_DIR / "capacity_reconciliation_2026_07.csv", index=False)

    carriers = pd.read_csv(BASE_MODEL / "carriers.csv")
    missing_carriers = sorted(set(generators["carrier"]) - set(carriers["name"]))
    if missing_carriers:
        carriers = pd.concat(
            [
                carriers,
                pd.DataFrame(
                    {
                        "name": missing_carriers,
                        "nice_name": [name.replace("_", " ") for name in missing_carriers],
                    }
                ),
            ],
            ignore_index=True,
        )
    carriers.to_csv(OUTPUT_DIR / "carriers.csv", index=False)
    for filename in ["crs.json", "technology-p_max-pu.csv"]:
        shutil.copy2(BASE_MODEL / filename, OUTPUT_DIR / filename)
    shutil.copy2(PROFILE_INPUT, OUTPUT_DIR / "renewable_profiles.csv")
    shutil.copy2(SOURCE_DIR / "README.md", OUTPUT_DIR / "DATA_DOCUMENTATION.md")

    committable = generators.loc[generators["committable"].astype(bool)]
    print(f"Wrote nine buses and {len(grid)} bidirectional transmission corridors")
    print(f"Wrote {len(loads)} hourly loads over {len(snapshots)} snapshots")
    print(f"Wrote {len(generators)} generators, including {len(committable)} committable records")
    print(f"Wrote {len(storage)} storage units")
    print(
        "Applied observed oil-and-gas energy equality target: "
        f"{oil_gas_annual_target_mwh / 1_000.0:,.2f} MU over FY2025-26"
    )
    print(
        "Applied July 2026 capacity overlay: "
        f"{additional_generators['p_nom'].sum():.4f} MW modeled and "
        f"{additional_capacity.loc[~additional_capacity['include_in_model'], 'capacity_mw'].sum():.2f} MW unassigned"
    )
    print(f"Model folder: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
