"""Compare the CEA transmission-line workbook with the nine-area model grid.

This applies the topology part of PyPSA's busmap/linemap methodology:

1. identify districts explicitly named in each source record;
2. map those districts to the model balancing areas;
3. remove intra-area records and group inter-area records by unordered area pair;
4. compare those corridors with the capacities currently used by the model.

The source workbook has no line rating and no structured endpoint/district fields.
Consequently this script deliberately does *not* estimate MW/MVA capacities from
line length or circuit labels. Such an estimate would need, at minimum, voltage,
conductor/thermal rating and a structured substation-to-district gazetteer.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "additional_data" / "Grid" / "Transmission_Lines_1788874302675.xlsx"
AREA_FILE = ROOT / "9_BA" / "Balancing_areas.txt"
CURRENT_GRID = ROOT / "9_BA" / "model" / "transmission_capacity_metadata.csv"
OUTPUT_DIR = ROOT / "outputs" / "transmission_source_comparison"

DISTRICT_ALIASES = {
    "Tiruvallur": ["Thiruvallur"],
    "Kancheepuram": ["Kanchipuram"],
    "Viluppuram": ["Villupuram"],
    "The Nilgiris": ["Nilgiris"],
    "Kanniyakumari": ["Kanyakumari"],
    "Thoothukudi": ["Thoothukkudi", "Tuticorin"],
    "Tiruchirappalli": ["Trichy"],
    "Sivagangai": ["Sivaganga"],
}


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def read_area_mapping() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in AREA_FILE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "---" in line or "District" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        mapping[cells[0]] = cells[1]
    if len(mapping) != 38:
        raise ValueError(f"Expected 38 district mappings, found {len(mapping)}")
    return mapping


def named_districts(name: object, districts: dict[str, str]) -> list[str]:
    padded_name = f" {normalize(name)} "
    matches: list[str] = []
    for district in districts:
        candidates = [district, *DISTRICT_ALIASES.get(district, [])]
        if any(f" {normalize(candidate)} " in padded_name for candidate in candidates):
            matches.append(district)
    return matches


def corridor_key(area0: str, area1: str) -> str:
    return " | ".join(sorted((area0, area1)))


def endpoint_match(name: object, districts: dict[str, str]) -> tuple[str, str, str, str]:
    """Return unambiguous district/area matches on opposite sides of a dash."""
    parts = re.split(r"\s+[\-\N{EN DASH}\N{EM DASH}]\s+", str(name), maxsplit=1)
    if len(parts) != 2:
        return "", "", "", ""
    side0 = named_districts(parts[0], districts)
    side1 = named_districts(parts[1], districts)
    areas0 = sorted({districts[d] for d in side0})
    areas1 = sorted({districts[d] for d in side1})
    if len(areas0) != 1 or len(areas1) != 1 or areas0[0] == areas1[0]:
        return "; ".join(side0), "; ".join(side1), "", ""
    return "; ".join(side0), "; ".join(side1), areas0[0], areas1[0]


def main() -> None:
    district_to_area = read_area_mapping()
    source = pd.read_excel(SOURCE, sheet_name="Transmission_Lines")
    current = pd.read_csv(CURRENT_GRID)

    source["matched_districts"] = source["Name"].map(
        lambda value: "; ".join(named_districts(value, district_to_area))
    )
    source["matched_areas"] = source["matched_districts"].map(
        lambda value: "; ".join(
            sorted({district_to_area[d] for d in value.split("; ") if d})
        )
    )
    source["matched_district_count"] = source["matched_districts"].map(
        lambda value: 0 if not value else len(value.split("; "))
    )
    source["matched_area_count"] = source["matched_areas"].map(
        lambda value: 0 if not value else len(value.split("; "))
    )
    endpoint_matches = source["Name"].map(
        lambda value: endpoint_match(value, district_to_area)
    )
    source[["endpoint0_district", "endpoint1_district", "area0", "area1"]] = pd.DataFrame(
        endpoint_matches.tolist(), index=source.index
    )
    source["is_tantransco"] = source["Executive Agency"].astype(str).str.contains(
        "TANTRANS", case=False, na=False
    )
    source["capacity_available"] = False
    source["capacity_mw"] = pd.NA
    source["capacity_note"] = (
        "Not derivable: source has no MW/MVA rating, structured voltage, or conductor rating"
    )

    inter = source.loc[source["area0"].ne("") & source["area1"].ne("")].copy()
    inter["corridor"] = inter.apply(
        lambda row: corridor_key(row["area0"], row["area1"]), axis=1
    )

    current["corridor"] = current.apply(
        lambda row: corridor_key(str(row["bus0"]), str(row["bus1"])), axis=1
    )
    current_cmp = current[
        ["corridor", "bus0", "bus1", "p_nom", "source_capacity_text", "capacity_method"]
    ].rename(columns={"p_nom": "current_model_capacity_mw"})

    evidence = (
        inter.groupby("corridor", as_index=False)
        .agg(
            workbook_records=("Name", "size"),
            workbook_total_length_ckm=("Line Length (cKM)", "sum"),
            workbook_line_names=("Name", lambda s: " || ".join(map(str, s))),
        )
    )
    comparison = current_cmp.merge(evidence, on="corridor", how="left")
    comparison["workbook_records"] = comparison["workbook_records"].fillna(0).astype(int)
    comparison["workbook_total_length_ckm"] = comparison[
        "workbook_total_length_ckm"
    ].fillna(0.0)
    comparison["source_capacity_mw"] = pd.NA
    comparison["capacity_difference_mw"] = pd.NA
    comparison["comparison_status"] = comparison["workbook_records"].map(
        lambda count: (
            "Topology evidence found; capacity not comparable"
            if count
            else "No district-explicit source record; capacity not comparable"
        )
    )

    summary = pd.DataFrame(
        [
            ("source_workbook_rows", len(source)),
            ("tantransco_rows", int(source["is_tantransco"].sum())),
            ("rows_with_one_matched_area", int(source["matched_area_count"].eq(1).sum())),
            ("rows_with_two_area_names_anywhere", int(source["matched_area_count"].eq(2).sum())),
            ("rows_with_unambiguous_interarea_endpoints", len(inter)),
            ("source_rows_with_capacity", 0),
            ("current_model_corridors", len(current)),
            ("current_model_total_capacity_mw", float(current["p_nom"].sum())),
            ("current_corridors_with_literal_source_evidence", int(comparison["workbook_records"].gt(0).sum())),
        ],
        columns=["metric", "value"],
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    source.to_csv(OUTPUT_DIR / "source_line_mapping_audit.csv", index=False)
    inter.to_csv(OUTPUT_DIR / "district_explicit_interarea_lines.csv", index=False)
    comparison.to_csv(OUTPUT_DIR / "corridor_comparison.csv", index=False)
    summary.to_csv(OUTPUT_DIR / "comparison_summary.csv", index=False)

    report = f"""# Transmission source comparison

## Result

The topology stage of the PyPSA clustering methodology was applied using the
model's 38-district to nine-balancing-area map. Capacity aggregation cannot be
completed from this workbook because it has no MW/MVA rating and no consistent
structured voltage or conductor rating.

## Coverage

- Source workbook records: {len(source):,}
- TANTRANSCO records: {int(source['is_tantransco'].sum()):,}
- Records naming exactly one model area: {int(source['matched_area_count'].eq(1).sum()):,}
- Records naming exactly two model areas anywhere in the name: {int(source['matched_area_count'].eq(2).sum()):,}
- Records with unambiguous district matches on opposite endpoints: {len(inter):,}
- Current model corridors: {len(current):,}
- Current corridors with conservative district-explicit evidence: {int(comparison['workbook_records'].gt(0).sum()):,}
- Current model total corridor capacity: {current['p_nom'].sum():,.0f} MW

## Interpretation

The current 22 corridor ratings are independent assumptions read from
`9_BA/Grid_capacity.txt`; they are not recoverable or validated by the supplied
workbook. Line length is not capacity. To finish the PyPSA-style aggregation,
add a structured table with source substation, destination substation, endpoint
districts (or coordinates), voltage, number of circuits/conductors, and a thermal
rating in MVA/MW or enough conductor data to calculate one.
"""
    (OUTPUT_DIR / "README.md").write_text(report, encoding="utf-8")

    print(summary.to_string(index=False))
    print(f"\nWrote comparison outputs to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
