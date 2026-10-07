"""Shared labels, colors, units, and schema metadata for result analysis."""

from __future__ import annotations

ANALYSIS_SCHEMA_VERSION = "1.0"

CARRIER_ORDER = (
    "coal",
    "oil_gas",
    "diesel",
    "nuclear",
    "bio_power",
    "hydro",
    "solar",
    "wind",
    "imports",
    "unserved_energy",
)

CARRIER_LABELS = {
    "coal": "Coal",
    "oil_gas": "Oil and gas",
    "diesel": "Diesel",
    "nuclear": "Nuclear",
    "bio_power": "Bio-power",
    "hydro": "Hydro incl. pumped-storage discharge",
    "solar": "Solar",
    "wind": "Wind",
    "imports": "Net imports",
    "unserved_energy": "Unserved energy",
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
    "imports": "#A6761D",
    "unserved_energy": "#D62728",
}

RENEWABLE_CARRIERS = ("solar", "wind")
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
    if carrier in {"hydro_turbine", "pumped_hydro", "reservoir_hydro"}:
        return "hydro"
    return carrier
