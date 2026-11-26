"""Build the repository's nine-balancing-area PyPSA model.

The script combines the build-stage template with the spatial model inputs and
writes the finished PyPSA CSV folder to ``model/``.
"""

from __future__ import annotations

import re
import shutil
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa

from transmission_assumptions import (
    INTERCONNECTOR_EFFICIENCY,
    INTERSTATE_FLOW_COST_PER_MWH,
    interconnector_efficiency,
)

from build_unit_level_model import COAL_CYCLE_COST_PER_MW
from bio_power_targets import read_bio_power_monthly_targets
from small_hydro_targets import read_small_hydro_monthly_targets
from solar_capacity_trajectory import monthly_solar_capacity, solar_capacity_mw
from solar_seasonal_correction import hourly_solar_correction


ROOT = Path(__file__).resolve().parents[1]
BASE_MODEL = ROOT / "data" / "build_template"
SOURCE_DIR = ROOT / "data" / "model_inputs"
OUTPUT_DIR = ROOT / "model"
AREA_FILE = SOURCE_DIR / "Balancing_areas.txt"
GRID_WORKBOOK = SOURCE_DIR / "TN_9BA_with_interconnectors_pypsa_FY2025-26.xlsx"
DEMAND_WORKBOOK = SOURCE_DIR / "tamil_nadu_balancing_area_demand.xlsx"
PROFILE_INPUT = SOURCE_DIR / "renewable_profiles.csv"
# Solar availability is calibrated so that the potential of the capacity
# installed through FY2025-26 (scripts/solar_capacity_trajectory.py) equals
# the NITI Dashboard's observed annual statewide solar generation; see
# apply_solar_capacity_trajectory.
OBSERVED_ANNUAL_GENERATION_FILE = SOURCE_DIR / "niti_dashboard_annual_generation_FY2025_26.csv"
ADDITIONAL_CAPACITY_FILE = SOURCE_DIR / "additional_capacity_2026_07.csv"
REA_INTERSTATE_HOURLY_FLOW_FILE = SOURCE_DIR / "rea_interstate_hourly_flows_FY2025_26.csv"
REA_INTERSTATE_GATEWAY_SHARE_FILE = SOURCE_DIR / "rea_interstate_gateway_shares_FY2025_26.csv"
# Interstate exchange basis: "transactions" uses the audited REA transactions
# only; "metered" adds a correction so that the net import delivered into
# Tamil Nadu matches the metered net import of the Tamil Nadu area
# (scripts/prepare_metered_interchange.py). Set with --interchange.
INTERCHANGE_BASIS = "metered"
TN_METERED_INTERCHANGE_FILE = SOURCE_DIR / "tn_metered_interchange_FY2025_26.csv"
REA_PLANT_EXPORT_HOURLY_FILE = SOURCE_DIR / "rea_plant_export_hourly_FY2025_26.csv"
REA_PLANT_EXPORT_AUDIT_FILE = SOURCE_DIR / "rea_plant_export_mapping_audit_FY2025_26.csv"
# Tamil Nadu's entitlement share of the central coal stations and their units,
# from scripts/prepare_declared_capacity.py (SRPC REA).
CENTRAL_STATION_ENTITLEMENT_FILE = SOURCE_DIR / "central_station_tn_entitlement_FY2025_26.csv"
CENTRAL_STATION_UNITS_FILE = SOURCE_DIR / "central_station_units.csv"
TN_EXPORT_CAPACITY_FILE = SOURCE_DIR / "tn_export_capacity_allocations.csv"
# Station energy charges and physical unit sizes for every coal component
# (sources in docs/input-data.md).
COAL_OPERATING_COST_FILE = SOURCE_DIR / "coal_operating_costs.csv"
PLANT_OPERATING_COST_FILE = SOURCE_DIR / "plant_operating_costs.csv"
# CEA, "Recommendations on Operational norms of secondary fuel oil
# consumption" to CERC (13 Jan 2025): oil per hot start-up (kl) by unit
# series. Used only to scale the Tuticorin (210 MW) cycling cost per MW to
# other unit sizes.
CEA_HOT_START_OIL_KL = {210: 20.0, 500: 30.0, 660: 45.0, 800: 60.0}
COAL_CYCLE_COST_REFERENCE_SERIES_MW = 210
# Hours committable coal units are treated as already online before the first
# modelled hour. Any value of at least the longest minimum up/down time makes
# the whole pre-horizon history "on"; one week is used for readability.
COAL_INITIAL_UP_HOURS = 168
OBSERVED_GENERATION_FILE = (
    ROOT
    / "data"
    / "source"
    / "TamilNadu_FY2025-2026_Electricity_Power_Generation_daily.xlsx"
)
CAPACITY_WORKBOOK = (
    ROOT
    / "data"
    / "source"
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
# Only these carriers keep on/off commitment decisions (see restrict_unit_commitment).
COMMITTABLE_CARRIERS = {"coal"}
EXPECTED_ADDITIONAL_CAPACITY_MW = {
    "diesel": 211.70,
    "solar": 212.33,
    "wind": 139.9595,
}
EXPECTED_UNASSIGNED_CAPACITY_MW = 49.31
EXPECTED_EXCLUDED_OFF_GRID_CAPACITY_MW = 75.91 + 27.57
EXPECTED_KNOWN_EXPORT_ALLOCATION_MW = 1_521.6
EXPECTED_UNLISTED_EXPORT_ALLOCATION_MW = 777.4
EXPECTED_JULY_2026_TOTAL_CAPACITY_MW = 48_380.41
FY_START = pd.Timestamp("2025-04-01")
FY_END = pd.Timestamp("2026-03-31")
# How the run scripts apply the observed energy targets (no GlobalConstraints).
OIL_GAS_TARGET_APPLICATION = "daily equality for each solved day (oil_gas_daily_energy_budget.csv)"
MONTHLY_TARGET_APPLICATION = (
    "equality over each solved period; monthly total spread uniformly over the month's hours"
)
RESERVOIR_MAX_HOURS = 720.0
HYDRO_ASSET_REGISTRY = SOURCE_DIR / "hydro_asset_registry.csv"
HYDRO_RECONCILIATION = SOURCE_DIR / "hydro_fleet_reconciliation.csv"
HYDRO_TECHNICAL_PARAMETERS = SOURCE_DIR / "hydro_technical_parameters.csv"
CEA_MONTHLY_HYDRO_GENERATION = (
    SOURCE_DIR / "cea_hydro_monthly_generation_FY2025_26.csv"
)
AGRICULTURE_RESERVOIR_DATA = (
    ROOT / "data" / "reservoirs" / "tn_reservoir_daily_FY2025_26.csv"
)
HYDRO_DEFAULT_EFFICIENCY = 0.90
HYDRO_DEFAULT_HEAD_M = 100.0
MCFT_M_HEAD_TO_MWH = 0.0771634

# CEA reports stations while the model combines a few cascades.  The systems
# below are the only explicit station-to-model aggregations used for targets.
CEA_SYSTEMS_BY_ASSET = {
    "aliyar": ["aliyar"], "bhavani_kattalai": ["bhavani_kattalai", "bhawani_barrage_ii", "bhawani_barrage_iii"],
    "kadmparai": ["kadamparai"], "kodayar": ["kodayar_i", "kodayar_ii"],
    "kundah": ["kundah_i", "kundah_ii", "kundah_iii", "kundah_iv", "kundah_v"],
    "lower_mettur": ["lower_mettur_i", "lower_mettur_ii", "lower_mettur_iii", "lower_mettur_iv"],
    "mettur": ["mettur_dam", "mettur_tunnel"], "moyar": ["moyar"], "papanasam": ["papanasam"],
    "parsons_valley": ["parsons_valley"], "periyar": ["periyar"], "pykara": ["pykara"],
    "pykara_ultimate": ["pykara_ultimate"], "sarkarpathy": ["sarkarpathy"], "sholayar": ["sholayar"],
    "suruliyar": ["suruliyar"],
}
# These systems have no Agriculture-flow representation.  Their CEA energy is
# therefore a weekly objective, allocated from monthly CEA values.  Agriculture
# linked systems receive the same allocation as a ceiling, not an objective.
NO_FLOW_CEA_TARGET_ASSETS = {"kundah", "parsons_valley", "pykara", "pykara_ultimate", "moyar", "suruliyar"}
AGRICULTURE_CEA_CAP_ASSETS = {
    "aliyar", "bhavani_kattalai", "kodayar", "lower_mettur", "mettur",
    "papanasam", "periyar", "sarkarpathy", "sholayar",
}
# Existing-reservoir FRL capacities stated together in the Government of India
# Expert Appraisal Committee minutes (7 March 2023, Kodayar PSHEP discussion).
KODAYAR_UPPER_FRL_CAPACITY_MCM = 98.66
KODAYAR_PECHIPARAI_FRL_CAPACITY_MCM = 150.22
KODAYAR_CAPACITY_SCALE = KODAYAR_UPPER_FRL_CAPACITY_MCM / KODAYAR_PECHIPARAI_FRL_CAPACITY_MCM
KODAYAR_CAPACITY_SOURCE = (
    "https://environmentclearance.nic.in/writereaddata/Form-1A/Minutes/"
    "2103202340054026Final_MOM_43rd_EAC_River_07_03_23.pdf"
)
KODAYAR_I_WATER_CFT_PER_KWH = 16.5
KODAYAR_I_HEAD_FT = 3109.0
KODAYAR_I_CONVERSION_SOURCE = (
    "https://tenders.bhel.com/sites/default/files/"
    "TCCSCT2159-2024-07-29-04%3A00%3A36.pdf"
)
KODAYAR_I_MWH_PER_MCFT = 1000.0 / KODAYAR_I_WATER_CFT_PER_KWH
KODAYAR_II_ASSUMED_MWH_PER_MCFT = (
    MCFT_M_HEAD_TO_MWH * HYDRO_DEFAULT_HEAD_M * HYDRO_DEFAULT_EFFICIENCY
)
# Water Stores are measured in the electricity their own plant would produce
# from the stored volume. Kodayar-I discharge enters the Kodayar-II forebay,
# so the same volume is re-expressed in Kodayar-II MWh on the way down. This
# keeps every turbine Link at efficiency 1.0, so its bus0 flow and p_nom are
# electrical MW (as CEA weekly targets and capacity statistics assume).
KODAYAR_I_TO_II_WATER_CONVERSION = (
    KODAYAR_II_ASSUMED_MWH_PER_MCFT / KODAYAR_I_MWH_PER_MCFT
)
# Kadamparai PSP: no reservoir survey is used, so the upper Store holds an
# assumed six hours of full generation output, in turbine-output MWh like
# every other water Store.
KADAMPARAI_STORAGE_HOURS = 6.0
# Pumping 1 MWh of electricity stores 0.81 MWh of later turbine output
# (0.90 pump x 0.90 turbine). The pumped water volume is conserved: what
# enters Kadamparai is exactly what leaves Upper Aliyar.
KADAMPARAI_PUMP_STORAGE_EFFICIENCY = HYDRO_DEFAULT_EFFICIENCY ** 2
# Kadamparai's dam is on the Kadamparai river, so part of its observed CEA
# generation (258 GWh in FY2025-26) comes from natural inflow rather than
# pumping. No inflow or pumping observation is available. The natural inflow
# is therefore the CEA generation minus the generation the model itself
# produces from pumped water (calibrated on a full-year rolling run without
# unit commitment), spread over the year in proportion to CEA's monthly
# Kadamparai generation. Recalibrate if pumping behaviour changes materially.
KADAMPARAI_MODELED_PUMPED_GENERATION_GWH = 50.2
MCM_TO_MCFT = 35.3146667214886
# Terminal river sinks are unbounded. PyPSA (>=1.4 verified) treats an infinite
# fixed e_nom as having no upper energy bound and drops that constraint row,
# avoiding the large right-hand side a finite placeholder (e.g. 1e12) adds.
TERMINAL_WATER_SINK_CAPACITY_MWH = np.inf
# Small positive tie-breaker that prefers useful turbine discharge over
# non-generating reservoir release whenever the electrical system can accept it.
# Spill remains available when required by observed SOC boundaries or hydraulics.
WATER_SPILL_MARGINAL_COST = 0.01  # currency units per MWh of released water
UPPER_ALIYAR_INFLOW = "upper_aliyar_calibration_inflow"
KADAMPARAI_INFLOW = "kadamparai_calibration_inflow"
# Equal operating costs leave wind-versus-solar curtailment allocation to the
# optimizer and avoid imposing a technology preference.
SOLAR_CURTAILMENT_PREFERENCE_COST = 0.0  # currency units per MWh
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


def read_hydro_technical_parameters() -> pd.DataFrame:
    """Read unit-level hydro corrections and public technical provenance."""
    frame = pd.read_csv(HYDRO_TECHNICAL_PARAMETERS)
    required = {
        "component_id", "asset_id", "model_carrier", "p_nom_mw",
        "operational_status", "source_url", "source_note",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(
            f"Hydro technical parameter file is missing columns: {sorted(missing)}"
        )
    if frame["component_id"].duplicated().any():
        duplicates = frame.loc[
            frame["component_id"].duplicated(keep=False), "component_id"
        ].tolist()
        raise ValueError(f"Duplicate hydro technical component IDs: {duplicates}")
    frame["p_nom_mw"] = pd.to_numeric(frame["p_nom_mw"], errors="raise")
    if frame["p_nom_mw"].le(0).any():
        raise ValueError("Hydro technical p_nom values must be positive")
    if frame[["component_id", "asset_id", "model_carrier", "source_url"]].isna().any().any():
        raise ValueError("Hydro technical identity, carrier and source fields are required")
    return frame


def apply_hydro_technical_overrides(
    generators: pd.DataFrame,
    storage: pd.DataFrame,
    metadata: pd.DataFrame,
    parameters: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Apply reviewed unit ratings/classification before spatial allocation."""
    generators = generators.copy()
    storage = storage.copy()
    metadata = metadata.copy()

    for row in parameters.itertuples(index=False):
        generator_match = generators["name"].eq(row.component_id)
        storage_match = storage["name"].eq(row.component_id)
        match_count = int(generator_match.sum() + storage_match.sum())
        if match_count != 1:
            raise ValueError(
                f"Hydro technical component {row.component_id!r} must match exactly "
                f"one source component, found {match_count}"
            )
        target = generators if generator_match.any() else storage
        target_mask = generator_match if generator_match.any() else storage_match
        target.loc[target_mask, "p_nom"] = float(row.p_nom_mw)
        target.loc[target_mask, "carrier"] = str(row.model_carrier)

        metadata_match = metadata["component_id"].eq(row.component_id)
        if metadata_match.sum() != 1:
            raise ValueError(
                f"Hydro technical component {row.component_id!r} must match one metadata row"
            )
        metadata.loc[metadata_match, "capacity_mw"] = float(row.p_nom_mw)
    return generators, storage, metadata


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


def read_grid() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Import the authoritative transmission network from its PyPSA workbook."""
    imported = pypsa.Network()
    # PyPSA documents import_from_excel for workbooks with one sheet per
    # component. openpyxl works with the installed PyPSA and this workbook.
    imported.import_from_excel(GRID_WORKBOOK, engine="openpyxl")

    buses = imported.buses.reset_index()
    buses["carrier"] = buses["carrier"].replace("", "AC").fillna("AC")
    buses = buses.reindex(columns=[
        "name", "v_nom", "x", "y", "carrier", "kind",
        "within_area_mw", "within_area_circuits",
    ])

    lines = imported.lines.reset_index().reindex(columns=[
        "name", "bus0", "bus1", "x", "r", "b", "s_nom", "s_max_pu",
        "length", "carrier", "kind", "s_nom_n1", "largest_circuit_mw",
        "n_circuits", "n_circuits_rated", "circuits_by_kv", "mw_by_kv",
        "line_ids",
    ])

    links = imported.links.reset_index()
    if "active" in links:
        links = links.loc[links["active"].fillna(False).astype(bool)].copy()
    links = links.reindex(columns=[
        "name", "bus0", "bus1", "carrier", "p_nom", "p_min_pu",
        "p_max_pu", "marginal_cost", "efficiency", "length", "kind", "direction", "pair",
        "p_nom_n1", "largest_circuit_mw", "n_circuits", "circuits_by_kv",
        "x_equivalent_ohm", "line_ids",
    ])
    links["marginal_cost"] = INTERSTATE_FLOW_COST_PER_MWH
    links["efficiency"] = interconnector_efficiency(links["length"])

    if len(lines) != 18:
        raise ValueError(f"Expected 18 active internal grid lines, found {len(lines)}")
    if len(links) != 24:
        raise ValueError(f"Expected 24 active interconnector links, found {len(links)}")
    if lines["name"].duplicated().any() or links["name"].duplicated().any():
        raise ValueError("Imported grid component names must be unique")

    line_metadata = lines.assign(component_type="Line", source_workbook=GRID_WORKBOOK.name)
    link_metadata = links.assign(component_type="Link", source_workbook=GRID_WORKBOOK.name)
    metadata = pd.concat([line_metadata, link_metadata], ignore_index=True, sort=False)
    return buses, lines, links, metadata


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


def cea_weekly_generation_mwh(asset: str) -> pd.DataFrame:
    """Allocate an asset's CEA station-month generation uniformly by day, then
    aggregate to the model's rolling weeks (week_start, week_end, week_days,
    weekly_generation_mwh)."""
    cea = pd.read_csv(CEA_MONTHLY_HYDRO_GENERATION, parse_dates=["report_month"])
    required = {"report_month", "hydro_system", "monthly_generation_gwh", "source_file"}
    if required - set(cea):
        raise ValueError("CEA monthly generation input is missing required columns")
    expected_months = pd.date_range(FY_START, FY_END, freq="MS")
    dates = pd.date_range(FY_START, FY_END, freq="D", name="date")
    week_start = FY_START + pd.to_timedelta(((dates - FY_START).days // 7) * 7, unit="D")
    calendar = pd.DataFrame({"date": dates, "week_start": week_start, "report_month": dates.to_period("M").to_timestamp()})
    calendar["week_end"] = (calendar.week_start + pd.Timedelta(days=6)).clip(upper=FY_END)
    calendar["week_days"] = calendar.groupby("week_start")["date"].transform("size")
    monthly = cea.loc[cea.hydro_system.isin(CEA_SYSTEMS_BY_ASSET[asset])].groupby(
        "report_month"
    ).monthly_generation_gwh.sum().reindex(expected_months)
    if monthly.isna().any():
        raise ValueError(f"Incomplete CEA month coverage for {asset}")
    days_in_month = calendar.groupby("report_month").date.transform("size")
    daily_mwh = calendar.report_month.map(monthly * 1000) / days_in_month
    weekly = calendar.assign(weekly_generation_mwh=daily_mwh.to_numpy()).groupby(
        "week_start", as_index=False
    ).agg(
        week_end=("week_end", "first"), week_days=("week_days", "first"),
        weekly_generation_mwh=("weekly_generation_mwh", "sum"),
    )
    weekly.attrs["annual_generation_gwh"] = float(monthly.sum())
    return weekly


def build_cascade_generation_calibration(
    electrical_inventory: pd.DataFrame,
) -> pd.DataFrame:
    """Allocate CEA station-month generation to rolling weeks.

    No-flow systems receive equality objectives. Systems represented with
    Agriculture flows receive only upper bounds, so hydrology may reduce their
    dispatch but cannot raise it above the observed CEA monthly energy.
    """
    capacities = electrical_inventory.groupby("asset_id")["official_p_nom_mw"].sum()
    rows: list[pd.DataFrame] = []
    for asset in sorted(NO_FLOW_CEA_TARGET_ASSETS | AGRICULTURE_CEA_CAP_ASSETS):
        systems = CEA_SYSTEMS_BY_ASSET[asset]
        if asset not in capacities.index:
            raise ValueError(f"CEA target lacks official model capacity: {asset}")
        weekly_cea = cea_weekly_generation_mwh(asset)
        frame = weekly_cea.rename(
            columns={"weekly_generation_mwh": "weekly_generation_target_mwh"}
        )
        frame.insert(1, "asset_id", asset)
        frame["constraint_kind"] = "equality" if asset in NO_FLOW_CEA_TARGET_ASSETS else "maximum"
        frame["weekly_capacity_mwh"] = float(capacities.at[asset]) * 24.0 * frame.week_days
        frame["annual_generation_target_gwh"] = weekly_cea.attrs["annual_generation_gwh"]
        frame["timing_basis"] = "CEA station-month total allocated uniformly by day, then aggregated to rolling weeks"
        frame["source"] = "CEA National Power Portal Sub-report 16 FY2025-26 monthly reports"
        frame["quality"] = "official"
        frame["notes"] = f"CEA system(s): {';'.join(systems)}"
        rows.append(frame)
    targets = pd.concat(rows, ignore_index=True)
    targets["target_exceeds_model_capacity"] = (
        targets.weekly_generation_target_mwh > targets.weekly_capacity_mwh + 1e-8
    )
    over_capacity = targets.loc[
        targets.target_exceeds_model_capacity & targets.constraint_kind.eq("equality")
    ]
    if len(over_capacity):
        details = over_capacity[["asset_id", "week_start", "weekly_generation_target_mwh", "weekly_capacity_mwh"]]
        raise ValueError(f"Hard CEA weekly objective exceeds official plant capacity: {details.to_dict('records')}")
    return targets


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
    # Off-grid categories are intentionally absent from the supported model's
    # component metadata and district allocation. Keep the workbook join
    # consistent with that documented scope.
    off_grid = allocation["Name of Power Plant"].astype(str).str.contains(
        r"off\s*-?\s*grid", case=False, regex=True
    )
    allocation = allocation.loc[~off_grid].copy()
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


def apply_tn_export_capacity_allocations(
    generators: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reduce listed in-state stations to their Tamil Nadu capacity shares."""
    allocations = pd.read_csv(TN_EXPORT_CAPACITY_FILE)
    required = {
        "station_id", "plant_name", "component_prefix", "gross_capacity_mw",
        "tn_capacity_mw", "known_other_state_allocation_mw",
        "unlisted_or_unallocated_mw", "recipient_allocations", "source_reference",
    }
    missing = sorted(required - set(allocations.columns))
    if missing:
        raise ValueError(f"TN export-allocation input is missing columns: {missing}")
    if allocations["station_id"].duplicated().any():
        raise ValueError("TN export-allocation station IDs must be unique")
    numeric = [
        "gross_capacity_mw", "tn_capacity_mw",
        "known_other_state_allocation_mw", "unlisted_or_unallocated_mw",
    ]
    allocations[numeric] = allocations[numeric].apply(pd.to_numeric, errors="raise")
    if (allocations[numeric] < 0).any().any():
        raise ValueError("TN export-allocation capacities cannot be negative")
    if not np.allclose(
        allocations.gross_capacity_mw,
        allocations.tn_capacity_mw
        + allocations.known_other_state_allocation_mw
        + allocations.unlisted_or_unallocated_mw,
    ):
        raise ValueError("Station capacities do not reconcile to TN, named-state, and residual shares")

    adjusted = generators.copy()
    allocations["applied_tn_capacity_ratio"] = (
        allocations.tn_capacity_mw / allocations.gross_capacity_mw
    )
    for index, record in allocations.iterrows():
        prefix = str(record.component_prefix)
        mask = adjusted["name"].astype(str).str.startswith(prefix)
        matched = adjusted.loc[mask]
        if matched.empty:
            raise ValueError(f"No generator units match TN allocation {record.station_id!r}")
        installed = float(matched.p_nom.sum())
        if not np.isclose(installed, float(record.gross_capacity_mw)):
            raise ValueError(
                f"{record.station_id} generator capacity is {installed} MW; expected "
                f"{record.gross_capacity_mw} MW"
            )
        ratio = float(record.applied_tn_capacity_ratio)
        adjusted.loc[mask] = pd.DataFrame(
            [scale_component(row, ratio) for _, row in matched.iterrows()],
            index=matched.index,
        )
        allocations.loc[index, "modeled_tn_capacity_mw"] = float(
            adjusted.loc[mask, "p_nom"].sum()
        )
        allocations.loc[index, "capacity_withheld_from_tn_mw"] = (
            float(record.gross_capacity_mw) - float(record.tn_capacity_mw)
        )

    if not np.isclose(allocations.tn_capacity_mw.sum(), 2_671.0):
        raise AssertionError("TN station capacity shares do not sum to the documented 2,671 MW")
    if not np.isclose(
        allocations.known_other_state_allocation_mw.sum(),
        EXPECTED_KNOWN_EXPORT_ALLOCATION_MW,
    ) or not np.isclose(
        allocations.unlisted_or_unallocated_mw.sum(),
        EXPECTED_UNLISTED_EXPORT_ALLOCATION_MW,
    ):
        raise AssertionError("TN station export allocations do not match the documented totals")
    return adjusted, allocations


def build_rea_plant_export_reservations(
    generators: pd.DataFrame,
    metadata: pd.DataFrame,
    snapshots: pd.Series,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Reserve modeled plant capacity for verified REA export schedules.

    Each matched REA plant receives one non-committable fixed-dispatch export
    generator at its physical bus. Its nameplate is the plant's peak concurrent
    export schedule. The plant's ordinary units, which serve Tamil Nadu, keep:

    - at the central coal stations, Tamil Nadu's entitlement share of their
      rating (``CENTRAL_STATION_ENTITLEMENT_FILE``): the share of the
      station's declared capacity allocated to Tamil Nadu. Other states also
      take Tamil Nadu's undrawn share (unrequisitioned surplus), so the export
      peak plus Tamil Nadu's share can exceed the rating; the export schedule
      is the actual one, and Tamil Nadu then is not drawing its share;
    - elsewhere, the rating less the export peak, so total nameplate is
      conserved and the reserved block cannot serve Tamil Nadu.
    """
    hourly = pd.read_csv(REA_PLANT_EXPORT_HOURLY_FILE, parse_dates=["snapshot"])
    source_audit = pd.read_csv(REA_PLANT_EXPORT_AUDIT_FILE)
    required = {
        "snapshot", "generator_name", "flow_MW", "energy_MWh",
    }
    if missing := sorted(required - set(hourly.columns)):
        raise ValueError(f"REA plant-export input is missing columns: {missing}")
    model_snapshots = pd.DatetimeIndex(pd.to_datetime(snapshots, errors="raise"))
    if not set(hourly["snapshot"]).issubset(set(model_snapshots)):
        raise ValueError("REA plant-export schedules contain snapshots outside the model year")
    if hourly["flow_MW"].isna().any() or hourly["flow_MW"].lt(0).any():
        raise ValueError("REA plant-export schedules must be finite and non-negative")

    entitlement = pd.read_csv(CENTRAL_STATION_ENTITLEMENT_FILE).set_index("station")["tn_share"]
    station_of = pd.read_csv(CENTRAL_STATION_UNITS_FILE).set_index("component")["station"]
    if not entitlement.between(0.0, 1.0).all():
        raise ValueError("Tamil Nadu entitlement shares must lie between 0 and 1")

    adjusted = generators.copy()
    expanded_metadata = metadata.copy()
    fixed_profiles: dict[str, np.ndarray] = {}
    audit_rows: list[dict[str, object]] = []
    for plant_name, plant_rows in hourly.groupby("generator_name", sort=False):
        audit_plant = source_audit.loc[
            source_audit["generator_name"].eq(plant_name)
            & source_audit["reservation_status"].eq("mapped_to_model_generator")
        ]
        component_text = audit_plant["component_ids"].dropna().drop_duplicates()
        if len(component_text) != 1:
            raise ValueError(f"REA plant {plant_name!r} has inconsistent component mappings")
        component_ids = str(component_text.iloc[0]).split(";")
        mask = adjusted["name"].isin(component_ids)
        matched = adjusted.loc[mask].copy()
        missing_components = sorted(set(component_ids) - set(matched["name"]))
        if missing_components:
            raise ValueError(f"REA plant {plant_name!r} lacks model components: {missing_components}")
        if matched["bus"].nunique() != 1 or matched["carrier"].nunique() != 1:
            raise ValueError(f"REA plant {plant_name!r} components must share one bus and carrier")

        profile = (
            plant_rows.groupby("snapshot", observed=True)["flow_MW"].sum()
            .reindex(model_snapshots, fill_value=0.0)
        )
        peak_mw = float(profile.max())
        plant_capacity_mw = float(matched["p_nom"].sum())
        if peak_mw <= 0.0 or peak_mw > plant_capacity_mw + 1e-6:
            raise ValueError(
                f"REA export peak for {plant_name!r} is {peak_mw} MW against "
                f"{plant_capacity_mw} MW modeled capacity"
            )
        stations = station_of.reindex(component_ids)
        if stations.notna().any():
            if stations.isna().any() or stations.nunique() != 1 or stations.iloc[0] not in entitlement.index:
                raise ValueError(f"REA plant {plant_name!r} does not map to one central station with a share")
            station = str(stations.iloc[0])
            retained_share = float(entitlement[station])
            sizing = f"Tamil Nadu entitlement share of {station}"
        else:
            station = ""
            retained_share = (plant_capacity_mw - peak_mw) / plant_capacity_mw
            sizing = "rating less export peak"
        adjusted.loc[mask] = pd.DataFrame(
            [scale_component(row, retained_share) for _, row in matched.iterrows()],
            index=matched.index,
        )

        reservation_name = f"rea_export_reservation__{slugify(str(plant_name))}"
        template = matched.iloc[0].copy()
        template["name"] = reservation_name
        template["p_nom"] = peak_mw
        template["p_min_pu"] = 0.0
        template["p_max_pu"] = 1.0
        template["p_init"] = 0.0
        template["committable"] = False
        for field in [
            "start_up_cost", "shut_down_cost", "min_up_time", "min_down_time",
            "up_time_before", "down_time_before",
        ]:
            if field in template.index:
                template[field] = 0.0
        # Dispatch is pinned to the REA schedule, so the parent units' ramp
        # limits must not apply: they would make the fixed schedule infeasible
        # from p_init = 0 and wherever the schedule itself steps faster.
        for field in ["ramp_limit_up", "ramp_limit_down"]:
            if field in template.index:
                template[field] = np.nan
        adjusted = pd.concat([adjusted, template.to_frame().T], ignore_index=True)
        fixed_profiles[reservation_name] = profile.to_numpy() / peak_mw

        meta_match = expanded_metadata.loc[
            expanded_metadata["component_id"].isin(component_ids)
        ]
        if not meta_match.empty:
            reservation_meta = meta_match.iloc[0].copy()
            reservation_meta["component_id"] = reservation_name
            reservation_meta["original_component_id"] = reservation_name
            reservation_meta["capacity_mw"] = peak_mw
            reservation_meta["allocation_method"] = "verified REA plant export schedule"
            reservation_meta["allocation_confidence"] = "verified"
            expanded_metadata = pd.concat(
                [expanded_metadata, reservation_meta.to_frame().T], ignore_index=True
            )

        beneficiary_gwh = audit_plant.groupby(
            "beneficiary_state", observed=True
        )["energy_GWh"].sum()
        audit_rows.append({
            "rea_generator_name": plant_name,
            "reservation_generator": reservation_name,
            "component_ids": ";".join(component_ids),
            "bus": str(matched["bus"].iloc[0]),
            "carrier": str(matched["carrier"].iloc[0]),
            "physical_capacity_mw": plant_capacity_mw,
            "reserved_capacity_mw": peak_mw,
            "remaining_tn_dispatchable_capacity_mw": plant_capacity_mw * retained_share,
            "central_station": station,
            "tn_capacity_sizing": sizing,
            # Modeled minus physical nameplate: positive where the export peak
            # and Tamil Nadu's share together exceed the rating.
            "capacity_overlap_mw": plant_capacity_mw * retained_share + peak_mw - plant_capacity_mw,
            "scheduled_export_GWh": float(profile.sum() / 1_000.0),
            "beneficiary_energy_GWh": ";".join(
                f"{state}:{value:.6f}" for state, value in beneficiary_gwh.items()
            ),
            "status": "fixed_from_verified_rea_schedule",
        })

    numeric_columns = generators.select_dtypes(include=[np.number]).columns
    for column in numeric_columns:
        adjusted[column] = pd.to_numeric(adjusted[column], errors="raise")
    fixed = pd.DataFrame(fixed_profiles, index=pd.RangeIndex(len(model_snapshots)))
    audit = pd.DataFrame(audit_rows)
    expected_mapped_gwh = float(source_audit.loc[
        source_audit["reservation_status"].eq("mapped_to_model_generator"), "energy_GWh"
    ].sum())
    if not np.isclose(float(audit["scheduled_export_GWh"].sum()), expected_mapped_gwh):
        raise AssertionError("Modeled plant reservations do not conserve mapped REA export energy")
    if adjusted["name"].duplicated().any():
        raise AssertionError("REA export reservation generator names are not unique")
    return adjusted, expanded_metadata, fixed, audit


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
    raw["current_inflow_cusec"] = pd.to_numeric(raw["current_inflow_cusec"], errors="coerce")
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
        capacity_method: str | None = None, source_detail: str | None = None,
        head_m: float | None = None, conversion_efficiency: float | None = None,
    ):
        bus = f"water_{slugify(name)}"
        buses.add(bus)
        # Guard 0 * inf = NaN for unbounded sinks that start empty.
        e_initial = 0.0 if initial_fraction == 0 else e_nom * initial_fraction
        stores.append({"name": name, "bus": bus, "e_nom": e_nom, "e_initial": e_initial,
                       "e_cyclic": False, "carrier": "water_energy"})
        metadata.append({"reservoir_id": name, "pypsa_component": name, "p_nom_mw": np.nan,
                         "e_nom_mwh": e_nom, "energy_capacity_method": capacity_method or "observed_or_documented_proxy",
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
                         "efficiency": conversion_efficiency if conversion_efficiency is not None else HYDRO_DEFAULT_EFFICIENCY,
                         "head_m": head_m if head_m is not None else HYDRO_DEFAULT_HEAD_M,
                         "data_quality": quality,
                         "source": source_detail or "Tamil Nadu Agriculture / documented first-pass assumption",
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
        discharge_conversion: float = 1.0,
    ):
        # The upstream Store is in this plant's turbine-output MWh, so the
        # electrical efficiency is 1.0 and p_nom (measured on bus0) equals
        # the official electrical MW. discharge_conversion re-expresses the
        # discharged volume in the downstream Store's own MWh units.
        if discharge_conversion <= 0:
            raise ValueError("Hydro turbine discharge conversion must be positive")
        links.append({"name": name, "bus0": upstream, "bus1": electrical_bus, "bus2": downstream,
                      "p_nom": output_mw, "p_min_pu": 0.0,
                      "efficiency": 1.0, "efficiency2": discharge_conversion,
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
            TERMINAL_WATER_SINK_CAPACITY_MWH, 0.0,
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
            "efficiency": 1.0, "carrier": "water_spill",
            "marginal_cost": WATER_SPILL_MARGINAL_COST,
        })

    # PAP: separate Parambikulam/Thunacadavu, Sholayar and Upper Aliyar Stores.
    shol_mw, shol_e = capacity("hydro_sholayar")
    sark_mw, sark_e = capacity("hydro_sarkarpathy")
    aliyar_mw, aliyar_e = capacity("hydro_aliyar")
    para = add_store("Parambikulam", sark_e, 0.5, "observed_agriculture", "high", "Seasonal source for PAP.")
    thun = add_store("Thunacadavu", 0.15 * sark_e, 0.5, "endogenous_pondage", "medium", "Initial fraction may use the connected Parambikulam SOC proxy; weekly water-neutral thereafter.", initial_method="proxy_fraction", terminal_method="end_equals_start", resolution="none", weekly_balance="end_equals_start")
    shol = add_store("Sholayar", shol_e, 0.5, "observed_agriculture", "high", "Observed daily Agriculture storage/inflow.")
    # The available Parambikulam observation is a useful seasonal *initial-state*
    # proxy, but the reviewed PAP routing supports delivery to Aliyar rather than a
    # documented, direct Parambikulam-to-Upper-Aliyar transfer.  Do not recreate a
    # daily target from it: that forces water into Upper Aliyar without a physical
    # replenishment path.  The rolling state therefore carries forward after the
    # initial window.  Upper Aliyar's own Aliyar-headwater catchment is
    # represented by the CEA-calibrated inflow in build_inferred_cascade_inflows.
    upper = add_store(
        "Upper_Aliyar", aliyar_e, 0.5, "proxy_connected_reservoir", "medium",
        "Parambikulam fraction is used only to initialise Upper Aliyar; no verified direct transfer is modelled. Natural inflow is estimated from Aliyar's CEA monthly generation. Subsequent windows retain their solved state.",
        initial_method="proxy_fraction", terminal_method="end_equals_start",
        resolution="none", weekly_balance="end_equals_start",
        source_detail="Parambikulam proxy for initial state only; PAP routing review (IAMWARM/TNAU) does not verify a direct transfer to Upper Aliyar. Inflow: CEA Aliyar HPS monthly generation as a lower-bound catchment estimate.",
    )
    lower = add_store(
        "Lower_Aliyar", aliyar_e, 0.5, "endogenous_pondage", "low",
        "Receives Upper Aliyar turbine discharge and documented PAP Contour Canal / Aliyar Feeder deliveries from the Parambikulam group.",
    )
    pap_sink = add_store(
        "PAP_downstream",
        TERMINAL_WATER_SINK_CAPACITY_MWH,
        0.0,
        "downstream_sink", "medium",
        "Terminal sink for non-generating PAP irrigation releases, spill, and lower-pondage outflow.",
        initial_method="zero", terminal_method="none", resolution="none",
        weekly_balance="none",
    )
    links.append({"name": "pap_parambikulam_to_thunacadavu", "bus0": para, "bus1": thun, "p_nom": sark_mw / HYDRO_DEFAULT_EFFICIENCY,
                  "p_min_pu": 0.0, "efficiency": 1.0, "carrier": "water_transfer", "marginal_cost": 0.0})
    # IAMWARM documents that water from the Parambikulam group can be diverted
    # to Aliyar Reservoir through the Contour Canal and Aliyar Feeder Canal.
    # Neither that source nor the supplied water-source workbook provides a
    # canal rating or delivery schedule, so use the explicit existing PAP
    # unsurveyed-routing limit as a provisional bound rather than a claimed
    # measured capacity. This delivery enters Lower_Aliyar, never Upper_Aliyar.
    links.append({
        "name": "pap_contour_aliyar_feeder_to_lower_aliyar",
        "bus0": para, "bus1": lower,
        "p_nom": PAP_OBSERVED_RELEASE_CAPACITY_MW, "p_min_pu": 0.0,
        "efficiency": 1.0, "carrier": "water_transfer", "marginal_cost": 0.0,
    })
    links.extend([
        {"name": "pap_parambikulam_observed_release", "bus0": para, "bus1": pap_sink,
         "p_nom": PAP_OBSERVED_RELEASE_CAPACITY_MW, "p_min_pu": 0.0,
         "efficiency": 1.0, "carrier": "water_spill", "marginal_cost": WATER_SPILL_MARGINAL_COST},
        {"name": "pap_sholayar_observed_release", "bus0": shol, "bus1": pap_sink,
         "p_nom": PAP_OBSERVED_RELEASE_CAPACITY_MW, "p_min_pu": 0.0,
         "efficiency": 1.0, "carrier": "water_spill", "marginal_cost": WATER_SPILL_MARGINAL_COST},
        {"name": "pap_lower_aliyar_outflow", "bus0": lower, "bus1": pap_sink,
         "p_nom": PAP_OBSERVED_RELEASE_CAPACITY_MW, "p_min_pu": 0.0,
         "efficiency": 1.0, "carrier": "water_spill", "marginal_cost": WATER_SPILL_MARGINAL_COST},
    ])
    add_asset_turbines("sarkarpathy", thun, "Coimbatore", lower, "group-aggregated PAP storage", "pap_sarkarpathy_turbine")
    add_asset_turbines("sholayar", shol, "Coimbatore", lower, "individual Sholayar Store", "pap_sholayar_turbine")
    add_asset_turbines("aliyar", upper, "Coimbatore", lower, "group-aggregated PAP storage", "pap_aliyar_turbine")
    # Upper Aliyar spillway releases into the Aliyar River, which flows into
    # the lower Aliyar reservoir. p_nom is set to the peak estimated inflow
    # once the inflow series exists (set_spill_capacities).
    links.append({
        "name": "upper_aliyar_spill", "bus0": upper, "bus1": lower,
        "p_nom": np.nan, "p_min_pu": 0.0, "efficiency": 1.0,
        "carrier": "water_spill", "marginal_cost": WATER_SPILL_MARGINAL_COST,
    })

    # Kadamparai PSP: separate generation and pumping Links. Its natural
    # inflow is the CEA-calibrated estimate in build_inferred_cascade_inflows.
    kad_units = electrical_inventory.loc[electrical_inventory["official_component_id"].isin(kadamparai)]
    kad_mw = float(kad_units["official_p_nom_mw"].sum())
    kad = add_store("Kadamparai", kad_mw * KADAMPARAI_STORAGE_HOURS, 0.5, "assumed", "low", "No upper-reservoir observation: neutral initial fraction and weekly water-neutral boundary. Natural inflow is estimated from CEA generation net of modeled pumped generation; pumping is not natural inflow.", initial_method="assumed_fraction", terminal_method="end_equals_start", resolution="none", weekly_balance="end_equals_start")
    add_asset_turbines("kadmparai", kad, "Coimbatore", upper, "individual Kadamparai Store", "kadamparai_generation")
    # bus0 is the electrical side, so p_nom is the 400 MW pumping input.
    # The same water volume is added to Kadamparai (bus1) and withdrawn from
    # Upper Aliyar (bus2); losses fall on electricity only.
    links.append({"name": "kadamparai_pump", "bus0": "Coimbatore", "bus1": kad, "bus2": upper,
                  "p_nom": kad_mw, "p_min_pu": 0.0,
                  "efficiency": KADAMPARAI_PUMP_STORAGE_EFFICIENCY,
                  "efficiency2": -KADAMPARAI_PUMP_STORAGE_EFFICIENCY,
                  "carrier": "hydro_pump", "marginal_cost": 0.0})

    # Kodayar: the upper Store has no measured storage series. Its state evolves
    # from assumed initial water, estimated natural inflow, Kodayar-I use, and
    # spill when the estimated wet-season inflow would otherwise exceed FRL.
    kod_mw, _ = capacity("hydro_kodayar")
    kod_e = KODAYAR_UPPER_FRL_CAPACITY_MCM * MCM_TO_MCFT * KODAYAR_I_MWH_PER_MCFT
    kod_upper = add_store(
        "Upper_Kodayar", kod_e, 0.5, "assumed", "low",
        "Published Upper Kodayar FRL capacity and Kodayar-I water-use conversion; initial storage assumed half full. Subsequent storage follows estimated inflow minus Kodayar-I turbine withdrawal and explicit non-generating spill.",
        initial_method="assumed_fraction", terminal_method="none",
        resolution="none", weekly_balance="none",
        capacity_method="published_upper_frl_volume_x_kodayar_i_water_use_conversion",
        source_detail=f"{KODAYAR_CAPACITY_SOURCE} | {KODAYAR_I_CONVERSION_SOURCE}",
        head_m=KODAYAR_I_HEAD_FT * 0.3048,
        conversion_efficiency=(
            KODAYAR_I_MWH_PER_MCFT
            / (MCFT_M_HEAD_TO_MWH * KODAYAR_I_HEAD_FT * 0.3048)
        ),
    )
    kod_ii = add_store(
        "Kodayar_II_forebay", 0.10 * kod_e * KODAYAR_I_TO_II_WATER_CONVERSION, 0.5,
        "endogenous_pondage", "low",
        "Intermediate pondage (10% of Upper Kodayar volume), in Kodayar-II turbine-output MWh; no invented seasonal trajectory.",
    )
    kod_down = add_store(
        "Kodayar_downstream", TERMINAL_WATER_SINK_CAPACITY_MWH, 0.0,
        "downstream_sink", "low",
        "Effectively unbounded terminal sink for Kodayar-II turbine discharge; it does not limit upstream generation.",
        initial_method="zero", terminal_method="none",
        resolution="none", weekly_balance="none",
    )
    kod_units = electrical_inventory.loc[electrical_inventory["asset_id"].eq("kodayar")].sort_values("official_component_id")
    if len(kod_units) != 2:
        raise ValueError("Kodayar I-II must have two official inventory records")
    first_kod, second_kod = [row for _, row in kod_units.iterrows()]
    add_turbine("kodayar_I__" + str(first_kod.official_component_id), kod_upper, "Tirunelveli", kod_ii,
                float(first_kod.official_p_nom_mw), str(first_kod.official_component_id), "kodayar", "group-aggregated Kodayar storage",
                discharge_conversion=KODAYAR_I_TO_II_WATER_CONVERSION)
    add_turbine("kodayar_II__" + str(second_kod.official_component_id), kod_ii, "Tirunelveli", kod_down,
                float(second_kod.official_p_nom_mw), str(second_kod.official_component_id), "kodayar", "group-aggregated Kodayar storage")
    # The small common spill cost is necessary here because Upper Kodayar has
    # no terminal target; without it, gratuitous dumping of stored water would
    # be objective-neutral.
    links.append({
        "name": "upper_kodayar_spill", "bus0": kod_upper, "bus1": kod_down,
        # No surveyed spillway rating is available. p_nom is set after the
        # inflow series is built: the peak hourly estimated inflow (see
        # set_spill_capacities).
        "p_nom": np.nan,
        "p_min_pu": 0.0, "efficiency": 1.0, "carrier": "water_spill",
        "marginal_cost": WATER_SPILL_MARGINAL_COST,
    })

    # Nilgiris/Kundah and Pykara cascades: observed seasonal data are added later;
    # absent series use documented neutral initial values and endogenous pondage.
    kund_mw, kund_e = capacity("hydro_kundah")
    _, parsons_e = capacity("hydro_parsons_valley")
    kund_upper = add_store("Upper_Bhavani", kund_e, 0.5, "monthly_group_energy", "low", "SRPC monthly stored-energy boundary method; provisional neutral fraction until the series is loaded.", initial_method="monthly_interpolated", terminal_method="monthly_interpolated_tolerance", resolution="monthly", weekly_balance="seasonal_interpolated")
    kund_down = add_store("Kundah_pondage", 0.10 * kund_e, 0.5, "endogenous_pondage", "low", "Aggregated intermediate Kundah pondage.")
    kund_sink = add_store(
        "Kundah_downstream", TERMINAL_WATER_SINK_CAPACITY_MWH, 0.0,
        "downstream_sink", "low",
        "Effectively unbounded terminal sink for Parsons Valley turbine discharge; it does not limit upstream generation.",
        initial_method="zero", terminal_method="none",
        resolution="none", weekly_balance="none",
    )
    add_asset_turbines("kundah", kund_upper, "Coimbatore", kund_down, "group-aggregated Kundah storage", "kundah_turbine", "Official Kundah units share the first-pass aggregate hydraulic stage; no 585 MW source-table total is used.")
    add_asset_turbines("parsons_valley", kund_down, "Coimbatore", kund_sink, "Kundah downstream pondage", "parsons_valley_turbine")
    links.append({"name": "kundah_pondage_bypass_release", "bus0": kund_down, "bus1": kund_sink,
                  "p_nom": kund_mw, "p_min_pu": 0.0, "efficiency": 1.0,
                  "carrier": "water_spill", "marginal_cost": WATER_SPILL_MARGINAL_COST})
    pyk_mw, pyk_e = capacity("hydro_pykara")
    pus_mw, pus_e = capacity("hydro_pykara_ultimate")
    moy_mw, moy_e = capacity("hydro_moyar")
    pyk = add_store("Pykara", pyk_e, 0.5, "monthly_group_energy", "low", "SRPC monthly stored-energy boundary method; provisional neutral fraction until the series is loaded.", initial_method="monthly_interpolated", terminal_method="monthly_interpolated_tolerance", resolution="monthly", weekly_balance="seasonal_interpolated")
    glen = add_store("Glenmorgan_forebay", 0.10 * pus_e, 0.5, "endogenous_pondage", "low", "No artificial seasonal forebay profile.")
    moy = add_store("Moyar_pondage", 0.10 * moy_e, 0.5, "endogenous_pondage", "low", "Downstream Pykara cascade pondage.")
    sink = add_store(
        "Pykara_downstream", TERMINAL_WATER_SINK_CAPACITY_MWH, 0.0,
        "downstream_sink", "low",
        "Effectively unbounded terminal sink for Moyar turbine discharge; it does not limit upstream generation.",
        initial_method="zero", terminal_method="none",
        resolution="none", weekly_balance="none",
    )
    add_asset_turbines("pykara", pyk, "Coimbatore", glen, "individual Pykara Store", "pykara_turbine", "Official Pykara units share one first-pass hydraulic stage; no 61.2 MW grouped reference is used.")
    add_asset_turbines("pykara_ultimate", glen, "Coimbatore", moy, "Pykara group forebay", "pushep_turbine")
    add_asset_turbines("moyar", moy, "Coimbatore", sink, "Pykara/Moyar group pondage", "moyar_turbine", "Official Moyar 3x12 MW units share one first-pass hydraulic stage.")
    links.append({"name": "moyar_pondage_bypass_release", "bus0": moy, "bus1": sink,
                  "p_nom": pus_mw + moy_mw, "p_min_pu": 0.0, "efficiency": 1.0,
                  "carrier": "water_spill", "marginal_cost": WATER_SPILL_MARGINAL_COST})

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
        TERMINAL_WATER_SINK_CAPACITY_MWH,
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
                "marginal_cost": WATER_SPILL_MARGINAL_COST,
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


# Each spill must pass every mandatory inflow that reaches its reservoir.
# Upper Aliyar also receives Kadamparai's natural inflow via the Kadamparai
# turbines, since Kadamparai's own Store is small and has no spill.
SPILL_INFLOWS = {
    "upper_kodayar_spill": ["upper_kodayar_estimated_inflow"],
    "upper_aliyar_spill": [UPPER_ALIYAR_INFLOW, KADAMPARAI_INFLOW],
}


def set_spill_capacities(
    hydraulic_links: pd.DataFrame, inflow_generators: pd.DataFrame,
) -> pd.DataFrame:
    """Cap each estimated-inflow reservoir's spill at its peak hourly inflow.

    These inflows are mandatory and the reservoirs have no storage targets in
    rolling runs, so spill only ever needs to pass incoming water. Spilling the
    full inflow keeps the level unchanged in any hour, so this cap cannot make
    the model infeasible.
    """
    hydraulic_links = hydraulic_links.copy()
    for spill_name, inflow_names in SPILL_INFLOWS.items():
        spill = hydraulic_links["name"].eq(spill_name)
        if spill.sum() != 1:
            raise ValueError(f"Expected one {spill_name} Link")
        peak_mw = 0.0
        for inflow_name in inflow_names:
            inflow = inflow_generators["name"].eq(inflow_name)
            if inflow.sum() != 1:
                raise ValueError(f"Expected one {inflow_name} inflow")
            peak = float(inflow_generators.loc[inflow, "p_nom"].iloc[0])
            if not np.isfinite(peak) or peak <= 0:
                raise ValueError(f"{inflow_name} peak inflow must be positive and finite")
            peak_mw += peak
        hydraulic_links.loc[spill, "p_nom"] = peak_mw
    return hydraulic_links


def build_water_inflow_generators(
    water_buses: pd.DataFrame, snapshots: pd.Series, upper_kodayar_capacity_mwh: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Create observed inflows and a labelled Pechiparai proxy for Upper Kodayar."""
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
        daily = raw.loc[raw["reservoir"].eq(reservoir)].set_index("date")["current_inflow_cusec"].fillna(0.0)
        mw = index.normalize().map(daily).fillna(0.0).to_numpy() * 0.0864 * MCFT_M_HEAD_TO_MWH * HYDRO_DEFAULT_HEAD_M * HYDRO_DEFAULT_EFFICIENCY / 24.0
        p_nom = max(float(np.max(mw)), 1e-6)
        name = f"{slugify(reservoir)}_natural_inflow"
        rows.append({"name": name, "bus": bus, "p_nom": p_nom, "p_min_pu": 0.0, "p_max_pu": 1.0,
                     "carrier": "water_inflow", "marginal_cost": 0.0, "committable": False})
        profiles[name] = mw / p_nom
    if "water_upper_kodayar" not in set(water_buses["name"]):
        raise ValueError("Upper Kodayar water bus is missing")
    pechiparai = raw.loc[raw["reservoir"].eq("Pechiparai")].set_index("date").sort_index()
    if pechiparai.index.has_duplicates or pechiparai.empty:
        raise ValueError("Expected one Pechiparai inflow reading per date")
    dates = index.normalize().unique()
    pechiparai = pechiparai.reindex(dates)
    if pechiparai[["current_inflow_cusec", "full_capacity_mcft"]].isna().any().any():
        raise ValueError("Pechiparai inflow/capacity is missing for an Upper Kodayar model date")
    if (pechiparai["current_inflow_cusec"] < 0).any():
        raise ValueError("Pechiparai inflow must be nonnegative")
    expected_capacity_mwh = (
        KODAYAR_UPPER_FRL_CAPACITY_MCM * MCM_TO_MCFT * KODAYAR_I_MWH_PER_MCFT
    )
    if not np.isclose(upper_kodayar_capacity_mwh, expected_capacity_mwh):
        raise ValueError("Upper Kodayar capacity does not match its published volume")
    pechiparai_daily_mcft = pechiparai["current_inflow_cusec"] * 0.0864
    inflow_scale = KODAYAR_CAPACITY_SCALE
    estimated_daily_mcft = pechiparai_daily_mcft * inflow_scale
    estimated_daily_mwh = estimated_daily_mcft * KODAYAR_I_MWH_PER_MCFT
    hourly_mw = index.normalize().map(estimated_daily_mwh).to_numpy() / 24.0
    peak_mw = float(np.max(hourly_mw))
    if peak_mw <= 0:
        raise ValueError("Upper Kodayar estimated inflow must be positive")
    rows.append({
        "name": "upper_kodayar_estimated_inflow",
        "bus": "water_upper_kodayar",
        "p_nom": peak_mw,
        "p_min_pu": 0.0,
        "p_max_pu": 1.0,
        "carrier": "water_inflow",
        "marginal_cost": 0.0,
        "committable": False,
    })
    profiles["upper_kodayar_estimated_inflow"] = hourly_mw / peak_mw
    kodayar_audit = pd.DataFrame({
        "date": dates,
        "proxy_reservoir": "Pechiparai",
        "proxy_inflow_cusec": pechiparai["current_inflow_cusec"].to_numpy(),
        "proxy_full_capacity_mcft": pechiparai["full_capacity_mcft"].to_numpy(),
        "upper_kodayar_capacity_mwh_equiv": upper_kodayar_capacity_mwh,
        "capacity_ratio": KODAYAR_CAPACITY_SCALE,
        "inflow_scale": inflow_scale,
        "upper_frl_capacity_mcm": KODAYAR_UPPER_FRL_CAPACITY_MCM,
        "pechiparai_frl_capacity_mcm": KODAYAR_PECHIPARAI_FRL_CAPACITY_MCM,
        "capacity_source": KODAYAR_CAPACITY_SOURCE,
        "kodayar_i_mwh_per_mcft": KODAYAR_I_MWH_PER_MCFT,
        "kodayar_i_water_use_source": KODAYAR_I_CONVERSION_SOURCE,
        "estimated_upper_inflow_mcft": estimated_daily_mcft.to_numpy(),
        "estimated_upper_inflow_mwh_equiv": estimated_daily_mwh.to_numpy(),
        "method": "Pechiparai daily inflow scaled by the published Upper Kodayar/Pechiparai FRL capacity ratio",
    })
    return pd.DataFrame(rows), pd.DataFrame(profiles, index=pd.RangeIndex(len(index))), kodayar_audit


def build_inferred_cascade_inflows(
    water_buses: pd.DataFrame, snapshots: pd.Series, targets: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Infer weekly calibration water allowances and preserve routed water.

    Water uses the model's existing MWh-equivalent hydraulic basis. Kundah
    water is injected once at Upper Bhavani and can then serve Parsons Valley.
    Pykara Ultimate exceeds upstream Pykara generation, so its residual is an
    explicit Glenmorgan forebay diversion rather than hidden Pykara inflow.

    Upper Aliyar is a dam on the Aliyar headwaters with its own catchment but
    no flow or storage observation; Parambikulam water does not reach it.
    The Aliyar power house is its only generating outlet and Kadamparai
    pumping returns the water it takes, so the natural inflow must supply at
    least Aliyar's observed CEA generation. That generation is used as the
    estimated inflow (a lower bound: any spill is not observed).

    Kadamparai's natural inflow is its CEA generation net of the model's own
    pumped generation (KADAMPARAI_MODELED_PUMPED_GENERATION_GWH), following
    the CEA monthly Kadamparai shape.
    """
    index = pd.DatetimeIndex(pd.to_datetime(snapshots))
    weekly = targets.pivot(
        index="week_start", columns="asset_id", values="weekly_generation_target_mwh"
    )
    weekly.index = pd.DatetimeIndex(weekly.index)
    week_hours = (
        targets.drop_duplicates("week_start").set_index("week_start")["week_days"] * 24.0
    )
    week_hours.index = pd.DatetimeIndex(week_hours.index)
    snapshot_week = FY_START + pd.to_timedelta(
        ((index.normalize() - FY_START).days // 7) * 7, unit="D"
    )
    if weekly.isna().any().any() or not pd.Index(snapshot_week).isin(weekly.index).all():
        raise ValueError("Cascade calibration targets do not cover all rolling weeks")
    kadamparai_weekly = cea_weekly_generation_mwh("kadmparai")
    kadamparai_annual_gwh = kadamparai_weekly.attrs["annual_generation_gwh"]
    kadamparai_cea = kadamparai_weekly.set_index("week_start")["weekly_generation_mwh"]
    kadamparai_cea.index = pd.DatetimeIndex(kadamparai_cea.index)
    if not 0 <= KADAMPARAI_MODELED_PUMPED_GENERATION_GWH < kadamparai_annual_gwh:
        raise ValueError("Kadamparai pumped-generation calibration must be below its CEA generation")
    kadamparai_natural = kadamparai_cea.reindex(weekly.index) * (
        1.0 - KADAMPARAI_MODELED_PUMPED_GENERATION_GWH / kadamparai_annual_gwh
    )
    requirements = pd.DataFrame({
        "kundah_upper_bhavani_calibration_inflow": weekly["kundah"],
        "pykara_calibration_inflow": weekly["pykara"],
        "glenmorgan_diversion_calibration_inflow": (
            weekly["pykara_ultimate"] - weekly["pykara"]
        ).clip(lower=0.0),
        UPPER_ALIYAR_INFLOW: weekly["aliyar"],
        KADAMPARAI_INFLOW: kadamparai_natural,
    }, index=weekly.index)
    buses = {
        "kundah_upper_bhavani_calibration_inflow": "water_upper_bhavani",
        "pykara_calibration_inflow": "water_pykara",
        "glenmorgan_diversion_calibration_inflow": "water_glenmorgan_forebay",
        UPPER_ALIYAR_INFLOW: "water_upper_aliyar",
        KADAMPARAI_INFLOW: "water_kadamparai",
    }
    if set(buses.values()) - set(water_buses["name"]):
        raise ValueError("Expected cascade calibration water buses are missing")
    rows: list[dict[str, object]] = []
    profiles: dict[str, np.ndarray] = {}
    audit_rows: list[pd.DataFrame] = []
    for name, target in requirements.items():
        hourly_mw = (
            pd.Index(snapshot_week).map(target).to_numpy()
            / pd.Index(snapshot_week).map(week_hours).to_numpy()
        )
        p_nom = max(float(hourly_mw.max()), 1e-6)
        rows.append({"name": name, "bus": buses[name], "p_nom": p_nom,
                     "p_min_pu": 0.0, "p_max_pu": 1.0, "carrier": "water_inflow",
                     "marginal_cost": 0.0, "committable": False})
        profiles[name] = hourly_mw / p_nom
        audit_rows.append(pd.DataFrame({
            "week_start": target.index,
            "week_end": target.index.map(
                targets.drop_duplicates("week_start").set_index("week_start")["week_end"]
            ),
            "source_component": name, "water_bus": buses[name],
            "inferred_inflow_mwh": target.to_numpy(),
            "method": "weekly calibration water allowance delivered uniformly within the rolling week",
        }))
    audit = pd.concat(audit_rows, ignore_index=True)
    water_mwh_per_mcft = MCFT_M_HEAD_TO_MWH * HYDRO_DEFAULT_HEAD_M * HYDRO_DEFAULT_EFFICIENCY
    audit["inferred_inflow_mcft_at_assumed_head"] = audit["inferred_inflow_mwh"] / water_mwh_per_mcft
    audit["inferred_inflow_mcm_at_assumed_head"] = audit["inferred_inflow_mcft_at_assumed_head"] / MCM_TO_MCFT
    audit["volume_conversion_basis"] = f"{HYDRO_DEFAULT_HEAD_M:g} m head; {HYDRO_DEFAULT_EFFICIENCY:.0%} efficiency"
    return (pd.DataFrame(rows), pd.DataFrame(profiles, index=pd.RangeIndex(len(index))), audit)


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


def cea_start_up_series(physical_unit_mw: float) -> int:
    """Map a physical unit rating to the CEA start-up oil norm series."""
    if physical_unit_mw < 300:
        return 210
    if physical_unit_mw < 650:
        return 500
    if physical_unit_mw < 750:
        return 660
    return 800


def coal_cycle_cost_per_mw(physical_unit_mw: float) -> float:
    """Return the stop-start cycling cost (INR per MW of capacity) for a unit.

    The CEA Tuticorin two-shift cost is measured on 210 MW units. Larger units
    need less start-up oil per MW (CEA 2025 hot-start norms), so the per-MW
    cost is scaled by the ratio of oil per start per MW of the unit's series to
    that of the 210 MW series.
    """
    series = cea_start_up_series(physical_unit_mw)
    reference = COAL_CYCLE_COST_REFERENCE_SERIES_MW
    scale = (CEA_HOT_START_OIL_KL[series] / series) / (
        CEA_HOT_START_OIL_KL[reference] / reference
    )
    return COAL_CYCLE_COST_PER_MW * scale


def apply_coal_operating_costs(
    generators: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Set station energy charges and size-scaled cycling costs for coal.

    Every coal component must match exactly one row of
    ``coal_operating_costs.csv``. Committable units get equal start-up and
    shut-down costs (half the cycle cost each), which keeps PyPSA's
    tightening constraints for linearized unit commitment active.
    """
    table = pd.read_csv(COAL_OPERATING_COST_FILE)
    generators = generators.copy()
    coal = generators["carrier"].eq("coal")
    names = generators.loc[coal, "name"].astype(str)
    matched_row = pd.Series(-1, index=names.index)
    for position, row in table.iterrows():
        hits = names.str.fullmatch(row["component_regex"])
        if not hits.any():
            raise ValueError(f"Coal cost row matches no component: {row['component_regex']}")
        if (matched_row[hits] >= 0).any():
            raise ValueError(f"Coal components match more than one cost row: {row['component_regex']}")
        matched_row[hits] = position
    if (matched_row < 0).any():
        missing = names[matched_row < 0].tolist()
        raise ValueError(f"Coal components without an operating-cost row: {missing}")

    audit_rows: list[dict[str, object]] = []
    for index, position in matched_row.items():
        row = table.loc[position]
        generators.at[index, "marginal_cost"] = float(row["energy_charge_inr_per_mwh"])
        committable = bool(generators.at[index, "committable"])
        physical = row["physical_unit_mw"]
        cycle_cost = np.nan
        series = np.nan
        if committable:
            if pd.isna(physical):
                raise ValueError(f"Committable coal unit {names[index]} needs physical_unit_mw")
            series = cea_start_up_series(float(physical))
            cycle_cost = coal_cycle_cost_per_mw(float(physical))
            transition = float(generators.at[index, "p_nom"]) * cycle_cost / 2.0
            generators.at[index, "start_up_cost"] = transition
            generators.at[index, "shut_down_cost"] = transition
        audit_rows.append({
            "name": names[index],
            "station": row["station"],
            "p_nom_mw": float(generators.at[index, "p_nom"]),
            "committable": committable,
            "energy_charge_inr_per_mwh": float(row["energy_charge_inr_per_mwh"]),
            "charge_basis": row["charge_basis"],
            "source_id": row["source_id"],
            "physical_unit_mw": physical,
            "cea_start_up_series_mw": series,
            "cycle_cost_inr_per_mw": cycle_cost,
            "start_up_cost_inr": generators.at[index, "start_up_cost"] if committable else 0.0,
        })
    return generators, pd.DataFrame(audit_rows)


def apply_plant_operating_costs(
    generators: pd.DataFrame, path: Path = PLANT_OPERATING_COST_FILE,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Set official energy charges for oil/gas, nuclear, bio-power and diesel.

    Rows of ``plant_operating_costs.csv`` are matched to components of their
    carrier by ``component_regex`` in file order; the first matching row wins,
    so a catch-all row (``.*``) can follow specific ones. Every component of a
    listed carrier must match a row.
    """
    table = pd.read_csv(path, dtype={"component_regex": str})
    generators = generators.copy()
    audit_rows: list[dict[str, object]] = []
    for carrier, rows in table.groupby("carrier", sort=False):
        units = generators.index[generators["carrier"].eq(carrier)]
        if not len(units):
            raise ValueError(f"Operating-cost rows for {carrier} but no {carrier} components")
        names = generators.loc[units, "name"].astype(str)
        unmatched = pd.Series(True, index=units)
        for _, row in rows.iterrows():
            hits = unmatched & names.str.contains(row["component_regex"], regex=True)
            if not hits.any():
                raise ValueError(f"{carrier} cost row matches no component: {row['component_regex']}")
            for index in units[hits.to_numpy()]:
                generators.at[index, "marginal_cost"] = float(row["energy_charge_inr_per_mwh"])
                audit_rows.append({
                    "name": names[index],
                    "carrier": carrier,
                    "station": row["station"],
                    "p_nom_mw": float(generators.at[index, "p_nom"]),
                    "energy_charge_inr_per_mwh": float(row["energy_charge_inr_per_mwh"]),
                    "basis": row["basis"],
                    "source": row["source"],
                })
            unmatched &= ~hits
        if unmatched.any():
            raise ValueError(
                f"{carrier} components without an operating-cost row: {names[unmatched].tolist()}"
            )
    return generators, pd.DataFrame(audit_rows)


def set_coal_initially_online(generators: pd.DataFrame) -> pd.DataFrame:
    """Start committable coal units online instead of cold.

    The template marks every thermal unit as offline before the first hour
    (``down_time_before`` = minimum down time), which makes the whole coal
    fleet start at 00:00 on 1 April. Observed Tamil Nadu coal generation on
    1 April 2025 was 196.6 MU (about 8.2 GW on average), so the fleet was
    running. Committable coal units are therefore online before the horizon,
    with no first-hour ramp reference (``p_init`` unknown) so the optimizer
    chooses the opening dispatch and pays shut-down costs for units it turns
    off. Other committable units keep the template's offline start.
    """
    generators = generators.copy()
    coal = generators["carrier"].eq("coal") & generators["committable"].fillna(False).astype(bool)
    longest = generators.loc[coal, ["min_up_time", "min_down_time"]].max().max()
    if coal.any() and COAL_INITIAL_UP_HOURS < longest:
        raise ValueError("COAL_INITIAL_UP_HOURS must cover the longest minimum up/down time")
    generators.loc[coal, "up_time_before"] = COAL_INITIAL_UP_HOURS
    generators.loc[coal, "down_time_before"] = 0
    generators.loc[coal, "p_init"] = np.nan
    return generators


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


def restrict_unit_commitment(generators: pd.DataFrame) -> pd.DataFrame:
    """Keep commitment decisions only for coal; dispatch other thermal freely.

    Oil/gas, diesel and bio-power are small, fast units whose output is
    already pinned by observed energy targets, and their cycling cost is a
    small fraction of fuel cost (Indian Technology Catalogue 2022). Making
    them non-committable removes about two-thirds of the unit-commitment
    variables. Without a status variable PyPSA would treat ``p_min_pu`` as a
    must-run floor, so it is set to zero together with all commitment-only
    attributes. Their unit ramp limits of 1.0 p.u./h can never bind, so they
    are dropped as well.
    """
    generators = generators.copy()
    committable = generators["committable"].fillna(False).astype(bool)
    released = committable & ~generators["carrier"].isin(COMMITTABLE_CARRIERS)
    if (generators.loc[released, ["ramp_limit_up", "ramp_limit_down"]].fillna(1.0) < 1.0).any(axis=None):
        raise ValueError("Released units with binding ramp limits need explicit handling")
    generators.loc[released, "committable"] = False
    generators.loc[released, ["p_min_pu", "start_up_cost", "shut_down_cost"]] = 0.0
    generators.loc[released, ["min_up_time", "min_down_time", "down_time_before"]] = 0
    generators.loc[released, "up_time_before"] = 1
    generators.loc[released, [
        "p_init", "ramp_limit_up", "ramp_limit_down",
        "ramp_limit_start_up", "ramp_limit_shut_down",
    ]] = np.nan
    return generators


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


def _place_by_share(totals: np.ndarray, shares: np.ndarray, room: np.ndarray) -> np.ndarray:
    """Split hourly totals over gateways by share, refilling where capacity runs out."""
    result = np.zeros((len(totals), len(shares)))
    for hour, total in enumerate(totals):
        remaining = float(total)
        active = (shares > 0.0) & (room[hour] > 1e-9)
        while remaining > 1e-7 and active.any():
            weights = shares * active
            addition = np.maximum(np.minimum(remaining * weights / weights.sum(), room[hour] - result[hour]), 0.0)
            result[hour] += addition
            remaining -= float(addition.sum())
            active = (shares > 0.0) & (room[hour] - result[hour] > 1e-9)
    return result


def build_interstate_flow_loads(
    buses: pd.DataFrame, grid_links: pd.DataFrame, snapshots: pd.Series
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Place audited REA flows at external buses without fixing individual links.

    Resolved flows retain their audited gateway. Unresolved exports are
    distributed using direction-specific annual resolved-flow shares, with a
    capacity-aware water-filling pass for amounts that do not fit at a gateway.
    Unresolved imports are retained in the audit but excluded from the model so
    that only confirmed imports affect dispatch.

    With ``INTERCHANGE_BASIS = "metered"`` a correction load is added each hour
    (an import when the transactions deliver too little, an export when too
    much), split over the gateways by the same shares, so that the net import
    delivered into Tamil Nadu after interconnector losses equals the metered
    net import of the Tamil Nadu area (actual drawal minus the output of the
    interstate-grid plants inside Tamil Nadu).
    """
    gateway_order = ("Andhra Pradesh", "Karnataka", "Kerala", "Western Region")
    directions = ("import", "export")
    measures = ("resolved", "unresolved_distributed")
    expected_gwh = {
        ("import", "resolved"): 27_765.9794200001,
        ("import", "unresolved_distributed"): 20_826.9511525,
        ("export", "resolved"): 15_504.698395,
        ("export", "unresolved_distributed"): 8_000.055805,
    }
    if len(snapshots) != 8_760:
        raise ValueError("REA interstate flows require the complete 8,760-hour FY2025-26 model")

    flows = pd.read_csv(REA_INTERSTATE_HOURLY_FLOW_FILE, parse_dates=["snapshot"])
    shares = pd.read_csv(REA_INTERSTATE_GATEWAY_SHARE_FILE)
    required_flow = {"snapshot", "direction", "measure", "gateway", "flow_MW"}
    required_share = {"direction", "gateway", "allocation_share"}
    if missing := sorted(required_flow - set(flows.columns)):
        raise ValueError(f"REA hourly-flow input is missing columns: {missing}")
    if missing := sorted(required_share - set(shares.columns)):
        raise ValueError(f"REA gateway-share input is missing columns: {missing}")
    model_snapshots = pd.to_datetime(snapshots, errors="raise")
    if not flows["direction"].isin(directions).all():
        raise ValueError("REA flow directions must be import or export")
    if not flows["measure"].isin(["resolved", "unresolved_total"]).all():
        raise ValueError("REA source measures must be resolved or unresolved_total")
    if flows["flow_MW"].isna().any() or flows["flow_MW"].lt(0.0).any():
        raise ValueError("REA hourly flows must be finite and non-negative")
    if set(flows["snapshot"].unique()) != set(model_snapshots):
        raise ValueError("REA hourly-flow snapshots do not match the model snapshots")
    if set(shares["gateway"]) != set(gateway_order):
        raise ValueError("REA allocation shares must cover the four external gateways")
    if not np.allclose(shares.groupby("direction")["allocation_share"].sum(), 1.0):
        raise ValueError("REA allocation shares must sum to one for each direction")
    unknown_buses = sorted(set(gateway_order) - set(buses["name"]))
    if unknown_buses:
        raise ValueError(f"REA gateways are absent from the network buses: {unknown_buses}")

    gateway_set = set(gateway_order)
    capacities: dict[tuple[str, str], float] = {}
    for direction in directions:
        for gateway in gateway_order:
            if direction == "import":
                mask = grid_links["bus0"].eq(gateway) & ~grid_links["bus1"].isin(gateway_set)
            else:
                mask = grid_links["bus1"].eq(gateway) & ~grid_links["bus0"].isin(gateway_set)
            links = grid_links.loc[mask]
            capacities[(direction, gateway)] = float(
                (pd.to_numeric(links["p_nom"], errors="raise")
                 * pd.to_numeric(links["p_max_pu"], errors="raise")).sum()
            )
            if capacities[(direction, gateway)] <= 0.0:
                raise ValueError(f"No {direction} capacity found for gateway {gateway}")

    resolved = flows.loc[flows["measure"].eq("resolved")].pivot_table(
        index="snapshot", columns=["direction", "gateway"], values="flow_MW",
        aggfunc="sum", fill_value=0.0,
    ).reindex(model_snapshots, fill_value=0.0)
    unresolved = flows.loc[flows["measure"].eq("unresolved_total")].pivot_table(
        index="snapshot", columns="direction", values="flow_MW",
        aggfunc="sum", fill_value=0.0,
    ).reindex(model_snapshots, fill_value=0.0)
    share_lookup = shares.set_index(["direction", "gateway"])["allocation_share"]

    allocated: dict[tuple[str, str], np.ndarray] = {}
    initial_allocation: dict[tuple[str, str], np.ndarray] = {}
    redistribution: dict[tuple[str, str], np.ndarray] = {}
    limited: dict[tuple[str, str], np.ndarray] = {}
    for direction in directions:
        direction_shares = np.array([
            float(share_lookup.loc[(direction, gateway)]) for gateway in gateway_order
        ])
        direction_capacities = np.array([
            capacities[(direction, gateway)] for gateway in gateway_order
        ])
        resolved_values = np.column_stack([
            resolved.get((direction, gateway), pd.Series(0.0, index=model_snapshots)).to_numpy()
            for gateway in gateway_order
        ])
        if (resolved_values > direction_capacities + 1e-6).any():
            row, column = np.argwhere(resolved_values > direction_capacities + 1e-6)[0]
            raise ValueError(
                f"Resolved {direction} flow exceeds {gateway_order[column]} capacity at "
                f"{model_snapshots[row]}"
            )
        unresolved_values = unresolved.get(
            direction, pd.Series(0.0, index=model_snapshots)
        ).to_numpy()
        initial = unresolved_values[:, None] * direction_shares[None, :]
        result = np.zeros_like(initial)
        for hour, total in enumerate(unresolved_values):
            remaining_capacity = np.maximum(direction_capacities - resolved_values[hour], 0.0)
            remaining = float(total)
            active = (direction_shares > 0.0) & (remaining_capacity > 1e-9)
            while remaining > 1e-7 and active.any():
                weights = direction_shares * active
                proposal = remaining * weights / weights.sum()
                addition = np.minimum(proposal, remaining_capacity - result[hour])
                addition = np.maximum(addition, 0.0)
                result[hour] += addition
                remaining -= float(addition.sum())
                active = (direction_shares > 0.0) & (
                    remaining_capacity - result[hour] > 1e-9
                )
            if remaining > 1e-5:
                raise ValueError(
                    f"Cannot place {remaining:.6f} MW of unresolved {direction} flow at "
                    f"{model_snapshots[hour]} after applying gateway capacities"
                )
        if not np.allclose(result.sum(axis=1), unresolved_values, atol=1e-5):
            raise AssertionError(f"Unresolved {direction} allocation does not conserve hourly flow")
        for column, gateway in enumerate(gateway_order):
            allocated[(direction, gateway)] = result[:, column]
            initial_allocation[(direction, gateway)] = initial[:, column]
            redistribution[(direction, gateway)] = result[:, column] - initial[:, column]
            limited[(direction, gateway)] = ~np.isclose(
                result[:, column], initial[:, column], atol=1e-7
            )

    load_rows: list[dict[str, str]] = []
    profile_data: dict[str, np.ndarray] = {}
    audit_rows: list[pd.DataFrame] = []
    for direction in directions:
        sign = -1.0 if direction == "import" else 1.0
        unresolved_total = unresolved.get(
            direction, pd.Series(0.0, index=model_snapshots)
        ).to_numpy()
        for gateway in gateway_order:
            resolved_values = resolved.get(
                (direction, gateway), pd.Series(0.0, index=model_snapshots)
            ).to_numpy()
            for measure in measures:
                values = resolved_values if measure == "resolved" else allocated[(direction, gateway)]
                load_name = f"interstate_{measure}_{direction}_{slugify(gateway)}"
                included_in_model = not (
                    direction == "import" and measure == "unresolved_distributed"
                )
                if included_in_model:
                    load_rows.append({
                        "name": load_name, "bus": gateway,
                        "carrier": "electricity",
                    })
                    profile_data[load_name] = sign * values
                audit_rows.append(pd.DataFrame({
                    "snapshot": model_snapshots, "direction": direction,
                    "measure": measure, "gateway": gateway,
                    "load_name": load_name if included_in_model else "",
                    "flow_MW": values,
                    "modeled_flow_MW": values if included_in_model else np.zeros(len(values)),
                    "external_load_p_set_MW": (
                        sign * values if included_in_model else np.zeros(len(values))
                    ),
                    "included_in_model": included_in_model,
                    "allocation_share": float(share_lookup.loc[(direction, gateway)]),
                    "gateway_capacity_MW": capacities[(direction, gateway)],
                    "resolved_flow_MW": resolved_values,
                    "unresolved_total_MW": unresolved_total,
                    "pre_capacity_allocation_MW": (
                        np.zeros(len(model_snapshots)) if measure == "resolved"
                        else initial_allocation[(direction, gateway)]
                    ),
                    "capacity_redistribution_MW": (
                        np.zeros(len(model_snapshots)) if measure == "resolved"
                        else redistribution[(direction, gateway)]
                    ),
                    "capacity_limited": (
                        np.zeros(len(model_snapshots), dtype=bool) if measure == "resolved"
                        else limited[(direction, gateway)]
                    ),
                    "status": (
                        "audited_resolved" if measure == "resolved"
                        else (
                            "unresolved_distributed_not_verified"
                            if included_in_model
                            else "unresolved_distributed_excluded_from_model"
                        )
                    ),
                }))

    if INTERCHANGE_BASIS == "metered":
        # Net import delivered into Tamil Nadu by the loads so far: imports
        # arrive after the interconnector loss, exports leave before it.
        efficiency = INTERCONNECTOR_EFFICIENCY
        flows_so_far = {}
        for direction in directions:
            for gateway in gateway_order:
                flow = resolved.get((direction, gateway), pd.Series(0.0, index=model_snapshots)).to_numpy()
                if direction == "export":  # unresolved imports stay out of the model
                    flow = flow + allocated[(direction, gateway)]
                flows_so_far[(direction, gateway)] = flow
        delivered = sum(
            efficiency * flows_so_far[("import", g)] - flows_so_far[("export", g)] / efficiency
            for g in gateway_order
        )
        metered = pd.read_csv(TN_METERED_INTERCHANGE_FILE, parse_dates=["snapshot"]).set_index("snapshot")
        if set(metered.index) != set(model_snapshots):
            raise ValueError("Metered interchange snapshots do not match the model snapshots")
        target = metered["net_import_mw"].reindex(model_snapshots).to_numpy()
        gap = target - delivered
        corrections = {
            "import": np.maximum(gap, 0.0) / efficiency,
            "export": np.maximum(-gap, 0.0) * efficiency,
        }
        for direction in directions:
            sign = -1.0 if direction == "import" else 1.0
            direction_shares = np.array([float(share_lookup.loc[(direction, g)]) for g in gateway_order])
            room = np.column_stack([
                np.maximum(capacities[(direction, g)] - flows_so_far[(direction, g)], 0.0)
                for g in gateway_order
            ])
            placed = _place_by_share(corrections[direction], direction_shares, room)
            if not np.allclose(placed.sum(axis=1), corrections[direction], atol=1e-5):
                raise ValueError(f"Cannot place the metered {direction} correction within gateway capacity")
            for column, gateway in enumerate(gateway_order):
                values = placed[:, column]
                load_name = f"interstate_metered_correction_{direction}_{slugify(gateway)}"
                load_rows.append({"name": load_name, "bus": gateway, "carrier": "electricity"})
                profile_data[load_name] = sign * values
                zeros = np.zeros(len(model_snapshots))
                audit_rows.append(pd.DataFrame({
                    "snapshot": model_snapshots, "direction": direction,
                    "measure": "metered_correction", "gateway": gateway,
                    "load_name": load_name, "flow_MW": values, "modeled_flow_MW": values,
                    "external_load_p_set_MW": sign * values, "included_in_model": True,
                    "allocation_share": float(share_lookup.loc[(direction, gateway)]),
                    "gateway_capacity_MW": capacities[(direction, gateway)],
                    "resolved_flow_MW": zeros, "unresolved_total_MW": zeros,
                    "pre_capacity_allocation_MW": corrections[direction] * direction_shares[column],
                    "capacity_redistribution_MW": values - corrections[direction] * direction_shares[column],
                    "capacity_limited": ~np.isclose(values, corrections[direction] * direction_shares[column], atol=1e-7),
                    "status": "metered_correction",
                }))

    loads = pd.DataFrame(load_rows)
    profiles = pd.DataFrame(profile_data, index=pd.RangeIndex(len(model_snapshots)))
    audit = pd.concat(audit_rows, ignore_index=True)
    annual = audit.groupby(["direction", "measure"])["flow_MW"].sum() / 1_000.0
    for key, expected in expected_gwh.items():
        if not np.isclose(float(annual.loc[key]), expected, atol=1e-6):
            raise AssertionError(
                f"REA {key} annual energy changed: expected {expected}, got {annual.loc[key]}"
            )
    combined = audit.groupby(["snapshot", "direction", "gateway"])["modeled_flow_MW"].sum()
    for (snapshot, direction, gateway), flow in combined.items():
        if flow > capacities[(direction, gateway)] + 1e-6:
            raise AssertionError(
                f"Combined {direction} flow exceeds {gateway} capacity at {snapshot}"
            )
    return loads, profiles, audit


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
        return profiles[["snapshot", *required_columns]].copy()

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


def read_observed_annual_solar_mwh(path: Path = OBSERVED_ANNUAL_GENERATION_FILE) -> float:
    """NITI Dashboard FY2025-26 statewide solar generation in MWh."""
    observed = pd.read_csv(path)
    solar = observed.loc[observed["carrier"].eq("solar")]
    if len(solar) != 1:
        raise ValueError(f"Expected one solar row in {path}")
    row = solar.iloc[0]
    if (row["period_start"], row["period_end"]) != ("2025-04-01", "2026-03-31"):
        raise ValueError("Observed annual solar generation must cover FY2025-26")
    return float(row["generation_gwh"]) * 1_000.0


def apply_solar_seasonal_correction(
    generator_profiles: pd.DataFrame,
    generators: pd.DataFrame,
    snapshots: pd.Series,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply the soiling, wind-cooling and seasonal correction to solar availability.

    See scripts/solar_seasonal_correction.py.  The annual level is set
    afterwards by apply_solar_capacity_trajectory.
    """
    model_snapshots = pd.DatetimeIndex(pd.to_datetime(snapshots, errors="raise"))
    correction = hourly_solar_correction(model_snapshots)
    solar = generators.loc[generators["carrier"].eq("solar"), ["name", "bus"]]
    profiles = generator_profiles.copy()
    raw = generator_profiles[solar["name"]]
    for name, bus in solar.itertuples(index=False):
        profiles[name] = raw[name].to_numpy() * correction[slugify(bus)].to_numpy()

    nameplate = generators.set_index("name").loc[solar["name"], "p_nom"].astype(float)
    month = model_snapshots.to_period("M")
    before = raw.mul(nameplate, axis=1).sum(axis=1).groupby(month).sum()
    after = profiles[solar["name"]].mul(nameplate, axis=1).sum(axis=1).groupby(month).sum()
    audit = pd.DataFrame(
        {
            "month": before.index.astype(str),
            "energy_weighted_correction": (after / before).to_numpy(),
        }
    )
    return profiles, audit


def apply_solar_capacity_trajectory(
    generator_profiles: pd.DataFrame,
    generators: pd.DataFrame,
    snapshots: pd.Series,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Scale solar availability to the capacity installed at each snapshot.

    ``p_nom`` keeps the July 2026 fleet and its area distribution.  Government
    releases record statewide grid-connected solar only, so every solar
    generator's ``p_max_pu`` is multiplied by the same installed share of that
    fleet.  A single availability multiplier then makes annual potential equal
    the observed annual generation, replacing the earlier fixed 0.9304 that was
    calibrated against a constant July 2026 fleet.
    """
    solar = generators.loc[generators["carrier"].eq("solar"), ["name", "p_nom"]]
    nameplate = solar.set_index("name")["p_nom"].astype(float)
    fleet_mw = float(nameplate.sum())
    model_snapshots = pd.DatetimeIndex(pd.to_datetime(snapshots, errors="raise"))
    if len(model_snapshots) != len(generator_profiles):
        raise ValueError("Generator profiles and snapshots differ in length")
    monthly = monthly_solar_capacity()
    capacity = solar_capacity_mw(model_snapshots, monthly)
    if capacity.gt(fleet_mw + 1e-6).any():
        raise ValueError(
            "Reported solar capacity exceeds the modeled fleet, which p_nom would cap"
        )
    installed_share = capacity.to_numpy() / fleet_mw
    raw = generator_profiles[nameplate.index]
    flat_potential_mwh = float(raw.mul(nameplate, axis=1).to_numpy().sum())
    trajectory_potential_mwh = float(
        raw.mul(nameplate, axis=1).mul(installed_share, axis=0).to_numpy().sum()
    )
    observed_mwh = read_observed_annual_solar_mwh()
    multiplier = observed_mwh / trajectory_potential_mwh
    scaled = raw.mul(installed_share * multiplier, axis=0)
    if scaled.to_numpy().max() > 1.0:
        raise ValueError("Calibrated solar availability exceeds one")
    profiles = generator_profiles.copy()
    profiles.loc[:, nameplate.index] = scaled

    trajectory_audit = monthly.assign(
        model_fleet_mw=fleet_mw,
        installed_share_of_fleet=monthly["solar_grid_connected_mw"] / fleet_mw,
    ).reset_index()
    calibration = pd.DataFrame(
        [
            {
                "model_fleet_mw": fleet_mw,
                "fy_mean_grid_connected_mw": float(capacity.mean()),
                "first_snapshot_mw": float(capacity.iloc[0]),
                "last_snapshot_mw": float(capacity.iloc[-1]),
                "unscaled_potential_constant_fleet_gwh": flat_potential_mwh / 1_000.0,
                "unscaled_potential_trajectory_gwh": trajectory_potential_mwh / 1_000.0,
                "observed_annual_generation_gwh": observed_mwh / 1_000.0,
                "observed_source": OBSERVED_ANNUAL_GENERATION_FILE.name,
                "availability_multiplier": multiplier,
                "calibrated_potential_gwh": float(
                    scaled.mul(nameplate, axis=1).to_numpy().sum()
                )
                / 1_000.0,
            }
        ]
    )
    return profiles, trajectory_audit, calibration


def validate_capacity_conservation(
    original_generators: pd.DataFrame,
    original_storage: pd.DataFrame,
    generators: pd.DataFrame,
    storage: pd.DataFrame,
    capacity_overlap: pd.Series | None = None,
) -> None:
    """Modeled capacity per carrier equals the source fleet.

    ``capacity_overlap`` (MW per carrier) is the intended difference at the
    central stations, where Tamil Nadu's units and the export reservation
    together are sized above or below the rating.
    """
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
    if capacity_overlap is not None:
        allocated = allocated.sub(capacity_overlap, fill_value=0.0)
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
    grid_buses, grid_lines, grid_links, grid_metadata = read_grid()
    demand = read_demand_shares()
    areas = demand["balancing_area"].tolist()
    if set(areas) != set(district_to_area.values()):
        raise ValueError("Balancing areas differ between demand and district inputs")
    tn_grid_buses = set(
        grid_buses.loc[grid_buses["kind"].eq("TN balancing area"), "name"]
    )
    if tn_grid_buses != set(areas):
        raise ValueError("Imported Tamil Nadu grid buses differ from the nine balancing areas")
    grid_endpoints = set(grid_lines["bus0"]) | set(grid_lines["bus1"]) | set(grid_links["bus0"]) | set(grid_links["bus1"])
    if grid_endpoints - set(grid_buses["name"]):
        raise ValueError("Imported grid components refer to unknown buses")

    metadata = pd.read_csv(BASE_MODEL / "component_metadata.csv")
    original_generators = pd.read_csv(BASE_MODEL / "generators.csv")
    original_storage = pd.read_csv(BASE_MODEL / "storage_units.csv")
    district_allocations, district_allocation_export = read_district_allocations(
        metadata, district_to_area
    )
    hydro_technical_parameters = read_hydro_technical_parameters()
    source_nameplate = pd.concat(
        [
            original_generators[["name", "p_nom"]],
            original_storage[["name", "p_nom"]],
        ],
        ignore_index=True,
    ).set_index("name")["p_nom"]
    hydro_technical_capacity_adjustment_mw = float(
        sum(
            float(row.p_nom_mw) - float(source_nameplate.at[row.component_id])
            for row in hydro_technical_parameters.itertuples(index=False)
        )
    )
    original_generators, original_storage, metadata = apply_hydro_technical_overrides(
        original_generators,
        original_storage,
        metadata,
        hydro_technical_parameters,
    )
    reviewed_hydro_capacity = hydro_technical_parameters.set_index("component_id")[
        "p_nom_mw"
    ]
    reviewed_allocation = district_allocation_export["component_id"].isin(
        reviewed_hydro_capacity.index
    )
    district_allocation_export.loc[
        reviewed_allocation, "original_capacity_mw"
    ] = district_allocation_export.loc[reviewed_allocation, "component_id"].map(
        reviewed_hydro_capacity
    )
    district_allocation_export.loc[
        reviewed_allocation, "allocated_capacity_mw"
    ] = (
        district_allocation_export.loc[reviewed_allocation, "original_capacity_mw"]
        * district_allocation_export.loc[reviewed_allocation, "allocation_share"]
    )

    source_generators = original_generators.loc[
        ~original_generators["carrier"].isin(MECHANISM_CARRIERS)
    ].copy()
    # The four-station capacity allocation is retained as a historical audit
    # reference only. Verified plant-level REA schedules below now determine
    # export reservations, so the physical fleet is no longer derated to the
    # Tamil Nadu ownership shares in this table.
    tn_export_allocations = pd.read_csv(TN_EXPORT_CAPACITY_FILE)
    tn_export_allocations["application_status"] = (
        "reference_only_superseded_by_verified_rea_plant_schedules"
    )
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
    snapshots_table = pd.read_csv(BASE_MODEL / "snapshots.csv")
    snapshots = snapshots_table["snapshot"]
    (
        generators,
        generator_metadata,
        rea_export_reservation_profiles,
        rea_export_reservation_audit,
    ) = build_rea_plant_export_reservations(
        generators, generator_metadata, snapshots
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
        source_generators, original_storage, generators, storage,
        rea_export_reservation_audit.groupby("carrier")["capacity_overlap_mw"].sum(),
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
    generators = restrict_unit_commitment(generators)
    expanded_metadata = pd.concat(
        [expanded_metadata, additional_metadata], ignore_index=True, sort=False
    )
    district_allocation_export = pd.concat(
        [district_allocation_export, additional_allocation],
        ignore_index=True,
        sort=False,
    )

    loads, load_time_series, demand_allocation = build_loads(demand)
    (
        interstate_flow_loads,
        interstate_flow_profiles,
        interstate_flow_audit,
    ) = build_interstate_flow_loads(
        grid_buses, grid_links, snapshots
    )
    loads = pd.concat([loads, interstate_flow_loads], ignore_index=True)
    load_time_series = pd.concat([load_time_series, interstate_flow_profiles], axis=1)
    oil_gas_budget = read_oil_gas_energy_budget()
    nuclear_budget = read_oil_gas_energy_budget("Nuclear")
    cascade_generation_targets = build_cascade_generation_calibration(
        hydro_electrical_inventory
    )
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
    }
    store_sizes = stores.set_index("name")["e_nom"]
    store_soc_rows: list[pd.DataFrame] = []
    for source_component, target_stores in store_soc_map.items():
        source = hydro_observed_soc.loc[hydro_observed_soc["pypsa_component"].eq(source_component)]
        for target_store in target_stores:
            copied = source.copy()
            copied["pypsa_component"] = target_store
            copied["soc_mwh"] = copied["soc_fraction"] * float(store_sizes[target_store])
            if target_store == "Parambikulam":
                # This Store is the observed reservoir itself. The electrical
                # Sarkarpathy component used it as a proxy, but the hydraulic
                # Store's storage fraction is directly measured here.
                copied["soc_source"] = "observed_agriculture"
                copied["data_quality"] = "high"
            store_soc_rows.append(copied)
    if store_soc_rows:
        hydro_observed_soc = pd.concat([hydro_observed_soc, *store_soc_rows], ignore_index=True)
    # The electrical Kodayar group is represented by Links; its old Pechiparai
    # storage proxy is not an active Store observation or rolling target.
    hydro_observed_soc = hydro_observed_soc.loc[
        ~hydro_observed_soc["pypsa_component"].eq("hydro_kodayar")
    ].copy()
    water_inflow_generators, water_inflow_profiles, kodayar_inflow_audit = build_water_inflow_generators(
        water_buses, snapshots, float(store_sizes["Upper_Kodayar"])
    )
    cascade_inflow_generators, cascade_inflow_profiles, cascade_inflow_audit = build_inferred_cascade_inflows(
        water_buses, snapshots, cascade_generation_targets
    )
    hydraulic_links = set_spill_capacities(
        hydraulic_links,
        pd.concat([water_inflow_generators, cascade_inflow_generators], ignore_index=True),
    )
    generators = pd.concat(
        [generators, water_inflow_generators, cascade_inflow_generators],
        ignore_index=True, sort=False,
    )
    reservoir_names = pd.Index(storage.loc[storage["carrier"].eq("hydro") & storage["p_min_pu"].ge(0), "name"])
    reservoir_inflow = reservoir_inflow.reindex(columns=reservoir_names, fill_value=0.0)
    suruliyar_weekly = cascade_generation_targets.loc[
        cascade_generation_targets.asset_id.eq("suruliyar")
    ].set_index("week_start")["weekly_generation_target_mwh"]
    suruliyar_week_hours = (
        cascade_generation_targets.loc[
            cascade_generation_targets.asset_id.eq("suruliyar")
        ].set_index("week_start")["week_days"] * 24.0
    )
    snapshot_index = pd.DatetimeIndex(pd.to_datetime(snapshots))
    snapshot_week = FY_START + pd.to_timedelta(
        ((snapshot_index.normalize() - FY_START).days // 7) * 7, unit="D"
    )
    suruliyar_efficiency = float(
        storage.loc[storage.name.eq("hydro_suruliyar"), "efficiency_dispatch"].iloc[0]
    )
    # PyPSA's StorageUnit inflow series is absolute MW, not per-unit of p_nom.
    reservoir_inflow["hydro_suruliyar"] = (
        pd.Index(snapshot_week).map(suruliyar_weekly).to_numpy()
        / suruliyar_efficiency
        / pd.Index(snapshot_week).map(suruliyar_week_hours).to_numpy()
    )
    water_mwh_per_mcft = MCFT_M_HEAD_TO_MWH * HYDRO_DEFAULT_HEAD_M * HYDRO_DEFAULT_EFFICIENCY
    suruliyar_audit = pd.DataFrame({
        "week_start": suruliyar_weekly.index,
        "week_end": suruliyar_weekly.index.map(
            cascade_generation_targets.loc[
                cascade_generation_targets.asset_id.eq("suruliyar")
            ].set_index("week_start")["week_end"]
        ),
        "source_component": "suruliyar_calibration_inflow",
        "water_bus": "hydro_suruliyar StorageUnit",
        "inferred_inflow_mwh": suruliyar_weekly.to_numpy() / suruliyar_efficiency,
        "method": "weekly calibration water allowance from generation target and StorageUnit dispatch efficiency",
    })
    suruliyar_audit["inferred_inflow_mcft_at_assumed_head"] = suruliyar_audit["inferred_inflow_mwh"] / water_mwh_per_mcft
    suruliyar_audit["inferred_inflow_mcm_at_assumed_head"] = suruliyar_audit["inferred_inflow_mcft_at_assumed_head"] / MCM_TO_MCFT
    suruliyar_audit["volume_conversion_basis"] = f"{HYDRO_DEFAULT_HEAD_M:g} m head; {HYDRO_DEFAULT_EFFICIENCY:.0%} efficiency"
    cascade_inflow_audit = pd.concat([cascade_inflow_audit, suruliyar_audit], ignore_index=True)
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
    # Observed energy targets are not written as PyPSA GlobalConstraints: one
    # fixed constant cannot follow the solved period. The run scripts apply
    # them over the exact days solved (scripts/period_energy_targets.py).
    # Tamil Nadu Outlook reports monthly bio-power and small-hydro generation,
    # spread uniformly over the hours of each month.
    biomass_monthly_targets_mwh = read_bio_power_monthly_targets()
    biomass_annual_target_mwh = float(biomass_monthly_targets_mwh.sum())
    small_hydro_monthly_targets_mwh = read_small_hydro_monthly_targets()
    small_hydro_annual_target_mwh = float(small_hydro_monthly_targets_mwh.sum())
    small_hydro_capacity_mw = float(
        generators.loc[generators["carrier"].eq("small_hydro"), "p_nom"].sum()
    )
    if small_hydro_capacity_mw <= 0:
        raise ValueError("Cannot apply a small-hydro target without small_hydro generators")
    area_profiles = placeholder_profiles(
        areas, snapshots, generators, expanded_metadata
    )
    generator_profiles = build_generator_profiles(
        generators, expanded_metadata, area_profiles
    )
    generator_profiles, solar_seasonal_correction = apply_solar_seasonal_correction(
        generator_profiles, generators, snapshots
    )
    (
        generator_profiles,
        solar_capacity_trajectory,
        solar_availability_calibration,
    ) = apply_solar_capacity_trajectory(generator_profiles, generators, snapshots)
    for name in rea_export_reservation_profiles.columns:
        generator_profiles[name] = rea_export_reservation_profiles[name].to_numpy()
    # Monthly observed energy equalities supersede the inherited placeholder
    # profile for small hydro. Make its timing dispatchable up to nameplate.
    small_hydro_names = generators.loc[
        generators["carrier"].eq("small_hydro"), "name"
    ]
    generator_profiles.loc[:, small_hydro_names] = 1.0
    ror_profiles = build_run_of_river_profiles(generators, snapshots)
    for name in ror_profiles.columns:
        generator_profiles[name] = ror_profiles[name]
    for name in water_inflow_profiles.columns:
        generator_profiles[name] = water_inflow_profiles[name]
    for name in cascade_inflow_profiles.columns:
        generator_profiles[name] = cascade_inflow_profiles[name]

    # Keep the preference on the final fleet so it applies consistently to
    # both the source fleet and the July-2026 capacity overlay.
    generators.loc[
        generators["carrier"].eq("solar"), "marginal_cost"
    ] = SOLAR_CURTAILMENT_PREFERENCE_COST
    generators, coal_operating_cost_audit = apply_coal_operating_costs(generators)
    generators, plant_operating_cost_audit = apply_plant_operating_costs(generators)
    generators = set_coal_initially_online(generators)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    coal_operating_cost_audit.to_csv(OUTPUT_DIR / "coal_operating_cost_audit.csv", index=False)
    plant_operating_cost_audit.to_csv(OUTPUT_DIR / "plant_operating_cost_audit.csv", index=False)
    # Earlier builds wrote annual energy GlobalConstraints; a stale file would
    # still be imported by PyPSA and make part-year solves infeasible.
    (OUTPUT_DIR / "global_constraints.csv").unlink(missing_ok=True)
    nuclear_budget.to_csv(OUTPUT_DIR / "nuclear_daily_energy_budget.csv", index=False, date_format="%Y-%m-%d")
    tn_export_allocations.to_csv(OUTPUT_DIR / "tn_export_capacity_allocations.csv", index=False)
    rea_export_reservation_audit.to_csv(
        OUTPUT_DIR / "rea_plant_export_reservation_audit.csv", index=False
    )
    interstate_flow_audit.to_csv(OUTPUT_DIR / "interstate_flow_audit.csv", index=False)
    interstate_flow_reconciliation = interstate_flow_audit.groupby(
        ["direction", "measure", "gateway", "status"], as_index=False
    ).agg(
        energy_GWh=("flow_MW", lambda values: values.sum() / 1_000.0),
        modeled_energy_GWh=("modeled_flow_MW", lambda values: values.sum() / 1_000.0),
        peak_MW=("flow_MW", "max"),
        modeled_peak_MW=("modeled_flow_MW", "max"),
        nonzero_hours=("flow_MW", lambda values: int((values > 1e-9).sum())),
        modeled_nonzero_hours=(
            "modeled_flow_MW", lambda values: int((values > 1e-9).sum())
        ),
        capacity_limited_hours=("capacity_limited", "sum"),
        net_capacity_redistribution_GWh=(
            "capacity_redistribution_MW", lambda values: values.sum() / 1_000.0
        ),
    )
    interstate_flow_reconciliation.to_csv(
        OUTPUT_DIR / "interstate_flow_reconciliation.csv", index=False
    )
    reservoir_inflow_audit.to_csv(
        OUTPUT_DIR / "hydro_reservoir_inflow.csv", index=False, date_format="%Y-%m-%d"
    )
    hydro_observed_soc.to_csv(OUTPUT_DIR / "hydro_observed_soc.csv", index=False)
    kodayar_inflow_audit.to_csv(OUTPUT_DIR / "upper_kodayar_inflow_estimate.csv", index=False)
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
    shutil.copy2(
        HYDRO_TECHNICAL_PARAMETERS,
        OUTPUT_DIR / "hydro_technical_parameters.csv",
    )
    hydro_asset_audit.to_csv(OUTPUT_DIR / "hydro_asset_audit.csv", index=False)
    network = pd.read_csv(BASE_MODEL / "network.csv")
    network.loc[0, "name"] = (
        "Tamil Nadu FY 2025-26 nine-balancing-area hourly UC inputs "
        "with July 2026 capacity overlay and seasonal reservoir hydro"
    )
    network.loc[0, "pypsa_version"] = pypsa.__version__
    network.to_csv(OUTPUT_DIR / "network.csv", index=False)
    pd.concat([
        grid_buses,
        water_buses,
    ], ignore_index=True, sort=False).to_csv(
        OUTPUT_DIR / "buses.csv", index=False
    )
    link_columns = list(dict.fromkeys([*grid_links.columns, *hydraulic_links.columns]))
    pd.concat([grid_links.reindex(columns=link_columns), hydraulic_links.reindex(columns=link_columns)], ignore_index=True).to_csv(
        OUTPUT_DIR / "links.csv", index=False
    )
    # The external-bus loads fix total gateway exchange while allowing the
    # network to choose among parallel interconnector links.
    (OUTPUT_DIR / "links-p_min_pu.csv").unlink(missing_ok=True)
    (OUTPUT_DIR / "links-p_max_pu.csv").unlink(missing_ok=True)
    (OUTPUT_DIR / "interstate_signed_flows.csv").unlink(missing_ok=True)
    grid_lines.to_csv(OUTPUT_DIR / "lines.csv", index=False)
    grid_metadata.to_csv(
        OUTPUT_DIR / "transmission_capacity_metadata.csv", index=False
    )
    loads.to_csv(OUTPUT_DIR / "loads.csv", index=False)
    load_time_series.to_csv(OUTPUT_DIR / "loads-p_set.csv")
    generators.to_csv(OUTPUT_DIR / "generators.csv", index=False)
    generator_profiles.to_csv(OUTPUT_DIR / "generators-p_max_pu.csv")
    solar_capacity_trajectory.to_csv(
        OUTPUT_DIR / "solar_capacity_trajectory.csv", index=False, date_format="%Y-%m-%d"
    )
    solar_availability_calibration.to_csv(
        OUTPUT_DIR / "solar_availability_calibration.csv", index=False
    )
    solar_seasonal_correction.to_csv(
        OUTPUT_DIR / "solar_seasonal_correction.csv", index=False
    )
    pd.concat([
        water_inflow_profiles[["upper_kodayar_estimated_inflow"]],
        cascade_inflow_profiles,
        rea_export_reservation_profiles,
    ], axis=1).to_csv(OUTPUT_DIR / "generators-p_min_pu.csv")
    storage.to_csv(OUTPUT_DIR / "storage_units.csv", index=False)
    stores.to_csv(OUTPUT_DIR / "stores.csv", index=False)
    reservoir_inflow.to_csv(OUTPUT_DIR / "storage_units-inflow.csv")
    cascade_generation_targets.to_csv(
        OUTPUT_DIR / "hydro_cascade_generation_targets.csv", index=False, date_format="%Y-%m-%d"
    )
    cascade_inflow_audit.to_csv(
        OUTPUT_DIR / "hydro_cascade_inferred_inflow.csv", index=False, date_format="%Y-%m-%d"
    )
    snapshots_table.to_csv(OUTPUT_DIR / "snapshots.csv", index=False)
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
                "applied_in_run_scripts": OIL_GAS_TARGET_APPLICATION,
                "constraint_sense": "==",
            }
        ]
    ).to_csv(OUTPUT_DIR / "oil_gas_energy_budget_summary.csv", index=False)
    pd.DataFrame(
        [
            {
                "source": "Tamil Nadu Outlook monthly generation table",
                "fiscal_year": "FY2025-26",
                "installed_capacity_mw": biomass_capacity_mw,
                "annual_energy_target_mwh": biomass_annual_target_mwh,
                "applied_in_run_scripts": MONTHLY_TARGET_APPLICATION,
                "constraint_sense": "==",
            }
        ]
    ).to_csv(OUTPUT_DIR / "biomass_energy_target_summary.csv", index=False)
    pd.DataFrame(
        [
            {
                "source": "Tamil Nadu Outlook monthly generation table",
                "fiscal_year": "FY2025-26",
                "installed_capacity_mw": small_hydro_capacity_mw,
                "annual_energy_target_mwh": small_hydro_annual_target_mwh,
                "applied_in_run_scripts": MONTHLY_TARGET_APPLICATION,
                "constraint_sense": "==",
            }
        ]
    ).to_csv(OUTPUT_DIR / "small_hydro_energy_target_summary.csv", index=False)
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
            # Physical nameplate: remove the central stations' intended
            # overlap of Tamil Nadu units and export reservations.
            rea_export_reservation_audit.assign(p_nom=lambda f: -f["capacity_overlap_mw"])[["bus", "carrier", "p_nom"]],
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
    excluded_off_grid_capacity_mw = EXPECTED_EXCLUDED_OFF_GRID_CAPACITY_MW
    # REA-reserved blocks remain explicitly modeled at their physical plants;
    # none of their nameplate is omitted from installed-capacity reconciliation.
    withheld_export_capacity_mw = 0.0
    corrected_july_2026_total_mw = (
        EXPECTED_JULY_2026_TOTAL_CAPACITY_MW
        + hydro_technical_capacity_adjustment_mw
    )
    if not np.isclose(
        modeled_total_mw + unassigned_total_mw + excluded_off_grid_capacity_mw + withheld_export_capacity_mw,
        corrected_july_2026_total_mw,
    ):
        raise AssertionError(
            "Modeled capacity plus excluded off-grid and unassigned capacities "
            "does not reconcile to the July 2026 comparison total"
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
                "item": "off_grid_solar_and_bio_power",
                "capacity_mw": excluded_off_grid_capacity_mw,
                "treatment": "excluded off-grid capacity",
            },
            {
                "item": "in_state_capacity_omitted_for_external_allocations",
                "capacity_mw": withheld_export_capacity_mw,
                "treatment": "none; verified REA export blocks remain modeled",
            },
            {
                "item": "source_july_2026_comparison_total",
                "capacity_mw": EXPECTED_JULY_2026_TOTAL_CAPACITY_MW,
                "treatment": "source total before reviewed hydro corrections",
            },
            {
                "item": "reviewed_hydro_technical_correction",
                "capacity_mw": hydro_technical_capacity_adjustment_mw,
                "treatment": "Moyar corrected from 40 MW to official 36 MW",
            },
            {
                "item": "july_2026_comparison_total",
                "capacity_mw": corrected_july_2026_total_mw,
                "treatment": "reconciled total after reviewed hydro corrections",
            },
        ]
    ).to_csv(OUTPUT_DIR / "capacity_reconciliation_2026_07.csv", index=False)

    carriers = pd.read_csv(BASE_MODEL / "carriers.csv")
    missing_carriers = sorted(
        (
            set(generators["carrier"])
            | set(stores["carrier"])
            | set(hydraulic_links["carrier"])
            | set(grid_lines["carrier"])
            | set(grid_links["carrier"])
        )
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
    shutil.copy2(BASE_MODEL / "crs.json", OUTPUT_DIR / "crs.json")

    committable = generators.loc[generators["committable"].astype(bool)]
    print(
        f"Wrote {len(grid_buses)} electrical grid buses, {len(grid_lines)} internal "
        f"lines, and {len(grid_links)} directional interconnector links; hydraulic "
        "water buses are included separately"
    )
    print(f"Wrote {len(loads)} hourly loads over {len(snapshots)} snapshots")
    print(f"Wrote {len(generators)} generators, including {len(committable)} committable records")
    print(f"Wrote {len(storage)} storage units")
    print(
        "Observed energy targets (applied per solved period by the run scripts): "
        f"oil/gas {oil_gas_annual_target_mwh / 1_000.0:,.2f} MU, "
        f"bio-power {biomass_annual_target_mwh / 1_000.0:,.2f} MU, "
        f"small hydro {small_hydro_annual_target_mwh / 1_000.0:,.2f} MU, "
        f"nuclear {nuclear_budget.daily_energy_target_mwh.sum() / 1_000.0:,.2f} MU over FY2025-26"
    )
    print(
        "Applied July 2026 capacity overlay: "
        f"{additional_generators['p_nom'].sum():.4f} MW modeled and "
        f"{additional_capacity.loc[~additional_capacity['include_in_model'], 'capacity_mw'].sum():.2f} MW unassigned"
    )
    calibration = solar_availability_calibration.iloc[0]
    print(
        "Solar capacity through FY2025-26: "
        f"{calibration['first_snapshot_mw']:,.0f} to {calibration['last_snapshot_mw']:,.0f} MW "
        f"(mean {calibration['fy_mean_grid_connected_mw']:,.0f} MW of a "
        f"{calibration['model_fleet_mw']:,.0f} MW fleet); availability multiplier "
        f"{calibration['availability_multiplier']:.4f}"
    )
    print(f"Model folder: {OUTPUT_DIR}")


if __name__ == "__main__":
    import argparse

    _parser = argparse.ArgumentParser(description="Build the nine-balancing-area model in model/.")
    _parser.add_argument(
        "--interchange", choices=("metered", "transactions"), default=INTERCHANGE_BASIS,
        help="Interstate exchange basis (default %(default)s)",
    )
    INTERCHANGE_BASIS = _parser.parse_args().interchange
    main()
