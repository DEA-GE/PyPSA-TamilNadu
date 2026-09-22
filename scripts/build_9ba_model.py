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
GRID_AVAILABILITY_FACTOR = 0.50
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
BIOMASS_ENERGY_CONSTRAINT = "cea_resource_adequacy_biomass_generation_target"
CEA_BIOMASS_CUF = 0.215
RESERVOIR_MAX_HOURS = 720.0
HYDRO_ASSET_REGISTRY = SOURCE_DIR / "hydro_asset_registry.csv"
HYDRO_RECONCILIATION = SOURCE_DIR / "hydro_fleet_reconciliation.csv"
AGRICULTURE_RESERVOIR_DATA = ROOT / "tn_reservoir_data" / "tn_reservoir_daily_FY2025_26.csv"
HYDRO_DEFAULT_EFFICIENCY = 0.90
HYDRO_DEFAULT_HEAD_M = 100.0
MCFT_M_HEAD_TO_MWH = 0.0771634
# Small operating-cost tie-breaker so solar is curtailed before zero-cost wind
# when both are otherwise equally useful to the dispatch.
SOLAR_CURTAILMENT_PREFERENCE_COST = 1.0  # currency units per MWh
# No source in the current data package states the Papanasam--Servalar tunnel
# rating. Keep the provisional water-equivalent rating explicit and easy to
# replace when a surveyed value becomes available. The transfer Link itself
# is lossless on the model's water basis.
PAPANASAM_SERVALAR_TRANSFER_CAPACITY_MW = 20.0
# The diversion-weir release is hydraulic, not an additional generator. Its
# provisional rating must accommodate observed reservoir drawdown that can
# exceed the 32 MW powerhouse throughput.
PAPANASAM_DIRECT_RELEASE_CAPACITY_MW = 100.0
PAPANASAM_LOWER_PONDAGE_BYPASS_CAPACITY_MW = 100.0
PAPANASAM_LOWER_PONDAGE_HOURS = 6.0
PAPANASAM_LOWER_PONDAGE_INITIAL_SOC_FRACTION = 0.5
# Seasonal observed reservoirs also need a non-generating outlet for measured
# irrigation releases and spill. This is a hydraulic routing limit, not plant
# capacity, and remains explicit pending surveyed outlet ratings.
PAP_OBSERVED_RELEASE_CAPACITY_MW = 250.0
METTUR_OBSERVED_RELEASE_CAPACITY_MW = 2_500.0
PERIYAR_OBSERVED_RELEASE_CAPACITY_MW = 500.0
# Reservoir inflow is derived from the validated Tamil Nadu Agriculture daily
# observations. Do not fall back to an allocation of reported hydro generation.


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
        stated_capacity_mw = float(np.mean(values))
        effective_capacity_mw = stated_capacity_mw * GRID_AVAILABILITY_FACTOR
        records.append(
            {
                "name": f"{slugify(cells[0])}__{slugify(cells[1])}",
                "bus0": cells[0],
                "bus1": cells[1],
                # Apply the deterministic transmission availability derating to
                # the Link rating so that both directional flow limits are 80%
                # of the stated corridor capacity in every snapshot.
                "p_nom": effective_capacity_mw,
                "p_min_pu": -1.0,
                "efficiency": 1.0,
                "carrier": "AC",
                "marginal_cost": 0.0,
                "stated_capacity_mw": stated_capacity_mw,
                "availability_factor": GRID_AVAILABILITY_FACTOR,
                "source_capacity_text": cells[2],
                "capacity_method": "value" if len(values) == 1 else "range midpoint",
            }
        )
    grid = pd.DataFrame(records)
    if len(grid) != 18:
        raise ValueError(f"Expected 18 grid corridors, found {len(grid)}")
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


def read_oil_gas_energy_budget(parameter="Oil & Gas") -> pd.DataFrame:
    """Create a technology's FY daily budget from the observed generation file."""
    raw = pd.read_excel(
        OBSERVED_GENERATION_FILE,
        sheet_name="Electricity_Power_Generation_",
    )
    selected = raw.loc[
        raw["State"].eq("Tamil Nadu")
        & raw["Parameter"].eq(f"{parameter} - Generation (in MU)")
    ]
    if len(selected) != 1:
        raise ValueError(
            f"Expected exactly one Tamil Nadu {parameter} generation row in the "
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
        raise ValueError(f"{parameter} observations do not cover every FY2025-26 day")
    if daily.isna().any() or (daily < 0.0).any():
        raise ValueError(f"{parameter} observations must be non-negative and complete")

    annual_mu = float(daily.sum())
    if annual_mu <= 0.0:
        raise ValueError(f"{parameter} annual generation must be positive")
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


def convert_conventional_hydro_to_reservoirs(
    generators: pd.DataFrame,
    storage: pd.DataFrame,
    metadata: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.Index, pd.DataFrame, pd.DataFrame]:
    """Use the asset registry to distinguish reservoir, cascade, and ROR hydro.

    The capacity workbook is unit based.  Reservoir aggregation is therefore
    limited to water storage: the returned electrical inventory preserves every
    official unit-level p_nom for conversion to turbine Links downstream.
    """
    hydro = generators.loc[generators["carrier"].eq("hydro")].copy()
    if hydro.empty:
        raise ValueError("Cannot build reservoir hydro without conventional hydro generators")
    if hydro["name"].duplicated().any() or set(hydro["name"]) & set(storage["name"]):
        raise ValueError("Hydro reservoir component names must be unique")

    registry = pd.read_csv(HYDRO_ASSET_REGISTRY)
    required = {"asset_id", "match_regex", "representation", "data_status", "max_hours"}
    missing = required - set(registry.columns)
    if missing:
        raise ValueError(f"Hydro asset registry is missing columns: {sorted(missing)}")
    if registry["asset_id"].duplicated().any() or registry["match_regex"].duplicated().any():
        raise ValueError("Hydro asset registry asset IDs and match patterns must be unique")

    matched_assets: list[str] = []
    for name in hydro["name"]:
        matches = registry.loc[registry["match_regex"].map(lambda pattern: bool(re.search(pattern, name)))]
        if len(matches) != 1:
            raise ValueError(f"Hydro unit {name!r} must match exactly one asset registry row")
        matched_assets.append(str(matches.iloc[0]["asset_id"]))
    hydro["asset_id"] = matched_assets
    hydro = hydro.merge(registry, on="asset_id", validate="many_to_one")
    electrical_inventory = hydro[["name", "asset_id", "bus", "p_nom", "representation"]].rename(
        columns={"name": "official_component_id", "p_nom": "official_p_nom_mw"}
    )
    # Servalar is labelled Small-Hydro in the official source workbook, but is
    # hydraulically part of this cascade. Move the official 20 MW unit from a
    # free-standing Generator into the turbine-Link inventory so its capacity
    # is neither invented nor counted twice.
    servalar_units = generators.loc[
        generators["name"].str.startswith("servalar_small_hydro_project__")
    ].copy()
    if len(servalar_units) != 1 or not np.isclose(
        float(servalar_units["p_nom"].sum()), 20.0
    ):
        raise ValueError(
            "Expected exactly one official 20 MW Servalar Small Hydro unit"
        )
    servalar_units["asset_id"] = "servalar"
    servalar_units["representation"] = "cascade_proxy"
    electrical_inventory = pd.concat(
        [
            electrical_inventory,
            servalar_units[
                ["name", "asset_id", "bus", "p_nom", "representation"]
            ].rename(
                columns={
                    "name": "official_component_id",
                    "p_nom": "official_p_nom_mw",
                }
            ),
        ],
        ignore_index=True,
    )
    # Kadamparai is an existing reversible StorageUnit in the source model,
    # rather than a Generator. It is nevertheless an official plant record and
    # must retain its four 100 MW electrical units when converted to Links.
    source_storage = storage.loc[
        storage["carrier"].eq("hydro") & storage["name"].str.startswith("kadmparai_")
    ].copy()
    if len(source_storage):
        source_storage["asset_id"] = "kadmparai"
        source_storage["representation"] = "pumped_storage"
        electrical_inventory = pd.concat([
            electrical_inventory,
            source_storage[["name", "asset_id", "bus", "p_nom", "representation"]].rename(
                columns={"name": "official_component_id", "p_nom": "official_p_nom_mw"}
            ),
        ], ignore_index=True)
    electrical_inventory["p_nom_source"] = "official_tamil_nadu_generation_inventory"
    if not hydro["representation"].isin({"reservoir", "cascade_proxy", "run_of_river"}).all():
        raise ValueError("Hydro representation must be reservoir, cascade_proxy, or run_of_river")

    # A registered asset belongs at one electrical bus.  This guards against
    # silently pooling water from geographically split source records.
    bus_counts = hydro.groupby("asset_id")["bus"].nunique()
    mixed_buses = bus_counts[bus_counts.gt(1)]
    if len(mixed_buses):
        raise ValueError(f"Hydro assets span multiple buses: {mixed_buses.to_dict()}")

    storage_assets = hydro.loc[hydro["representation"].isin({"reservoir", "cascade_proxy"})]
    grouped = storage_assets.groupby("asset_id", sort=False)
    reservoir_rows = grouped.agg(
        bus=("bus", "first"),
        p_nom=("p_nom", "sum"),
        marginal_cost=("marginal_cost", "min"),
        max_hours=("max_hours", "first"),
    ).reset_index()
    reservoir_rows["name"] = "hydro_" + reservoir_rows["asset_id"]
    reservoir_rows["p_min_pu"] = 0.0
    reservoir_rows["p_max_pu"] = 1.0
    reservoir_rows["carrier"] = "hydro"
    reservoir_rows["cyclic_state_of_charge"] = False
    reservoir_rows["efficiency_store"] = 1.0
    # Inflow is expressed as electricity-equivalent MW, so turbine conversion
    # losses are already implicit in the (currently proxy) calibration series.
    reservoir_rows["efficiency_dispatch"] = 1.0
    reservoir_rows["standing_loss"] = 0.0
    reservoir_rows["state_of_charge_initial"] = 0.0
    reservoir_rows = reservoir_rows[
        ["name", "bus", "p_nom", "p_min_pu", "p_max_pu", "carrier", "marginal_cost",
         "cyclic_state_of_charge", "max_hours", "efficiency_store", "efficiency_dispatch",
         "standing_loss", "state_of_charge_initial"]
    ]
    storage_columns = list(dict.fromkeys([*storage.columns, *reservoir_rows.columns]))
    storage = pd.concat(
        [storage.reindex(columns=storage_columns), reservoir_rows.reindex(columns=storage_columns)],
        ignore_index=True,
    )
    ror_names = hydro.loc[hydro["representation"].eq("run_of_river"), "name"]
    generators = generators.loc[~generators["carrier"].eq("hydro") | generators["name"].isin(ror_names)].copy()
    ror_mask = generators["name"].isin(ror_names)
    generators.loc[ror_mask, "carrier"] = "hydro_run_of_river"
    # No upstream-release or inflow time series is in the repository.  Keeping
    # the assets at zero availability preserves their capacity without making
    # up dispatchable water; provide measured releases to change this input.
    generators.loc[ror_mask, "p_max_pu"] = 0.0

    reservoir_names = pd.Index(reservoir_rows["name"], name="name")
    source_to_asset = hydro.set_index("name")["asset_id"]
    converted = metadata["component_id"].isin(source_to_asset.index) & ~metadata["component_id"].isin(ror_names)
    metadata = metadata.copy()
    cascade_components = hydro.loc[hydro["representation"].eq("cascade_proxy"), "name"]
    metadata.loc[converted & ~metadata["component_id"].isin(cascade_components), "component_type"] = (
        "StorageUnit (official hydro capacity aggregation)"
    )
    metadata.loc[converted & metadata["component_id"].isin(cascade_components), "component_type"] = (
        "Link (official hydro turbine capacity)"
    )
    metadata.loc[converted, "model_representation"] = metadata.loc[converted, "component_id"].map(
        lambda name: (
            f"official p_nom retained as turbine Link; hydraulic storage grouped under {source_to_asset[name]}"
            if name in set(cascade_components)
            else f"official units deliberately aggregated to reservoir StorageUnit hydro_{source_to_asset[name]}"
        )
    )
    explicit_link_assets = {"mettur", "periyar", "moyar", "papanasam"}
    explicit_link_components = metadata["component_id"].map(source_to_asset).isin(
        explicit_link_assets
    )
    metadata.loc[explicit_link_components, "component_type"] = (
        "Link (official hydro turbine capacity)"
    )
    metadata.loc[explicit_link_components, "model_representation"] = (
        "official p_nom retained as turbine Link with explicit hydraulic Store"
    )
    metadata.loc[metadata["component_id"].isin(ror_names), "model_representation"] = (
        "Run-of-river generator; zero availability pending measured upstream release/inflow"
    )
    servalar_metadata = metadata["component_id"].isin(servalar_units["name"])
    metadata.loc[servalar_metadata, "component_type"] = (
        "Link (official hydro turbine capacity)"
    )
    metadata.loc[servalar_metadata, "model_representation"] = (
        "official 20 MW p_nom retained as Servalar turbine Link"
    )
    asset_audit = registry.merge(
        hydro.groupby("asset_id", as_index=False).agg(installed_turbine_mw=("p_nom", "sum"), electrical_bus=("bus", "first")),
        on="asset_id", how="left", validate="one_to_one"
    )
    servalar_audit = asset_audit["asset_id"].eq("servalar")
    asset_audit.loc[servalar_audit, "installed_turbine_mw"] = float(
        servalar_units["p_nom"].sum()
    )
    asset_audit.loc[servalar_audit, "electrical_bus"] = str(
        servalar_units.iloc[0]["bus"]
    )
    asset_audit["model_component"] = asset_audit.apply(
        lambda row: f"hydro_{row.asset_id}" if row.representation != "run_of_river" else "unit generators",
        axis=1,
    )
    asset_audit.loc[
        asset_audit["asset_id"].isin(["mettur", "periyar", "moyar", "papanasam", "servalar"]),
        "model_component",
    ] = "explicit hydraulic Store and official turbine Link(s)"
    return generators, storage, metadata, reservoir_names, asset_audit, electrical_inventory


def build_reservoir_inflow(
    snapshots_table: pd.DataFrame,
    reservoir_storage: pd.DataFrame,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build component-specific inflow and observed SOC from daily reservoir data.

    Storage volume is converted on a documented fixed-head basis.  A component
    without a defensible observed or connected-reservoir source receives zero
    natural inflow rather than a statewide generation-derived proxy.
    """
    if not AGRICULTURE_RESERVOIR_DATA.exists():
        raise FileNotFoundError(f"Missing validated reservoir dataset: {AGRICULTURE_RESERVOIR_DATA}")
    raw = pd.read_csv(AGRICULTURE_RESERVOIR_DATA)
    required = {"date", "reservoir", "current_storage_mcft", "full_capacity_mcft", "current_inflow_cusec", "model_ready"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"Agriculture reservoir data missing columns: {sorted(missing)}")
    raw = raw.loc[raw["model_ready"].astype(str).str.lower().eq("true")].copy()
    raw["date"] = pd.to_datetime(raw["date"]).dt.normalize()
    raw["current_storage_mcft"] = pd.to_numeric(raw["current_storage_mcft"], errors="coerce")
    raw["full_capacity_mcft"] = pd.to_numeric(raw["full_capacity_mcft"], errors="coerce")
    raw["current_inflow_cusec"] = pd.to_numeric(raw["current_inflow_cusec"], errors="coerce").fillna(0.0)
    raw = raw.dropna(subset=["current_storage_mcft", "full_capacity_mcft"])

    # Component-to-observation mapping.  Values marked proxy use only the
    # connected reservoir's *fractional* filling, never its absolute volume.
    mapping = {
        "hydro_mettur": ("METTUR", "observed_agriculture", 100.0),
        "hydro_periyar": ("Periyar", "observed_agriculture", HYDRO_DEFAULT_HEAD_M),
        "hydro_papanasam": ("Papanasam (TN EB Dam)", "observed_agriculture", HYDRO_DEFAULT_HEAD_M),
        "hydro_sholayar": ("Sholayar", "observed_agriculture", HYDRO_DEFAULT_HEAD_M),
        "hydro_sarkarpathy": ("Parambikulam", "proxy_connected_reservoir", HYDRO_DEFAULT_HEAD_M),
        "hydro_aliyar": ("Parambikulam", "proxy_connected_reservoir", HYDRO_DEFAULT_HEAD_M),
        "hydro_kodayar": ("Pechiparai", "proxy_connected_reservoir", HYDRO_DEFAULT_HEAD_M),
    }
    snapshots = pd.DatetimeIndex(pd.to_datetime(snapshots_table["snapshot"]))
    inflow = pd.DataFrame(0.0, index=snapshots_table.index, columns=reservoir_storage["name"])
    observed_rows: list[dict[str, object]] = []
    parameter_rows: list[dict[str, object]] = []

    for _, unit in reservoir_storage.iterrows():
        name = str(unit["name"])
        configured = mapping.get(name)
        p_nom = float(unit["p_nom"])
        if configured is None:
            provisional_note = (
                "TANGEDCO monthly Suruliyar stored-energy series is not yet loaded; "
                "use the explicit neutral seasonal assumption and retain low data quality."
                if name == "hydro_suruliyar" else
                "No natural inflow assigned; weekly SOC is a documented neutral assumption."
            )
            parameter_rows.append({
                "pypsa_component": name, "reservoir_id": "", "p_nom_mw": p_nom,
                "e_nom_mwh": p_nom * RESERVOIR_MAX_HOURS,
                "energy_capacity_method": "documented_assumption", "soc_source": "assumed",
                "soc_proxy_reservoir": "", "initial_soc_method": "assumed_fraction",
                "terminal_soc_method": "explicit_assumed_target", "boundary_data_resolution": "none",
                "soc_boundary_tolerance_pu": 0.05, "weekly_balance_method": "explicit_assumption",
                "efficiency": HYDRO_DEFAULT_EFFICIENCY,
                "head_m": np.nan, "data_quality": "low", "source": "No reservoir observation mapped",
                "p_nom_source": "official_tamil_nadu_generation_inventory",
                "notes": provisional_note,
            })
            continue
        reservoir, soc_source, head_m = configured
        data = raw.loc[raw["reservoir"].eq(reservoir)].set_index("date").sort_index()
        if data.empty:
            raise ValueError(f"No model-ready Agriculture observations for mapped reservoir {reservoir}")
        full_mcft = float(data["full_capacity_mcft"].max())
        e_nom = MCFT_M_HEAD_TO_MWH * full_mcft * head_m * HYDRO_DEFAULT_EFFICIENCY
        if e_nom <= 0:
            raise ValueError(f"Non-positive energy capacity for {name}")
        daily_fraction = (data["current_storage_mcft"] / data["full_capacity_mcft"]).clip(0.0, 1.0)
        daily_soc = daily_fraction * e_nom
        daily_inflow_mwh = (
            data["current_inflow_cusec"] * 0.0864 * MCFT_M_HEAD_TO_MWH * head_m * HYDRO_DEFAULT_EFFICIENCY
        )
        if soc_source == "proxy_connected_reservoir":
            # The connected observation is a seasonal filling proxy only. Do
            # not turn its inflow into this reservoir's independent inflow.
            daily_inflow_mwh[:] = 0.0
        dates = snapshots.normalize()
        mapped_inflow = dates.map(daily_inflow_mwh).fillna(0.0).to_numpy() / 24.0
        inflow[name] = mapped_inflow
        for date, value in daily_soc.items():
            observed_rows.append({
                "date": date.date().isoformat(), "pypsa_component": name,
                "soc_mwh": float(value), "soc_fraction": float(value / e_nom),
                "soc_source": soc_source, "reservoir_observation": reservoir,
                "data_quality": "high" if soc_source == "observed_agriculture" else "medium",
            })
        parameter_rows.append({
            "pypsa_component": name, "reservoir_id": reservoir, "p_nom_mw": p_nom,
            "e_nom_mwh": e_nom,
            "energy_capacity_method": "agriculture_volume_x_fixed_head",
            "soc_source": soc_source,
            "soc_proxy_reservoir": reservoir if soc_source.startswith("proxy") else "",
            "initial_soc_method": "observed_daily" if soc_source == "observed_agriculture" else "proxy_fraction",
            "terminal_soc_method": "observed_daily_target" if soc_source == "observed_agriculture" else "proxy_target",
            "boundary_data_resolution": "daily" if soc_source == "observed_agriculture" else "proxy",
            "soc_boundary_tolerance_pu": 0.0, "weekly_balance_method": "seasonal_observed",
            "efficiency": HYDRO_DEFAULT_EFFICIENCY, "head_m": head_m,
            "data_quality": "high" if soc_source == "observed_agriculture" else "medium",
            "source": "Tamil Nadu Agriculture daily reservoir database",
            "p_nom_source": "official_tamil_nadu_generation_inventory",
            "notes": "Fixed-head first implementation; inflow is uniformly distributed over 24 hours.",
        })

    parameters = pd.DataFrame(parameter_rows)
    for _, row in parameters.loc[parameters["e_nom_mwh"].notna()].iterrows():
        unit_index = reservoir_storage.index[reservoir_storage["name"].eq(row["pypsa_component"])]
        if len(unit_index):
            reservoir_storage.loc[unit_index, "max_hours"] = float(row["e_nom_mwh"]) / float(row["p_nom_mw"])
            reservoir_storage.loc[unit_index, "efficiency_dispatch"] = float(row["efficiency"])
            first = pd.Timestamp(FY_START)
            observed_frame = pd.DataFrame(observed_rows)
            first_soc = observed_frame.loc[
                observed_frame["pypsa_component"].eq(row["pypsa_component"])
                & observed_frame["date"].eq(first.strftime("%Y-%m-%d"))
            ] if len(observed_frame) else observed_frame
            if len(first_soc):
                reservoir_storage.loc[unit_index, "state_of_charge_initial"] = float(first_soc.iloc[0]["soc_mwh"])
            elif row["soc_source"] == "assumed":
                reservoir_storage.loc[unit_index, "state_of_charge_initial"] = 0.5 * float(row["e_nom_mwh"])

    audit = pd.DataFrame({"date": pd.date_range(FY_START, FY_END, freq="D")})
    audit["observed_reservoir_inflow_mwh"] = (
        inflow.sum(axis=1).groupby(snapshots.normalize()).sum()
        .reindex(audit["date"], fill_value=0.0).to_numpy()
    )
    audit["inflow_method"] = "Tamil Nadu Agriculture daily inflow; no statewide allocation"
    return inflow, audit, pd.DataFrame(observed_rows), parameters


def build_run_of_river_profiles(generators: pd.DataFrame, snapshots: pd.Series) -> pd.DataFrame:
    """Derive barrage availability from documented upstream daily outflow."""
    ror = generators.loc[generators["carrier"].eq("hydro_run_of_river")]
    if ror.empty:
        return pd.DataFrame(index=pd.RangeIndex(len(snapshots)))
    raw = pd.read_csv(AGRICULTURE_RESERVOIR_DATA)
    raw = raw.loc[raw["model_ready"].astype(str).str.lower().eq("true")].copy()
    raw["date"] = pd.to_datetime(raw["date"]).dt.normalize()
    raw["current_outflow_cusec"] = pd.to_numeric(raw["current_outflow_cusec"], errors="coerce").fillna(0.0)
    upstream = {
        "lower_mettur": "METTUR",
        "bhavani_kattalai": "BHAVANISAGAR",
    }
    index = pd.DatetimeIndex(pd.to_datetime(snapshots))
    profiles: dict[str, np.ndarray] = {}
    ror = ror.copy()
    ror["upstream_reservoir"] = ror["name"].map(
        lambda name: next(
            (reservoir for prefix, reservoir in upstream.items() if str(name).startswith(prefix)), None
        )
    )
    for source, units in ror.dropna(subset=["upstream_reservoir"]).groupby("upstream_reservoir"):
        # The observed upstream release is one shared water volume.  Allocate
        # it over the complete barrage fleet, rather than allowing every unit
        # to independently use the same release.
        total_p_nom = float(units["p_nom"].sum())
        if total_p_nom <= 0.0:
            continue
        daily = raw.loc[raw["reservoir"].eq(source)].set_index("date")["current_outflow_cusec"]
        daily_mwh = daily * 0.0864 * MCFT_M_HEAD_TO_MWH * HYDRO_DEFAULT_HEAD_M * HYDRO_DEFAULT_EFFICIENCY
        availability = index.normalize().map(daily_mwh).fillna(0.0).to_numpy() / (24.0 * total_p_nom)
        for name in units["name"]:
            profiles[str(name)] = np.clip(availability, 0.0, 1.0)
    return pd.DataFrame(profiles, index=pd.RangeIndex(len(snapshots)))


def build_hydraulic_cascades(
    storage: pd.DataFrame,
    snapshots: pd.Series,
    parameters: pd.DataFrame,
    electrical_inventory: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Replace cascade/PSP StorageUnit proxies with explicit water Stores/Links.

    Water-bus quantities use a documented first-pass hydraulic-equivalent unit.
    Turbine Links preserve the routed water quantity at bus2 while producing
    electricity at bus1. Their p_nom is always copied from the official Tamil
    Nadu generation inventory. Water-bus energy has already incorporated the
    first-pass hydraulic conversion, so a Link has unit electrical efficiency
    and does not rescale the authoritative electrical capacity.
    """
    p = storage.set_index("name")
    param = parameters.set_index("pypsa_component")
    targets = [
        "hydro_mettur", "hydro_periyar",
        "hydro_aliyar", "hydro_sholayar", "hydro_sarkarpathy", "hydro_kodayar",
        "hydro_kundah", "hydro_pykara", "hydro_pykara_ultimate", "hydro_moyar",
        "hydro_parsons_valley", "hydro_papanasam",
    ]
    kadamparai = [name for name in p.index if name.startswith("kadmparai_power_house")]
    missing = set(targets) - set(p.index)
    if missing:
        raise ValueError(f"Cascade assets missing from storage model: {sorted(missing)}")
    remaining = storage.loc[~storage["name"].isin([*targets, *kadamparai])].copy()

    def capacity(name: str) -> tuple[float, float]:
        row = p.loc[name]
        p_nom = float(row["p_nom"])
        e_nom = float(param.at[name, "e_nom_mwh"])
        return p_nom, e_nom

    def assumed_capacity(mw: float) -> float:
        return mw * RESERVOIR_MAX_HOURS

    buses: set[str] = set()
    stores: list[dict[str, object]] = []
    links: list[dict[str, object]] = []
    metadata: list[dict[str, object]] = []

    def add_store(
        name: str, e_nom: float, initial_fraction: float, source: str, quality: str,
        notes: str, *, initial_method: str | None = None, terminal_method: str | None = None,
        resolution: str | None = None, weekly_balance: str | None = None,
    ):
        bus = f"water_{slugify(name)}"
        buses.add(bus)
        stores.append({"name": name, "bus": bus, "e_nom": e_nom, "e_initial": e_nom * initial_fraction,
                       "e_cyclic": False, "carrier": "water_energy"})
        metadata.append({"reservoir_id": name, "pypsa_component": name, "p_nom_mw": np.nan,
                         "e_nom_mwh": e_nom, "energy_capacity_method": "observed_or_documented_proxy",
                         "soc_source": source, "soc_proxy_reservoir": "",
                         "initial_soc_method": initial_method or (
                             "observed_daily" if source == "observed_agriculture" else
                             "assumed_fraction" if source in {"assumed", "endogenous_pondage"} else "proxy_fraction"
                         ),
                         "terminal_soc_method": terminal_method or (
                             "observed_daily_target" if source == "observed_agriculture" else
                             "end_equals_start" if source == "endogenous_pondage" else "explicit_assumed_target"
                         ),
                         "boundary_data_resolution": resolution or (
                             "daily" if source == "observed_agriculture" else
                             "none" if source in {"assumed", "endogenous_pondage"} else "proxy"
                         ),
                         "soc_boundary_tolerance_pu": 0.05,
                         "weekly_balance_method": weekly_balance or (
                             "seasonal_observed" if source == "observed_agriculture" else
                             "end_equals_start" if source == "endogenous_pondage" else "explicit_assumption"
                         ),
                         "efficiency": HYDRO_DEFAULT_EFFICIENCY,
                         "head_m": HYDRO_DEFAULT_HEAD_M, "data_quality": quality,
                         "source": "Tamil Nadu Agriculture / documented first-pass assumption",
                         "p_nom_source": "not_applicable_store", "notes": notes})
        return bus

    turbine_audit: list[dict[str, object]] = []

    def add_turbine(
        name: str,
        upstream: str,
        electrical_bus: str,
        downstream: str,
        output_mw: float,
        official_component_id: str,
        asset_id: str,
        storage_representation: str,
        aggregation_note: str = "",
    ):
        links.append({"name": name, "bus0": upstream, "bus1": electrical_bus, "bus2": downstream,
                      "p_nom": output_mw, "p_min_pu": 0.0,
                      "efficiency": 1.0, "efficiency2": 1.0,
                      "carrier": "hydro_turbine", "marginal_cost": 0.0})
        turbine_audit.append({
            "official_component_id": official_component_id,
            "asset_id": asset_id,
            "model_component": name,
            "model_component_type": "Link (hydro_turbine)",
            "official_p_nom_mw": output_mw,
            "model_p_nom_mw": output_mw,
            "p_nom_source": "official_tamil_nadu_generation_inventory",
            "upstream_store": upstream.removeprefix("water_"),
            "downstream_store": downstream.removeprefix("water_"),
            "storage_representation": storage_representation,
            "aggregation_note": aggregation_note,
            "p_nom_validation": "pass: model p_nom equals official inventory",
        })

    def add_asset_turbines(
        asset_id: str,
        upstream: str,
        electrical_bus: str,
        downstream: str,
        storage_representation: str,
        prefix: str,
        aggregation_note: str = "",
    ) -> None:
        units = electrical_inventory.loc[electrical_inventory["asset_id"].eq(asset_id)]
        if units.empty:
            raise ValueError(f"No official electrical inventory records for {asset_id}")
        for _, unit in units.iterrows():
            add_turbine(
                f"{prefix}__{unit.official_component_id}", upstream, electrical_bus, downstream,
                float(unit.official_p_nom_mw), str(unit.official_component_id), asset_id,
                storage_representation, aggregation_note,
            )

    # Observed seasonal reservoirs need explicit non-generating release paths:
    # an electrical-only StorageUnit cannot reproduce irrigation/spill-driven
    # SOC drawdown larger than turbine nameplate. Turbine MW remains official.
    for asset_id, store_name, district, release_capacity in [
        ("mettur", "Mettur_reservoir", "Erode", METTUR_OBSERVED_RELEASE_CAPACITY_MW),
        ("periyar", "Periyar_reservoir", "Madurai", PERIYAR_OBSERVED_RELEASE_CAPACITY_MW),
    ]:
        plant_mw, plant_e = capacity(f"hydro_{asset_id}")
        reservoir = add_store(
            store_name, plant_e, 0.5, "observed_agriculture", "high",
            f"Seasonal {asset_id.title()} reservoir with explicit turbine and non-generating release paths.",
        )
        downstream = add_store(
            f"{store_name.removesuffix('_reservoir')}_downstream",
            plant_e + release_capacity * len(snapshots), 0.0,
            "downstream_sink", "medium",
            "Terminal sink for turbine discharge, irrigation release, and spill.",
            initial_method="zero", terminal_method="none", resolution="none",
            weekly_balance="none",
        )
        add_asset_turbines(
            asset_id, reservoir, district, downstream,
            f"individual {store_name}", f"{asset_id}_turbine",
        )
        links.append({
            "name": f"{asset_id}_observed_release",
            "bus0": reservoir, "bus1": downstream,
            "p_nom": release_capacity, "p_min_pu": 0.0,
            "efficiency": 1.0, "carrier": "water_spill", "marginal_cost": 0.0,
        })

    # PAP: separate Parambikulam/Thunacadavu, Sholayar and Upper Aliyar Stores.
    shol_mw, shol_e = capacity("hydro_sholayar")
    sark_mw, sark_e = capacity("hydro_sarkarpathy")
    aliyar_mw, aliyar_e = capacity("hydro_aliyar")
    para = add_store("Parambikulam", sark_e, 0.5, "observed_agriculture", "high", "Seasonal source for PAP.")
    thun = add_store("Thunacadavu", 0.15 * sark_e, 0.5, "endogenous_pondage", "medium", "Initial fraction may use the connected Parambikulam SOC proxy; weekly water-neutral thereafter.", initial_method="proxy_fraction", terminal_method="end_equals_start", resolution="none", weekly_balance="end_equals_start")
    shol = add_store("Sholayar", shol_e, 0.5, "observed_agriculture", "high", "Observed daily Agriculture storage/inflow.")
    upper = add_store("Upper_Aliyar", aliyar_e, 0.5, "proxy_connected_reservoir", "medium", "PAP seasonal proxy; lower Aliyar absolute storage is not copied.", initial_method="proxy_fraction", terminal_method="proxy_target", resolution="proxy", weekly_balance="seasonal_observed")
    lower = add_store("Lower_Aliyar", aliyar_e, 0.5, "endogenous_pondage", "low", "Receives PAP turbine discharge.")
    pap_sink = add_store(
        "PAP_downstream",
        sark_e + shol_e + aliyar_e + PAP_OBSERVED_RELEASE_CAPACITY_MW * len(snapshots),
        0.0,
        "downstream_sink", "medium",
        "Terminal sink for non-generating PAP irrigation releases, spill, and lower-pondage outflow.",
        initial_method="zero", terminal_method="none", resolution="none",
        weekly_balance="none",
    )
    links.append({"name": "pap_parambikulam_to_thunacadavu", "bus0": para, "bus1": thun, "p_nom": sark_mw / HYDRO_DEFAULT_EFFICIENCY,
                  "p_min_pu": 0.0, "efficiency": 1.0, "carrier": "water_transfer", "marginal_cost": 0.0})
    links.extend([
        {"name": "pap_parambikulam_observed_release", "bus0": para, "bus1": pap_sink,
         "p_nom": PAP_OBSERVED_RELEASE_CAPACITY_MW, "p_min_pu": 0.0,
         "efficiency": 1.0, "carrier": "water_spill", "marginal_cost": 0.0},
        {"name": "pap_sholayar_observed_release", "bus0": shol, "bus1": pap_sink,
         "p_nom": PAP_OBSERVED_RELEASE_CAPACITY_MW, "p_min_pu": 0.0,
         "efficiency": 1.0, "carrier": "water_spill", "marginal_cost": 0.0},
        {"name": "pap_lower_aliyar_outflow", "bus0": lower, "bus1": pap_sink,
         "p_nom": PAP_OBSERVED_RELEASE_CAPACITY_MW, "p_min_pu": 0.0,
         "efficiency": 1.0, "carrier": "water_spill", "marginal_cost": 0.0},
    ])
    add_asset_turbines("sarkarpathy", thun, "Coimbatore", lower, "group-aggregated PAP storage", "pap_sarkarpathy_turbine")
    add_asset_turbines("sholayar", shol, "Coimbatore", lower, "individual Sholayar Store", "pap_sholayar_turbine")
    add_asset_turbines("aliyar", upper, "Coimbatore", lower, "group-aggregated PAP storage", "pap_aliyar_turbine")

    # Kadamparai PSP: no natural inflow, separate generation and pumping Links.
    kad_units = electrical_inventory.loc[electrical_inventory["official_component_id"].isin(kadamparai)]
    kad_mw = float(kad_units["official_p_nom_mw"].sum())
    kad = add_store("Kadamparai", kad_mw * 6.0 / HYDRO_DEFAULT_EFFICIENCY, 0.5, "assumed", "low", "No upper-reservoir observation: neutral initial fraction and weekly water-neutral boundary; pumping is not natural inflow.", initial_method="assumed_fraction", terminal_method="end_equals_start", resolution="none", weekly_balance="end_equals_start")
    add_asset_turbines("kadmparai", kad, "Coimbatore", upper, "individual Kadamparai Store", "kadamparai_generation")
    links.append({"name": "kadamparai_pump", "bus0": upper, "bus1": kad, "bus2": "Coimbatore",
                  "p_nom": kad_mw, "p_min_pu": 0.0,
                  "efficiency": HYDRO_DEFAULT_EFFICIENCY ** 2, "efficiency2": -1.0 / HYDRO_DEFAULT_EFFICIENCY,
                  "carrier": "hydro_pump", "marginal_cost": 0.0})

    # Kodayar: seasonal Upper Kodayar and endogenous Kodayar-II forebay.
    kod_mw, kod_e = capacity("hydro_kodayar")
    kod_upper = add_store("Upper_Kodayar", kod_e, 0.5, "proxy_connected_reservoir", "low", "Provisional Pechiparai normalized seasonal proxy until monthly Kodayar group energy is loaded.", initial_method="proxy_fraction", terminal_method="proxy_target", resolution="proxy", weekly_balance="explicit_assumption")
    kod_ii = add_store("Kodayar_II_forebay", 0.10 * kod_e, 0.5, "endogenous_pondage", "low", "Intermediate pondage; no invented seasonal trajectory.")
    kod_down = add_store("Kodayar_downstream", 0.10 * kod_e, 0.5, "endogenous_pondage", "low", "Downstream Kodayar routing sink.")
    kod_units = electrical_inventory.loc[electrical_inventory["asset_id"].eq("kodayar")].sort_values("official_component_id")
    if len(kod_units) != 2:
        raise ValueError("Kodayar I-II must have two official inventory records")
    first_kod, second_kod = [row for _, row in kod_units.iterrows()]
    add_turbine("kodayar_I__" + str(first_kod.official_component_id), kod_upper, "Tirunelveli", kod_ii,
                float(first_kod.official_p_nom_mw), str(first_kod.official_component_id), "kodayar", "group-aggregated Kodayar storage")
    add_turbine("kodayar_II__" + str(second_kod.official_component_id), kod_ii, "Tirunelveli", kod_down,
                float(second_kod.official_p_nom_mw), str(second_kod.official_component_id), "kodayar", "group-aggregated Kodayar storage")

    # Nilgiris/Kundah and Pykara cascades: observed seasonal data are added later;
    # absent series use documented neutral initial values and endogenous pondage.
    kund_mw, kund_e = capacity("hydro_kundah")
    _, parsons_e = capacity("hydro_parsons_valley")
    kund_upper = add_store("Upper_Bhavani", kund_e, 0.5, "monthly_group_energy", "low", "SRPC monthly stored-energy boundary method; provisional neutral fraction until the series is loaded.", initial_method="monthly_interpolated", terminal_method="monthly_interpolated_tolerance", resolution="monthly", weekly_balance="seasonal_interpolated")
    kund_down = add_store("Kundah_pondage", 0.10 * kund_e, 0.5, "endogenous_pondage", "low", "Aggregated intermediate Kundah pondage.")
    kund_sink = add_store("Kundah_downstream", 0.10 * parsons_e, 0.5, "endogenous_pondage", "low", "Downstream routing sink.")
    add_asset_turbines("kundah", kund_upper, "Coimbatore", kund_down, "group-aggregated Kundah storage", "kundah_turbine", "Official Kundah units share the first-pass aggregate hydraulic stage; no 585 MW source-table total is used.")
    add_asset_turbines("parsons_valley", kund_down, "Coimbatore", kund_sink, "Kundah downstream pondage", "parsons_valley_turbine")
    pyk_mw, pyk_e = capacity("hydro_pykara")
    pus_mw, pus_e = capacity("hydro_pykara_ultimate")
    moy_mw, moy_e = capacity("hydro_moyar")
    pyk = add_store("Pykara", pyk_e, 0.5, "monthly_group_energy", "low", "SRPC monthly stored-energy boundary method; provisional neutral fraction until the series is loaded.", initial_method="monthly_interpolated", terminal_method="monthly_interpolated_tolerance", resolution="monthly", weekly_balance="seasonal_interpolated")
    glen = add_store("Glenmorgan_forebay", 0.10 * pus_e, 0.5, "endogenous_pondage", "low", "No artificial seasonal forebay profile.")
    moy = add_store("Moyar_pondage", 0.10 * moy_e, 0.5, "endogenous_pondage", "low", "Downstream Pykara cascade pondage.")
    sink = add_store("Pykara_downstream", 0.10 * moy_e, 0.5, "endogenous_pondage", "low", "Downstream routing sink.")
    add_asset_turbines("pykara", pyk, "Coimbatore", glen, "individual Pykara Store", "pykara_turbine", "Official Pykara units share one first-pass hydraulic stage; no 61.2 MW grouped reference is used.")
    add_asset_turbines("pykara_ultimate", glen, "Coimbatore", moy, "Pykara group forebay", "pushep_turbine")
    add_asset_turbines("moyar", moy, "Coimbatore", sink, "Pykara/Moyar group pondage", "moyar_turbine", "Official Moyar units share one first-pass hydraulic stage; no 38 MW reference is used.")

    # Papanasam--Servalar: the Agriculture Papanasam series belongs only to the
    # seasonal Papanasam reservoir. Servalar is a separate upstream reservoir
    # reached by the transfer tunnel. Both Servalar generation and direct
    # Papanasam release feed the short-term lower pondage; only then can water
    # pass through the 32 MW Papanasam powerhouse to the downstream river.
    pap_mw, pap_e = capacity("hydro_papanasam")
    pap = add_store(
        "Papanasam_reservoir", pap_e, 0.5, "observed_agriculture", "high",
        "Seasonal upper reservoir; the Agriculture Papanasam observations apply here only.",
    )
    serv = add_store(
        "Servalar_reservoir", 0.10 * pap_e, 0.5, "endogenous_pondage", "medium",
        "Separate reservoir using the existing normalized group-storage proxy treatment until direct Servalar SOC observations are available.",
        initial_method="proxy_fraction", terminal_method="end_equals_start",
        resolution="none", weekly_balance="end_equals_start",
    )
    lower = add_store(
        "Papanasam_lower_pondage",
        PAPANASAM_LOWER_PONDAGE_HOURS * pap_mw,
        PAPANASAM_LOWER_PONDAGE_INITIAL_SOC_FRACTION,
        "endogenous_pondage", "medium",
        "Short-term diversion-weir/confluence pondage; configurable neutral initial SOC and retained-week end SOC equals start SOC.",
        initial_method="assumed_fraction", terminal_method="end_equals_start",
        resolution="none", weekly_balance="end_equals_start",
    )
    downstream = add_store(
        "Papanasam_downstream_river",
        pap_e + pap_mw * len(snapshots),
        0.0,
        "downstream_sink", "medium",
        "Terminal water sink for Papanasam PH discharge; no seasonal or weekly-neutral SOC target.",
        initial_method="zero", terminal_method="none", resolution="none",
        weekly_balance="none",
    )
    links.extend(
        [
            {
                "name": "papanasam_servalar_transfer_tunnel",
                "bus0": pap,
                "bus1": serv,
                "p_nom": PAPANASAM_SERVALAR_TRANSFER_CAPACITY_MW,
                "p_min_pu": 0.0,
                "efficiency": 1.0,
                "carrier": "water_transfer",
                "marginal_cost": 0.0,
            },
            {
                "name": "papanasam_direct_release",
                "bus0": pap,
                "bus1": lower,
                "p_nom": PAPANASAM_DIRECT_RELEASE_CAPACITY_MW,
                "p_min_pu": 0.0,
                "efficiency": 1.0,
                "carrier": "water_transfer",
                "marginal_cost": 0.0,
            },
            {
                "name": "papanasam_lower_pondage_bypass",
                "bus0": lower,
                "bus1": downstream,
                "p_nom": PAPANASAM_LOWER_PONDAGE_BYPASS_CAPACITY_MW,
                "p_min_pu": 0.0,
                "efficiency": 1.0,
                "carrier": "water_spill",
                "marginal_cost": 0.0,
            },
        ]
    )
    add_asset_turbines(
        "servalar", serv, "Tirunelveli", lower,
        "separate Servalar reservoir using documented group-storage proxy",
        "servalar_turbine",
    )
    add_asset_turbines(
        "papanasam", lower, "Tirunelveli", downstream,
        "Papanasam lower pondage", "papanasam_turbine",
    )

    # Remove the remaining proxy-only cascade components from the original fleet.
    removed = ["hydro_parsons_valley"]
    remaining = remaining.loc[~remaining["name"].isin(removed)].copy()
    return (
        remaining,
        pd.DataFrame({"name": sorted(buses), "v_nom": 1.0, "carrier": "water_energy"}),
        pd.DataFrame(stores),
        pd.DataFrame(links),
        pd.DataFrame(metadata),
        pd.DataFrame(turbine_audit),
    )


def build_water_inflow_generators(water_buses: pd.DataFrame, snapshots: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Create exogenous water-bus inflow generators from observed daily flow."""
    raw = pd.read_csv(AGRICULTURE_RESERVOIR_DATA)
    raw = raw.loc[raw["model_ready"].astype(str).str.lower().eq("true")].copy()
    raw["date"] = pd.to_datetime(raw["date"]).dt.normalize()
    raw["current_inflow_cusec"] = pd.to_numeric(raw["current_inflow_cusec"], errors="coerce").fillna(0.0)
    mapping = {
        "water_mettur_reservoir": "METTUR",
        "water_periyar_reservoir": "Periyar",
        "water_parambikulam": "Parambikulam",
        "water_sholayar": "Sholayar",
        "water_papanasam_reservoir": "Papanasam (TN EB Dam)",
    }
    index = pd.DatetimeIndex(pd.to_datetime(snapshots))
    rows: list[dict[str, object]] = []
    profiles: dict[str, np.ndarray] = {}
    for bus in water_buses["name"]:
        reservoir = mapping.get(bus)
        if reservoir is None:
            continue
        daily = raw.loc[raw["reservoir"].eq(reservoir)].set_index("date")["current_inflow_cusec"]
        mw = index.normalize().map(daily).fillna(0.0).to_numpy() * 0.0864 * MCFT_M_HEAD_TO_MWH * HYDRO_DEFAULT_HEAD_M * HYDRO_DEFAULT_EFFICIENCY / 24.0
        p_nom = max(float(np.max(mw)), 1e-6)
        name = f"{slugify(reservoir)}_natural_inflow"
        rows.append({"name": name, "bus": bus, "p_nom": p_nom, "p_min_pu": 0.0, "p_max_pu": 1.0,
                     "carrier": "water_inflow", "marginal_cost": 0.0, "committable": False})
        profiles[name] = mw / p_nom
    return pd.DataFrame(rows), pd.DataFrame(profiles, index=pd.RangeIndex(len(index)))


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
        elif carrier != "hydro_run_of_river" and name in meta.index:
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
    # Run-of-river is operationally distinct but remains conventional hydro
    # for source-capacity reconciliation.
    if "hydro_run_of_river" in allocated.index:
        allocated.loc["hydro"] = allocated.get("hydro", 0.0) + allocated.pop("hydro_run_of_river")
    allocated = allocated.add(storage.groupby("carrier")["p_nom"].sum(), fill_value=0.0)
    pd.testing.assert_series_equal(
        original.sort_index(), allocated.sort_index(), check_names=False, rtol=1e-10, atol=1e-7
    )


def build_hydro_capacity_validation(
    electrical_inventory: pd.DataFrame,
    storage: pd.DataFrame,
    generators: pd.DataFrame,
    stores: pd.DataFrame,
    hydraulic_links: pd.DataFrame,
    turbine_audit: pd.DataFrame,
) -> pd.DataFrame:
    """Report and enforce the separation of official MW and hydraulic storage."""
    grouped_reference = {
        "sholayar": 109.0,
        "kundah": 585.0,
        "pykara": 61.2,
        "moyar": 38.0,
    }
    rows: list[dict[str, object]] = []
    if len(turbine_audit):
        rows.extend(turbine_audit.to_dict("records"))

    # Reservoir StorageUnits are deliberate electrical aggregations of official
    # units sharing one water body. Their p_nom must equal the official sum.
    simple_assets = electrical_inventory.loc[
        electrical_inventory["representation"].eq("reservoir")
    ].groupby("asset_id", sort=False)
    for asset_id, units in simple_assets:
        name = f"hydro_{asset_id}"
        component = storage.loc[storage["name"].eq(name)]
        if component.empty:
            # Moyar and Papanasam are converted to turbine Links, so their
            # turbine rows already provide the authoritative validation.
            continue
        official = float(units["official_p_nom_mw"].sum())
        modeled = float(component.iloc[0]["p_nom"])
        rows.append({
            "official_component_id": " + ".join(units["official_component_id"]),
            "asset_id": asset_id,
            "model_component": name,
            "model_component_type": "StorageUnit (reservoir turbine)",
            "official_p_nom_mw": official,
            "model_p_nom_mw": modeled,
            "p_nom_source": "official_tamil_nadu_generation_inventory",
            "upstream_store": name,
            "downstream_store": "",
            "storage_representation": "individual reservoir StorageUnit; official units deliberately aggregated electrically",
            "aggregation_note": "Official units share one reservoir and are electrically aggregated.",
            "p_nom_validation": "pass: model p_nom equals official inventory" if np.isclose(official, modeled) else "FAIL",
        })

    # Run-of-river units remain individual official generators.
    ror = generators.loc[generators["carrier"].eq("hydro_run_of_river")].set_index("name")
    for _, unit in electrical_inventory.loc[electrical_inventory["representation"].eq("run_of_river")].iterrows():
        name = str(unit.official_component_id)
        modeled = float(ror.at[name, "p_nom"])
        official = float(unit.official_p_nom_mw)
        rows.append({
            "official_component_id": name, "asset_id": unit.asset_id,
            "model_component": name, "model_component_type": "Generator (run-of-river)",
            "official_p_nom_mw": official, "model_p_nom_mw": modeled,
            "p_nom_source": "official_tamil_nadu_generation_inventory",
            "upstream_store": "observed upstream release", "downstream_store": "",
            "storage_representation": "no storage; shared upstream-release availability",
            "aggregation_note": "", "p_nom_validation": "pass: model p_nom equals official inventory" if np.isclose(official, modeled) else "FAIL",
        })

    # The Kadamparai pump is intentionally one aggregate of its four official
    # 100 MW reversible units; the individual generation Links are above.
    kad = electrical_inventory.loc[electrical_inventory["asset_id"].eq("kadmparai")]
    pump = hydraulic_links.loc[hydraulic_links["name"].eq("kadamparai_pump")]
    if len(kad) and len(pump):
        official = float(kad["official_p_nom_mw"].sum())
        modeled = float(pump.iloc[0]["p_nom"])
        rows.append({
            "official_component_id": " + ".join(kad["official_component_id"]), "asset_id": "kadmparai",
            "model_component": "kadamparai_pump", "model_component_type": "Link (hydro_pump)",
            "official_p_nom_mw": official, "model_p_nom_mw": modeled,
            "p_nom_source": "official_tamil_nadu_generation_inventory",
            "upstream_store": "Upper_Aliyar", "downstream_store": "Kadamparai",
            "storage_representation": "individual Kadamparai Store",
            "aggregation_note": "Deliberate aggregate of four hydraulically equivalent reversible units.",
            "p_nom_validation": "pass: model p_nom equals official inventory" if np.isclose(official, modeled) else "FAIL",
        })

    # Stores are hydraulic-only components and therefore intentionally have no
    # electrical p_nom. Include them so the output is a complete hydro map.
    for _, store in stores.iterrows():
        rows.append({
            "official_component_id": "", "asset_id": "", "model_component": store["name"],
            "model_component_type": "Store (hydraulic only)", "official_p_nom_mw": np.nan,
            "model_p_nom_mw": np.nan, "p_nom_source": "not_applicable_store",
            "upstream_store": store["name"], "downstream_store": "",
            "storage_representation": "individual or group-aggregated hydraulic storage",
            "aggregation_note": "Storage aggregation does not alter electrical capacity.",
            "p_nom_validation": "not applicable",
        })

    report = pd.DataFrame(rows)
    report["grouped_reference_mw"] = report["asset_id"].map(grouped_reference)
    report["grouped_reference_status"] = report["grouped_reference_mw"].map(
        lambda value: "reference/system total only; not assigned to model p_nom" if pd.notna(value) else ""
    )
    failures = report.loc[report["p_nom_validation"].eq("FAIL")]
    if len(failures):
        raise AssertionError(f"Official hydro capacity changed in model: {failures[['model_component', 'official_p_nom_mw', 'model_p_nom_mw']].to_dict('records')}")
    return report


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
    generators, storage, expanded_metadata, reservoir_names, hydro_asset_audit, hydro_electrical_inventory = (
        convert_conventional_hydro_to_reservoirs(
            generators, storage, expanded_metadata
        )
    )
    validate_capacity_conservation(
        original_generators, original_storage, generators, storage
    )
    # The pre-cascade check above still sees the official Servalar source
    # Generator. Remove it only after that check; build_hydraulic_cascades
    # recreates the same 20 MW as an official turbine Link below.
    generators = generators.loc[
        ~generators["name"].str.startswith("servalar_small_hydro_project__")
    ].copy()

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
    nuclear_budget = read_oil_gas_energy_budget("Nuclear")
    reservoir_storage = storage.loc[storage["name"].isin(reservoir_names)]
    reservoir_inflow, reservoir_inflow_audit, hydro_observed_soc, hydro_parameters = build_reservoir_inflow(
        snapshots_table, reservoir_storage
    )
    calibrated = hydro_parameters.set_index("pypsa_component")
    for field, parameter_field in [("max_hours", "e_nom_mwh"), ("efficiency_dispatch", "efficiency")]:
        available = calibrated.index.intersection(storage["name"])
        if field == "max_hours":
            storage.loc[storage["name"].isin(available), field] = (
                storage.loc[storage["name"].isin(available), "name"].map(calibrated[parameter_field])
                / storage.loc[storage["name"].isin(available), "p_nom"]
            )
        else:
            storage.loc[storage["name"].isin(available), field] = storage.loc[
                storage["name"].isin(available), "name"
            ].map(calibrated[parameter_field])
    initial_soc = hydro_observed_soc.loc[
        hydro_observed_soc["date"].eq(FY_START.strftime("%Y-%m-%d"))
    ].set_index("pypsa_component")["soc_mwh"] if len(hydro_observed_soc) else pd.Series(dtype=float)
    storage.loc[storage["name"].isin(initial_soc.index), "state_of_charge_initial"] = storage.loc[
        storage["name"].isin(initial_soc.index), "name"
    ].map(initial_soc)
    storage, water_buses, stores, hydraulic_links, cascade_metadata, hydro_turbine_audit = build_hydraulic_cascades(
        storage, snapshots, hydro_parameters, hydro_electrical_inventory
    )
    store_soc_map = {
        "hydro_mettur": ["Mettur_reservoir"],
        "hydro_periyar": ["Periyar_reservoir"],
        "hydro_papanasam": ["Papanasam_reservoir"],
        "hydro_sholayar": ["Sholayar"],
        "hydro_sarkarpathy": ["Parambikulam", "Thunacadavu"],
        "hydro_aliyar": ["Upper_Aliyar"],
        "hydro_kodayar": ["Upper_Kodayar"],
    }
    store_sizes = stores.set_index("name")["e_nom"]
    store_soc_rows: list[pd.DataFrame] = []
    for source_component, target_stores in store_soc_map.items():
        source = hydro_observed_soc.loc[hydro_observed_soc["pypsa_component"].eq(source_component)]
        for target_store in target_stores:
            copied = source.copy()
            copied["pypsa_component"] = target_store
            copied["soc_mwh"] = copied["soc_fraction"] * float(store_sizes[target_store])
            store_soc_rows.append(copied)
    if store_soc_rows:
        hydro_observed_soc = pd.concat([hydro_observed_soc, *store_soc_rows], ignore_index=True)
    water_inflow_generators, water_inflow_profiles = build_water_inflow_generators(
        water_buses, snapshots
    )
    generators = pd.concat([generators, water_inflow_generators], ignore_index=True, sort=False)
    reservoir_names = pd.Index(storage.loc[storage["carrier"].eq("hydro") & storage["p_min_pu"].ge(0), "name"])
    reservoir_inflow = reservoir_inflow.reindex(columns=reservoir_names, fill_value=0.0)
    nuclear_mask = generators["carrier"].eq("nuclear")
    nuclear_capacity = generators.loc[nuclear_mask, "p_nom"].sum()
    if (nuclear_budget.daily_energy_target_mwh > nuclear_capacity * 24 + 1e-5).any():
        raise ValueError("Observed nuclear daily energy exceeds installed capacity")
    # Replace the inherited fixed 61.2% output with dispatch bounded by nameplate.
    generators.loc[nuclear_mask, "p_min_pu"] = 0.0
    generators.loc[nuclear_mask, "p_max_pu"] = 1.0
    oil_gas_annual_target_mwh = float(
        oil_gas_budget["daily_energy_target_mwh"].sum()
    )
    if not generators["carrier"].eq("oil_gas").any():
        raise ValueError("Cannot apply the oil-and-gas budget without oil-and-gas generators")
    biomass_capacity_mw = float(
        generators.loc[generators["carrier"].eq("bio_power"), "p_nom"].sum()
    )
    if biomass_capacity_mw <= 0:
        raise ValueError("Cannot apply a biomass target without bio_power generators")
    # CEA's Resource Adequacy Study assumes a 21.5% CUF for biomass.  An
    # equality target is intentional: without it, biomass is almost entirely
    # displaced by the lower-cost coal fleet in economic dispatch.
    biomass_annual_target_mwh = biomass_capacity_mw * CEA_BIOMASS_CUF * len(snapshots)
    global_constraints = pd.DataFrame(
        [
            {
                "name": OIL_GAS_BUDGET_CONSTRAINT,
                "type": "operational_limit",
                "carrier_attribute": "oil_gas",
                "sense": "==",
                "constant": oil_gas_annual_target_mwh,
            },
            {
                "name": BIOMASS_ENERGY_CONSTRAINT,
                "type": "operational_limit",
                "carrier_attribute": "bio_power",
                "sense": "==",
                "constant": biomass_annual_target_mwh,
            },
        ]
    )
    area_profiles = placeholder_profiles(
        areas, snapshots, generators, expanded_metadata
    )
    generator_profiles = build_generator_profiles(
        generators, expanded_metadata, area_profiles
    )
    ror_profiles = build_run_of_river_profiles(generators, snapshots)
    for name in ror_profiles.columns:
        generator_profiles[name] = ror_profiles[name]
    for name in water_inflow_profiles.columns:
        generator_profiles[name] = water_inflow_profiles[name]

    # Keep the preference on the final fleet so it applies consistently to
    # both the source fleet and the July-2026 capacity overlay.
    generators.loc[
        generators["carrier"].eq("solar"), "marginal_cost"
    ] = SOLAR_CURTAILMENT_PREFERENCE_COST

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    global_constraints.loc[len(global_constraints)] = {
        "name": "observed_fy2025_26_nuclear_generation_target",
        "type": "operational_limit", "carrier_attribute": "nuclear",
        "sense": "==", "constant": nuclear_budget.daily_energy_target_mwh.sum(),
    }
    nuclear_budget.to_csv(OUTPUT_DIR / "nuclear_daily_energy_budget.csv", index=False, date_format="%Y-%m-%d")
    reservoir_inflow_audit.to_csv(
        OUTPUT_DIR / "hydro_reservoir_inflow.csv", index=False, date_format="%Y-%m-%d"
    )
    hydro_observed_soc.to_csv(OUTPUT_DIR / "hydro_observed_soc.csv", index=False)
    hydro_parameter_report = pd.concat([hydro_parameters, cascade_metadata], ignore_index=True, sort=False)
    hydro_parameter_report.to_csv(OUTPUT_DIR / "hydro_reservoir_parameters.csv", index=False)
    hydro_parameter_report.loc[
        hydro_parameter_report["data_quality"].ne("high")
        | hydro_parameter_report["soc_source"].isin(["assumed", "proxy_connected_reservoir", "endogenous_pondage"])
    ].to_csv(OUTPUT_DIR / "hydro_assumptions_report.csv", index=False)
    hydro_capacity_validation = build_hydro_capacity_validation(
        hydro_electrical_inventory, storage, generators, stores, hydraulic_links, hydro_turbine_audit
    )
    hydro_capacity_validation.to_csv(OUTPUT_DIR / "hydro_capacity_validation.csv", index=False)
    shutil.copy2(HYDRO_RECONCILIATION, OUTPUT_DIR / "hydro_fleet_reconciliation.csv")
    hydro_asset_audit.to_csv(OUTPUT_DIR / "hydro_asset_audit.csv", index=False)
    network = pd.read_csv(BASE_MODEL / "network.csv")
    network.loc[0, "name"] = (
        "Tamil Nadu FY 2025-26 nine-balancing-area hourly UC inputs "
        "with July 2026 capacity overlay and seasonal reservoir hydro"
    )
    network.to_csv(OUTPUT_DIR / "network.csv", index=False)
    pd.concat([
        pd.DataFrame({"name": areas, "v_nom": 230.0, "carrier": "AC"}),
        water_buses,
    ], ignore_index=True).to_csv(
        OUTPUT_DIR / "buses.csv", index=False
    )
    grid_links = grid.drop(columns=["stated_capacity_mw", "availability_factor", "source_capacity_text", "capacity_method"])
    link_columns = list(dict.fromkeys([*grid_links.columns, *hydraulic_links.columns]))
    pd.concat([grid_links.reindex(columns=link_columns), hydraulic_links.reindex(columns=link_columns)], ignore_index=True).to_csv(
        OUTPUT_DIR / "links.csv", index=False
    )
    grid[["name", "bus0", "bus1", "stated_capacity_mw", "availability_factor", "p_nom", "source_capacity_text", "capacity_method"]].to_csv(
        OUTPUT_DIR / "transmission_capacity_metadata.csv", index=False
    )
    loads.to_csv(OUTPUT_DIR / "loads.csv", index=False)
    load_time_series.to_csv(OUTPUT_DIR / "loads-p_set.csv")
    generators.to_csv(OUTPUT_DIR / "generators.csv", index=False)
    generator_profiles.to_csv(OUTPUT_DIR / "generators-p_max_pu.csv")
    storage.to_csv(OUTPUT_DIR / "storage_units.csv", index=False)
    stores.to_csv(OUTPUT_DIR / "stores.csv", index=False)
    reservoir_inflow.to_csv(OUTPUT_DIR / "storage_units-inflow.csv")
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
    pd.DataFrame(
        [
            {
                "source": "CEA Resource Adequacy Study",
                "fiscal_year": "FY2025-26",
                "cuf": CEA_BIOMASS_CUF,
                "installed_capacity_mw": biomass_capacity_mw,
                "annual_energy_target_mwh": biomass_annual_target_mwh,
                "constraint_name": BIOMASS_ENERGY_CONSTRAINT,
                "constraint_sense": "==",
            }
        ]
    ).to_csv(OUTPUT_DIR / "biomass_energy_target_summary.csv", index=False)
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
            generators.loc[~generators["carrier"].isin(MECHANISM_CARRIERS | {"water_inflow"}), ["bus", "carrier", "p_nom"]],
            storage[["bus", "carrier", "p_nom"]],
            hydraulic_links.loc[hydraulic_links["carrier"].eq("hydro_turbine"), ["bus1", "carrier", "p_nom", "efficiency"]]
            .rename(columns={"bus1": "bus"}).assign(carrier="hydro", p_nom=lambda f: f["p_nom"] * f["efficiency"])[["bus", "carrier", "p_nom"]],
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
    missing_carriers = sorted(
        (set(generators["carrier"]) | set(stores["carrier"]) | set(hydraulic_links["carrier"]))
        - set(carriers["name"])
    )
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
        "Applied CEA biomass energy equality target: "
        f"{biomass_annual_target_mwh / 1_000.0:,.2f} MU "
        f"at {CEA_BIOMASS_CUF:.1%} CUF over FY2025-26"
    )
    print(
        "Applied July 2026 capacity overlay: "
        f"{additional_generators['p_nom'].sum():.4f} MW modeled and "
        f"{additional_capacity.loc[~additional_capacity['include_in_model'], 'capacity_mw'].sum():.2f} MW unassigned"
    )
    print(f"Model folder: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
