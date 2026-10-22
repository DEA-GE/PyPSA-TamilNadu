"""File-family classification and overlap definitions."""

from __future__ import annotations

import re

from .mappings import BENEFICIARY_FILES


def classify_file(filename: str) -> tuple[str, str, str]:
    """Return (family, primary role, energy meaning)."""
    name = filename.lower()
    if name.endswith(".zip"):
        return "ARCHIVE_CONTAINER", "EXCLUDE", "Container; members are inventoried separately"
    if name == "rea.csv":
        return "GNA_TGNA_APPLICATION_AGGREGATE", "VALIDATION_ONLY", "Monthly/application aggregate; parent of state views"
    if re.fullmatch(r"(?:tneb|aptransco|kptcl|kseb|pondy|ts)_?(?:imp|exp)\.csv", name):
        return "GNA_TGNA_STATE_VIEW", "TRANSACTION_DETAIL", "15-minute application-level scheduled transfers"
    if name in {"tneb_imp.csv", "tneb_exp.csv"}:
        return "GNA_TGNA_STATE_VIEW", "TRANSACTION_DETAIL", "15-minute application-level TN transfer view"
    match = re.fullmatch(r"commercial_sch_([a-z0-9_]+)\.csv", name)
    if match and match.group(1) in BENEFICIARY_FILES:
        return "STATE_COMPOSITE_SCHEDULE", "ALLOCATION_DETAIL", "Station columns allocate ISGS/LTA; composite columns are non-additive parents"
    if name in {"commercial_isgs.csv", "commercial_sch_isgs.csv"}:
        return "ISGS_STATION_WIDE", "VALIDATION_ONLY", "Station-wide schedule; control total, not buyer allocation"
    if re.fullmatch(r"commercial_px_(?:tamilnadu|andhrapradesh|karnataka|kerala|telangana|puducherry|pondichery|bihar|goa)\.csv", name):
        return "PX_STATE_DETAIL", "TRANSACTION_DETAIL", "State market direction/product breakdown of composite schedule"
    if name.startswith("commercial_pxsold_"):
        return "PX_GENERATOR_SALE_ATTRIBUTION", "ATTRIBUTION_ONLY", "Generator market-sale evidence; overlaps pooled state market schedules"
    if name == "commercial_remc_schedule.csv":
        return "REMC_TRANSACTION_DETAIL", "TRANSACTION_DETAIL", "Application/unit-level renewable schedules"
    if name == "commercial_rnw_schedule.csv":
        return "RNW_TRANSACTION_DETAIL", "TRANSACTION_DETAIL", "Renewable schedules; deduplicate against REMC/application views"
    if "ent" in name and name.endswith(".csv"):
        return "ENTITLEMENT", "VALIDATION_ONLY", "Entitlement/availability, not scheduled energy"
    if "urs" in name:
        return "URS", "VALIDATION_ONLY", "Unrequisitioned surplus control/supporting data"
    if "actual" in name or "meter" in name:
        return "ACTUAL_METERING", "VALIDATION_ONLY", "Physical/actual metering, outside commercial schedule boundary"
    if "dev" in name or "dsm" in name:
        return "DEVIATION_DSM", "SETTLEMENT_ONLY", "Deviation/DSM settlement, not scheduled trade"
    if any(token in name for token in ("sced", "scuc", "tras", "mbas", "ancillary")):
        return "ANCILLARY_REDISPATCH", "VALIDATION_ONLY", "Potential schedule correction or settlement; non-additive unless independently proved"
    if "ppa" in name or name.endswith(".pdf") or name.endswith(".xlsx"):
        return "CONTRACT_METADATA", "ATTRIBUTION_ONLY", "Contract/PPA/supporting metadata; not independent energy"
    if "price" in name or "rate" in name or "charge" in name or "settlement" in name:
        return "PRICE_SETTLEMENT", "SETTLEMENT_ONLY", "Price or monetary settlement"
    if "55_" in name or "55 data" in name or "55_data" in name:
        return "BENEFICIARY_SHARE_METADATA", "ATTRIBUTION_ONLY", "Beneficiary share/reference metadata; not monthly schedule energy"
    if name.endswith(".csv"):
        return "OTHER_CSV", "EXCLUDE", "Not established as additive scheduled commercial energy"
    return "OTHER_FILE", "EXCLUDE", "Not a primary scheduled-energy source"


OVERLAP_ROWS = [
    {
        "parent_family": "STATE_COMPOSITE_SCHEDULE",
        "child_family": "STATE_COMPOSITE_SCHEDULE.station_columns",
        "relationship": "station columns sum to lta",
        "additive_together": False,
        "accepted_representation": "station columns",
        "evidence_test": "block-level station sum minus lta",
    },
    {
        "parent_family": "STATE_COMPOSITE_SCHEDULE.market_columns",
        "child_family": "PX_STATE_DETAIL",
        "relationship": "same state-level DAM/RTM/GDAM/HPDAM positions",
        "additive_together": False,
        "accepted_representation": "PX_STATE_DETAIL",
        "evidence_test": "block-level product/direction identity",
    },
    {
        "parent_family": "STATE_COMPOSITE_SCHEDULE.GNA_TGNA",
        "child_family": "GNA_TGNA_STATE_VIEW",
        "relationship": "net composite vs gross application schedules at different boundaries",
        "additive_together": False,
        "accepted_representation": "GNA_TGNA_STATE_VIEW",
        "evidence_test": "monthly gross/net/loss residual",
    },
    {
        "parent_family": "GNA_TGNA_APPLICATION_AGGREGATE",
        "child_family": "GNA_TGNA_STATE_VIEW",
        "relationship": "application aggregate vs 15-minute state perspective",
        "additive_together": False,
        "accepted_representation": "GNA_TGNA_STATE_VIEW",
        "evidence_test": "application-id crosswalk and monthly energy",
    },
    {
        "parent_family": "ISGS_STATION_WIDE",
        "child_family": "STATE_COMPOSITE_SCHEDULE.station_columns",
        "relationship": "station total vs beneficiary allocations",
        "additive_together": False,
        "accepted_representation": "beneficiary station columns",
        "evidence_test": "station/day/month allocation residual",
    },
    {
        "parent_family": "PX_STATE_DETAIL",
        "child_family": "PX_GENERATOR_SALE_ATTRIBUTION",
        "relationship": "pooled state market positions vs generator sale evidence",
        "additive_together": False,
        "accepted_representation": "PX_STATE_DETAIL; generator rows attribution-only",
        "evidence_test": "reported as unresolved buyer/seller matching",
    },
    {
        "parent_family": "STATE_COMPOSITE_SCHEDULE.REMC",
        "child_family": "REMC_TRANSACTION_DETAIL/RNW_TRANSACTION_DETAIL",
        "relationship": "renewable net parent vs transaction details",
        "additive_together": False,
        "accepted_representation": "deduplicated transaction detail",
        "evidence_test": "application/block dedup and monthly net residual",
    },
]

