"""Shared labels, colors, units, and schema metadata for result analysis."""

from __future__ import annotations

ANALYSIS_SCHEMA_VERSION = "1.0"

# Currency of the model's marginal, start-up and other cost inputs.
CURRENCY = "INR"

# Carriers whose costs are model penalties rather than operating costs: load
# shedding, the slack that reconciles reservoirs with measured storage, and
# the small spill cost that breaks ties between spilling and storing.
PENALTY_CARRIERS = frozenset(
    {"unserved_energy", "water_balance_adjustment", "water_spill"}
)

CARRIER_ORDER = (
    "coal",
    "oil_gas",
    "diesel",
    "nuclear",
    "bio_power",
    "small_hydro",
    "other_res",
    "hydro",
    "solar",
    "wind",
    "offshore_wind",
    "battery",
    "imports",
    "unserved_energy",
)

CARRIER_LABELS = {
    "coal": "Coal",
    "oil_gas": "Oil and gas",
    "diesel": "Diesel",
    "nuclear": "Nuclear",
    "bio_power": "Bio-power",
    "small_hydro": "Small hydro",
    "other_res": "Other RES (small hydro + bio-power)",
    "hydro": "Hydro incl. pumped-storage discharge",
    "solar": "Solar",
    "wind": "Wind",
    "offshore_wind": "Offshore wind",
    "battery": "Battery discharge",
    "imports": "Net imports",
    "unserved_energy": "Unserved energy",
    "demand": "Demand incl. neighbouring-region loads",
    "storage_charging": "Storage charging and pumping",
    "transmission_losses": "Transmission losses",
    "transmission": "Interstate transmission charges",
    "water_balance_adjustment": "Measured-storage reconciliation (penalty)",
    "water_spill": "Spill tie-break (penalty)",
}

CARRIER_COLORS = {
    "coal": "#4D4D4D",
    "oil_gas": "#D95F02",
    "diesel": "#8C510A",
    "nuclear": "#7570B3",
    "bio_power": "#66A61E",
    "hydro": "#1F78B4",
    "solar": "#E6AB02",
    "wind": "#1B9E77",
    "offshore_wind": "#17BECF",
    "battery": "#E377C2",
    "imports": "#A6761D",
    "unserved_energy": "#D62728",
    "small_hydro": "#A6CEE3",
    "demand": "#9E9E9E",
    "storage_charging": "#6A3D9A",
    "transmission_losses": "#E7298A",
}

# Names and colours for model carriers outside the reporting taxonomy, so that
# PyPSA's own plots (``n.statistics.<metric>.plot``) read clearly.
MODEL_CARRIER_STYLES = {
    "AC": ("AC transmission", "#BDBDBD"),
    "DC": ("HVDC transmission", "#737373"),
    "electricity": ("Demand", "#9E9E9E"),
    "hydro_pump": ("Pumping", "#6A3D9A"),
    "water_energy": ("Reservoir water", "#9ECAE1"),
    "water_inflow": ("Reservoir inflow", "#C6DBEF"),
    "water_spill": ("Spill", "#DEEBF7"),
    "water_transfer": ("Water transfer", "#6BAED6"),
    "water_balance_adjustment": ("Measured-storage reconciliation", "#FB6A4A"),
}

RENEWABLE_CARRIERS = ("solar", "wind", "offshore_wind")
DISPATCHABLE_CARRIERS = (
    "coal",
    "oil_gas",
    "diesel",
    "nuclear",
    "bio_power",
    "hydro",
)


def canonical_carrier(value: str) -> str:
    """Map component-specific carrier labels to the reporting taxonomy."""
    carrier = str(value)
    if carrier in {
        "hydro_turbine",
        "hydro_run_of_river",
        "pumped_hydro",
        "reservoir_hydro",
    }:
        return "hydro"
    if carrier == "market_import":
        return "imports"
    return carrier
