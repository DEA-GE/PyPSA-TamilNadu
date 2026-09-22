from __future__ import annotations

import math
import re
from collections import defaultdict
from copy import copy
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from openpyxl import load_workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.properties import CalcProperties


ROOT = Path(__file__).resolve().parents[2]
BASE_PATH = ROOT / "additional_data" / "TamilNadu_plantCapacity_all_source_Sep26.xlsx.xlsx"
ENHANCED_PATH = (
    ROOT
    / "additional_data"
    / "TamilNadu_ICED_all_source_1782910007272_prayas_OSM_validated.xlsx"
)
OUTPUT_PATH = Path(__file__).resolve().parent / "TamilNadu_plantCapacity_all_source_Sep26_district_enhanced.xlsx"

BOUNDARY_URL = (
    "https://cdn.jsdelivr.net/gh/udit-001/india-maps-data@2884453/"
    "geojson/states/tamil-nadu.geojson"
)

TITLE_FILL = PatternFill("solid", fgColor="153B5B")
HEADER_FILL = PatternFill("solid", fgColor="0F766E")
SECTION_FILL = PatternFill("solid", fgColor="D1FAE5")
NOTE_FILL = PatternFill("solid", fgColor="ECFDF5")
WARN_FILL = PatternFill("solid", fgColor="FEF3C7")
FAIL_FILL = PatternFill("solid", fgColor="FEE2E2")
WHITE_FONT = Font(color="FFFFFF", bold=True)
HEADER_FONT = Font(color="FFFFFF", bold=True)
TITLE_FONT = Font(color="FFFFFF", bold=True, size=14)
THIN_GRAY = Side(style="thin", color="D1D5DB")
BOTTOM_BORDER = Border(bottom=THIN_GRAY)


def clean_scalar(value):
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value


def canonical_district(value):
    if value is None or pd.isna(value):
        return "N.A."
    text = str(value).strip()
    mapping = {
        "Kancheepuram": "Kanchipuram",
        "Kanniyakumari": "Kanyakumari",
        "Thoothukkudi": "Thoothukudi",
        "Tuticorin": "Thoothukudi",
        "Thiruvallur": "Tiruvallur",
        "Chengalpattu": "Kanchipuram",
        "Kallakurichi": "Viluppuram",
        "Mayiladuthurai": "Nagapattinam",
        "Ranipet": "Vellore",
        "Tirupathur": "Vellore",
    }
    return mapping.get(text, text)


def allocation_category(row):
    source = str(row["Source"])
    name = str(row["Name of Power Plant"])
    district = str(row["District"])
    low = name.lower()
    if source == "Solar":
        if "rooftop" in low:
            return "solar_rooftop"
        if "off -grid" in low or "off-grid" in low:
            return "solar_offgrid"
        if "others (districts not specified)" in low:
            return "solar_utility_other"
        if "n.a. 251" in low:
            return "solar_utility_251"
    if source == "Wind" and (district.strip().upper() == "N.A." or name.strip() == "Tamil Nadu-N.A."):
        return "wind_unspecified"
    return None


def read_evidence_sheet(path, sheet_name):
    return pd.read_excel(path, sheet_name=sheet_name, header=3)


def load_current_boundaries():
    response = requests.get(BOUNDARY_URL, timeout=60)
    response.raise_for_status()
    boundaries = gpd.GeoDataFrame.from_features(response.json()["features"], crs="EPSG:4326")
    boundaries["Allocation District"] = boundaries["district"].map(canonical_district)
    return boundaries[["Allocation District", "geometry"]].dissolve(by="Allocation District").reset_index()


def spatially_assign(evidence, boundaries, lat_col="Latitude", lon_col="Longitude"):
    valid = evidence.dropna(subset=[lat_col, lon_col]).copy()
    points = gpd.GeoDataFrame(
        valid,
        geometry=gpd.points_from_xy(valid[lon_col], valid[lat_col]),
        crs="EPSG:4326",
    )
    joined = gpd.sjoin(points, boundaries, how="left", predicate="within")
    unmatched = joined["Allocation District"].isna()
    if unmatched.any():
        fallback = joined.loc[unmatched, "Harmonized district"].map(canonical_district)
        joined.loc[unmatched, "Allocation District"] = fallback
    return pd.DataFrame(joined.drop(columns=["geometry", "index_right"], errors="ignore"))


def normalized_template(old_alloc, name_pattern):
    rows = old_alloc[
        old_alloc["Name of Power Plant"].astype(str).str.contains(name_pattern, case=False, na=False, regex=True)
    ].copy()
    if rows.empty:
        raise ValueError(f"No enhanced allocation template matched {name_pattern!r}")
    rows["Allocated District"] = rows["Allocated District"].map(canonical_district)
    shares = rows.groupby("Allocated District", as_index=False)["Allocation Share"].sum()
    shares["Allocation Share"] /= shares["Allocation Share"].sum()
    exemplar = rows.iloc[0]
    return shares, exemplar


def split_tirunelveli_share(shares, current_proxy):
    shares = shares.copy()
    mask = shares["Allocated District"].eq("Tirunelveli")
    if not mask.any():
        return shares
    combined = current_proxy.get("Tirunelveli", 0.0) + current_proxy.get("Tenkasi", 0.0)
    if combined <= 0:
        return shares
    original_share = float(shares.loc[mask, "Allocation Share"].sum())
    shares = shares.loc[~mask].copy()
    shares = pd.concat(
        [
            shares,
            pd.DataFrame(
                [
                    {
                        "Allocated District": "Tirunelveli",
                        "Allocation Share": original_share * current_proxy.get("Tirunelveli", 0.0) / combined,
                    },
                    {
                        "Allocated District": "Tenkasi",
                        "Allocation Share": original_share * current_proxy.get("Tenkasi", 0.0) / combined,
                    },
                ]
            ),
        ],
        ignore_index=True,
    )
    shares = shares[shares["Allocation Share"] > 0].copy()
    shares["Allocation Share"] /= shares["Allocation Share"].sum()
    return shares.sort_values("Allocated District").reset_index(drop=True)


def style_table_sheet(ws, header_row=1, freeze="A2", autofilter=True):
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = freeze
    if autofilter and ws.max_row >= header_row:
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(ws.max_column)}{ws.max_row}"
    for cell in ws[header_row]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[header_row].height = 30
    for row in ws.iter_rows(min_row=header_row + 1, max_row=ws.max_row):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=False)
            cell.border = BOTTOM_BORDER


def set_widths(ws, widths):
    for col, width in widths.items():
        ws.column_dimensions[col].width = width


def reset_sheet(wb, name):
    if name in wb.sheetnames:
        index = wb.sheetnames.index(name)
        wb.remove(wb[name])
        return wb.create_sheet(name, index)
    return wb.create_sheet(name)


def append_rows(ws, rows):
    for row in rows:
        ws.append([clean_scalar(v) for v in row])


def write_plant_info(wb, base):
    ws = reset_sheet(wb, "PlantInfo")
    append_rows(ws, [base.columns.tolist()] + base.values.tolist())
    style_table_sheet(ws)
    set_widths(
        ws,
        {"A": 16, "B": 22, "C": 62, "D": 16, "E": 26, "F": 18, "G": 16, "H": 58},
    )
    ws["F2"].number_format = "yyyy-mm-dd"
    for cell in ws["F"][1:]:
        cell.number_format = "yyyy-mm-dd"
    for cell in ws["G"][1:]:
        cell.number_format = "#,##0.0000"
    return ws


def build_allocations(base, old_alloc, wind_points, solar_points):
    solar_251, ex_251 = normalized_template(old_alloc, r"N\.A\. 251")
    solar_other, ex_other = normalized_template(old_alloc, r"Others \(Districts not specified\)")
    solar_rooftop, ex_rooftop = normalized_template(old_alloc, r"Rooftop")
    solar_offgrid, ex_offgrid = normalized_template(old_alloc, r"Off\s*-?grid")

    solar_proxy = solar_points.groupby("Allocation District")["Estimated MW"].sum().to_dict()
    solar_251 = split_tirunelveli_share(solar_251, solar_proxy)
    solar_other = split_tirunelveli_share(solar_other, solar_proxy)

    wind_proxy = wind_points.groupby("Allocation District")["Proxy MW"].sum().astype(float)
    wind_operational_total = float(
        base.loc[(base["Source"] == "Wind") & (base["Commissioning Group"] == "operational"), "Capacity (MW)"].sum()
    )

    known_wind = defaultdict(float)
    for _, row in base[(base["Source"] == "Wind") & (base["Commissioning Group"] == "operational")].iterrows():
        if allocation_category(row) == "wind_unspecified":
            continue
        district = str(row["District"])
        capacity = float(row["Capacity (MW)"])
        if "&" in district:
            parts = [canonical_district(p.strip()) for p in district.split("&")]
            for part in parts:
                known_wind[part] += capacity / len(parts)
        else:
            known_wind[canonical_district(district)] += capacity

    target = wind_proxy / wind_proxy.sum() * wind_operational_total
    gaps = {district: max(float(value) - known_wind.get(district, 0.0), 0.0) for district, value in target.items()}
    positive_gap_total = sum(gaps.values())
    if positive_gap_total <= 0:
        raise ValueError("Wind residual calculation produced no positive district gaps")
    wind_shares = pd.DataFrame(
        [
            {"Allocated District": district, "Allocation Share": gap / positive_gap_total}
            for district, gap in gaps.items()
            if gap > 0
        ]
    ).sort_values("Allocated District")

    template_by_category = {
        "solar_utility_251": (solar_251, ex_251, "Solar Utility"),
        "solar_utility_other": (solar_other, ex_other, "Solar Utility"),
        "solar_rooftop": (solar_rooftop, ex_rooftop, "Solar Rooftop"),
        "solar_offgrid": (solar_offgrid, ex_offgrid, "Solar Off-grid"),
    }

    allocation_rows = []
    for index, row in base.iterrows():
        excel_row = index + 2
        record_id = f"SEP26-{excel_row:04d}"
        category = allocation_category(row)
        capacity = float(row["Capacity (MW)"])

        common = {
            "Source Record ID": record_id,
            "Allocation ID": category or f"base_row_{excel_row:04d}",
            "Base Excel Row": excel_row,
            "Source": row["Source"],
            "Commissioning Group": row["Commissioning Group"],
            "Name of Power Plant": row["Name of Power Plant"],
            "State": row["State"],
            "Source District": row["District"],
            "Commissioning Date": row["Commissioning Date"],
            "Source Capacity (MW)": capacity,
            "Implementing Agency": row["Implementing Agency"],
        }

        if category in template_by_category:
            shares, exemplar, component = template_by_category[category]
            for _, share_row in shares.iterrows():
                share = float(share_row["Allocation Share"])
                allocation_rows.append(
                    {
                        **common,
                        "Allocated District": share_row["Allocated District"],
                        "Allocation Share": share,
                        "Verified Allocated Capacity (MW)": capacity * share,
                        "Allocation Component": component,
                        "Allocation Method": str(exemplar["Allocation Method"]),
                        "Confidence": str(exemplar["Confidence"]),
                        "Source URL": None if pd.isna(exemplar["Source URL"]) else exemplar["Source URL"],
                        "Notes": "Updated from Sep26 capacity using validated allocation shares. The former combined Tirunelveli share is split between Tirunelveli and Tenkasi using current OSM plant geography.",
                    }
                )
            continue

        if category == "wind_unspecified":
            for _, share_row in wind_shares.iterrows():
                share = float(share_row["Allocation Share"])
                allocation_rows.append(
                    {
                        **common,
                        "Allocated District": share_row["Allocated District"],
                        "Allocation Share": share,
                        "Verified Allocated Capacity (MW)": capacity * share,
                        "Allocation Component": "Wind Residual",
                        "Allocation Method": "Residual-gap allocation using current district assignment of supplied OSM turbines and Sep26 known district capacities",
                        "Confidence": "Medium",
                        "Source URL": BOUNDARY_URL,
                        "Notes": "Recalculated for Sep26. Tenkasi is separate; Chengalpattu, Kallakurichi, Ranipet, Tirupathur, and Mayiladuthurai are recombined to the established allocation geography.",
                    }
                )
            continue

        district = str(row["District"])
        if row["Source"] == "Solar" and "Tirunelveli, Tuticorin, Virudhunagar and Ramanathapuram" in district:
            parts = ["Tirunelveli", "Thoothukudi", "Virudhunagar", "Ramanathapuram"]
            method = "Equal split across districts named in the Sep26 source row"
            confidence = "Medium"
        elif row["Source"] == "Wind" and "&" in district:
            parts = [canonical_district(p.strip()) for p in district.split("&")]
            method = "Equal split across districts named in the Sep26 source row"
            confidence = "Medium"
        else:
            parts = [canonical_district(district)]
            method = "District carried from Sep26 base workbook"
            confidence = "Existing"

        share = 1.0 / len(parts)
        for part in parts:
            component = "Direct"
            if row["Source"] == "Solar":
                component = "Solar Utility"
            allocation_rows.append(
                {
                    **common,
                    "Allocated District": part,
                    "Allocation Share": share,
                    "Verified Allocated Capacity (MW)": capacity * share,
                    "Allocation Component": component,
                    "Allocation Method": method,
                    "Confidence": confidence,
                    "Source URL": None,
                    "Notes": "Sep26 is authoritative for the source record.",
                }
            )

    allocations = pd.DataFrame(allocation_rows)
    return allocations, wind_proxy, target, known_wind, gaps, solar_proxy


def write_district_allocation(wb, allocations):
    ws = reset_sheet(wb, "District Allocation")
    columns = [
        "Source Record ID",
        "Allocation ID",
        "Base Excel Row",
        "Source",
        "Commissioning Group",
        "Name of Power Plant",
        "State",
        "Source District",
        "Allocated District",
        "Commissioning Date",
        "Source Capacity (MW)",
        "Allocation Share",
        "Allocated Capacity (MW)",
        "Verified Allocated Capacity (MW)",
        "Implementing Agency",
        "Allocation Component",
        "Allocation Method",
        "Confidence",
        "Source URL",
        "Notes",
    ]
    ws.append(columns)
    for excel_row, (_, row) in enumerate(allocations.iterrows(), start=2):
        values = [row.get(col) for col in columns]
        values[12] = f"=K{excel_row}*L{excel_row}"
        ws.append([clean_scalar(v) for v in values])
    style_table_sheet(ws)
    set_widths(
        ws,
        {
            "A": 16, "B": 24, "C": 14, "D": 14, "E": 21, "F": 58, "G": 14,
            "H": 26, "I": 22, "J": 17, "K": 19, "L": 16, "M": 22, "N": 24,
            "O": 52, "P": 20, "Q": 70, "R": 13, "S": 52, "T": 76,
        },
    )
    for row in range(2, ws.max_row + 1):
        ws.cell(row, 10).number_format = "yyyy-mm-dd"
        for col in (11, 13, 14):
            ws.cell(row, col).number_format = "#,##0.0000"
        ws.cell(row, 12).number_format = "0.000000%"
        ws.cell(row, 17).alignment = Alignment(vertical="top", wrap_text=True)
        ws.cell(row, 20).alignment = Alignment(vertical="top", wrap_text=True)
    return ws


def write_allocation_qc(wb, base, allocations):
    ws = reset_sheet(wb, "Allocation QC")
    headers = [
        "Source Record ID", "Base Excel Row", "Source", "Name of Power Plant",
        "Base Capacity (MW)", "Formula Allocated (MW)", "Formula Difference (MW)",
        "Formula Check", "Verified Allocated (MW)", "Verified Difference (MW)",
        "Verified Check", "Allocation Rows",
    ]
    ws.append(headers)
    last_alloc = len(allocations) + 1
    verified = allocations.groupby("Source Record ID")["Verified Allocated Capacity (MW)"].agg(["sum", "size"])
    for index, row in base.iterrows():
        excel_row = index + 2
        record_id = f"SEP26-{excel_row:04d}"
        cap = float(row["Capacity (MW)"])
        alloc_sum = float(verified.loc[record_id, "sum"])
        diff = alloc_sum - cap
        qc_row = ws.max_row + 1
        ws.append(
            [
                record_id,
                excel_row,
                row["Source"],
                row["Name of Power Plant"],
                cap,
                f'=SUMIF(\'District Allocation\'!$A$2:$A${last_alloc},A{qc_row},\'District Allocation\'!$M$2:$M${last_alloc})',
                f"=F{qc_row}-E{qc_row}",
                f'=IF(ABS(G{qc_row})<=0.000001,"OK","FAIL")',
                alloc_sum,
                diff,
                "OK" if abs(diff) <= 1e-6 else "FAIL",
                int(verified.loc[record_id, "size"]),
            ]
        )

    style_table_sheet(ws)
    set_widths(ws, {"A": 16, "B": 14, "C": 14, "D": 60, "E": 20, "F": 22, "G": 22, "H": 15, "I": 23, "J": 24, "K": 16, "L": 16})
    for row in range(2, ws.max_row + 1):
        for col in (5, 6, 7, 9, 10):
            ws.cell(row, col).number_format = "#,##0.000000"
    ws.conditional_formatting.add(
        f"G2:G{ws.max_row}",
        CellIsRule(operator="notBetween", formula=["-0.000001", "0.000001"], fill=FAIL_FILL),
    )
    ws.conditional_formatting.add(
        f"J2:J{ws.max_row}",
        CellIsRule(operator="notBetween", formula=["-0.000001", "0.000001"], fill=FAIL_FILL),
    )
    return ws


def write_technology_qc(wb, base, allocations):
    ws = reset_sheet(wb, "Technology QC")
    headers = ["Source", "Commissioning Group", "Sep26 Base MW", "Verified Allocated MW", "Difference MW", "Check"]
    ws.append(headers)
    base_totals = base.groupby(["Source", "Commissioning Group"], dropna=False)["Capacity (MW)"].sum()
    alloc_totals = allocations.groupby(["Source", "Commissioning Group"], dropna=False)["Verified Allocated Capacity (MW)"].sum()
    for key, base_mw in base_totals.items():
        alloc_mw = float(alloc_totals.get(key, 0.0))
        diff = alloc_mw - float(base_mw)
        ws.append([key[0], key[1], float(base_mw), alloc_mw, diff, "OK" if abs(diff) <= 1e-6 else "FAIL"])
    style_table_sheet(ws)
    set_widths(ws, {"A": 18, "B": 24, "C": 20, "D": 23, "E": 18, "F": 12})
    for row in range(2, ws.max_row + 1):
        for col in (3, 4, 5):
            ws.cell(row, col).number_format = "#,##0.0000"
    return ws


def write_district_summary(wb, allocations):
    ws = reset_sheet(wb, "District Summary")
    title = "Operational Capacity Aggregated by Allocated District and Technology (MW)"
    columns = [
        "District", "Coal", "Oil & Gas", "Nuclear", "Bio Power", "Hydro", "Small-Hydro",
        "Wind", "Solar Utility", "Solar Rooftop", "Solar Off-grid", "Solar Total", "Total",
    ]
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(columns))
    ws.cell(1, 1, title)
    ws.cell(1, 1).fill = TITLE_FILL
    ws.cell(1, 1).font = TITLE_FONT
    ws.cell(1, 1).alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 28
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(columns))
    ws.cell(2, 1, "Operational records only. Values are independently verified from the allocation table. Sep26 remains authoritative for all source capacities.")
    ws.cell(2, 1).fill = NOTE_FILL
    ws.cell(2, 1).alignment = Alignment(wrap_text=True)
    ws.append(columns)

    operational = allocations[allocations["Commissioning Group"].eq("operational")].copy()
    districts = sorted(operational["Allocated District"].dropna().unique())
    for district in districts:
        subset = operational[operational["Allocated District"].eq(district)]
        values = {
            source: float(subset.loc[subset["Source"].eq(source), "Verified Allocated Capacity (MW)"].sum())
            for source in ["Coal", "Oil & Gas", "Nuclear", "Bio Power", "Hydro", "Small-Hydro", "Wind"]
        }
        solar = subset[subset["Source"].eq("Solar")]
        utility = float(solar.loc[solar["Allocation Component"].eq("Solar Utility"), "Verified Allocated Capacity (MW)"].sum())
        rooftop = float(solar.loc[solar["Allocation Component"].eq("Solar Rooftop"), "Verified Allocated Capacity (MW)"].sum())
        offgrid = float(solar.loc[solar["Allocation Component"].eq("Solar Off-grid"), "Verified Allocated Capacity (MW)"].sum())
        solar_total = utility + rooftop + offgrid
        total = sum(values.values()) + solar_total
        ws.append([district, *values.values(), utility, rooftop, offgrid, solar_total, total])

    style_table_sheet(ws, header_row=3, freeze="B4")
    set_widths(ws, {"A": 24, **{get_column_letter(i): 16 for i in range(2, len(columns) + 1)}})
    for row in range(4, ws.max_row + 1):
        for col in range(2, len(columns) + 1):
            ws.cell(row, col).number_format = "#,##0.000"
    return ws


def previous_distribution(old_alloc):
    previous = old_alloc[
        old_alloc["Source"].isin(["Solar", "Wind"])
        & old_alloc["Commissioning Group"].eq("operational")
    ].copy()
    previous["Allocated District"] = previous["Allocated District"].map(canonical_district)
    return previous.groupby(["Source", "Allocated District"])["Allocated Capacity (MW)"].sum()


def write_change_log(wb, old_alloc, allocations):
    ws = reset_sheet(wb, "Change Log")
    headers = ["Technology", "District", "Previous Allocated MW", "Updated Allocated MW", "Change MW", "Reason"]
    ws.append(headers)
    old = previous_distribution(old_alloc)
    current_rows = allocations[
        allocations["Source"].isin(["Solar", "Wind"])
        & allocations["Commissioning Group"].eq("operational")
    ]
    current = current_rows.groupby(["Source", "Allocated District"])["Verified Allocated Capacity (MW)"].sum()
    keys = sorted(set(old.index).union(current.index))
    for source, district in keys:
        old_mw = float(old.get((source, district), 0.0))
        new_mw = float(current.get((source, district), 0.0))
        diff = new_mw - old_mw
        if source == "Solar":
            reason = "Sep26 solar capacity update; validated shares retained, with Tirunelveli/Tenkasi coordinate split for utility solar."
        else:
            reason = "Wind residual-gap model rerun using Sep26 capacities and current district assignment; Tenkasi retained separately."
        ws.append([source, district, old_mw, new_mw, diff, reason])
    style_table_sheet(ws)
    set_widths(ws, {"A": 16, "B": 24, "C": 22, "D": 22, "E": 18, "F": 90})
    for row in range(2, ws.max_row + 1):
        for col in (3, 4, 5):
            ws.cell(row, col).number_format = "#,##0.000"
        ws.cell(row, 6).alignment = Alignment(vertical="top", wrap_text=True)
    return ws


def write_methodology(wb, base, allocations, wind_points, solar_points):
    ws = reset_sheet(wb, "Methodology")
    ws.merge_cells("A1:F1")
    ws["A1"] = "Tamil Nadu Sep26 District Allocation - Methodology and Sources"
    ws["A1"].fill = TITLE_FILL
    ws["A1"].font = TITLE_FONT
    ws["A1"].alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 28

    rows = [
        ("Quality control", "Value"),
        ("Sep26 base rows", len(base)),
        ("District allocation rows", len(allocations)),
        ("Rows failing verified reconciliation", int((allocations.groupby("Source Record ID")["Verified Allocated Capacity (MW)"].sum().sub(base["Capacity (MW)"].to_numpy()).abs() > 1e-6).sum())),
        ("Operational solar MW", float(base.loc[(base.Source == "Solar") & (base["Commissioning Group"] == "operational"), "Capacity (MW)"].sum())),
        ("Operational wind MW", float(base.loc[(base.Source == "Wind") & (base["Commissioning Group"] == "operational"), "Capacity (MW)"].sum())),
        ("Wind turbine points assigned", len(wind_points)),
        ("Solar plant points assigned", len(solar_points)),
        ("", ""),
        ("Topic", "Method / interpretation"),
        ("Authority", "Sep26 PlantInfo is authoritative for every source record, capacity, status, date, district, and agency."),
        ("Scope", "Only statewide or unresolved solar and wind rows are redistributed. Direct Sep26 district records are retained."),
        ("Stable matching", "Semantic allocation IDs are used; Excel row numbers and names containing capacity values are not used as merge keys."),
        ("Solar utility", "Validated utility allocation shares are retained. The updated 9,143.18 MW unspecified row is multiplied by those shares."),
        ("Solar Tenkasi", "The old combined Tirunelveli utility-solar share is split between Tirunelveli and Tenkasi using supplied OSM plant capacity proxies rejoined to current boundaries."),
        ("Rooftop and off-grid", "The previous PM Surya Ghar-derived shares are retained because those allocations are based on installation counts rather than plant coordinates."),
        ("Wind", "OSM turbine proxy is scaled to Sep26 operational wind. Known Sep26 district MW is subtracted; positive gaps are normalized to the 256.375 MW unresolved record."),
        ("Boundary system", "Tenkasi is separate. Chengalpattu->Kanchipuram; Kallakurichi->Viluppuram; Mayiladuthurai->Nagapattinam; Ranipet and Tirupathur->Vellore."),
        ("Boundary source", BOUNDARY_URL),
        ("Limitations", "OSM is community-maintained. Most wind turbines lack MW tags, so turbine count is the main wind proxy. Solar footprint area is also a proxy where explicit MW tags are absent."),
    ]
    for row in rows:
        ws.append(row)
    for cell in ws[3]:
        cell.fill = SECTION_FILL
        cell.font = Font(bold=True)
    topic_row = 12
    for cell in ws[topic_row]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    ws.sheet_view.showGridLines = False
    set_widths(ws, {"A": 34, "B": 110, "C": 14, "D": 14, "E": 14, "F": 14})
    for row in range(2, ws.max_row + 1):
        ws.cell(row, 1).alignment = Alignment(vertical="top", wrap_text=True)
        ws.cell(row, 2).alignment = Alignment(vertical="top", wrap_text=True)
        ws.row_dimensions[row].height = 34 if row >= topic_row else 22
    return ws


def write_wind_evidence(wb, wind_proxy, target, known_wind, gaps, allocations):
    ws = reset_sheet(wb, "Wind District Evidence")
    ws.merge_cells("A1:I1")
    ws["A1"] = "Sep26 Wind Residual Allocation - District Evidence"
    ws["A1"].fill = TITLE_FILL
    ws["A1"].font = TITLE_FONT
    ws.merge_cells("A2:I2")
    ws["A2"] = "Supplied OSM turbines were reassigned to current district polygons. Tenkasi is separate; other new districts are recombined to the established allocation geography."
    ws["A2"].fill = NOTE_FILL
    headers = ["District", "OSM Proxy MW", "Scaled Target MW", "Known Sep26 Wind MW", "Positive Gap MW", "Residual Share", "Allocated N.A. MW", "Boundary Source", "Confidence"]
    ws.append(headers)
    residual_rows = allocations[allocations["Allocation ID"].eq("wind_unspecified")]
    residual = residual_rows.set_index("Allocated District")["Verified Allocated Capacity (MW)"].to_dict()
    positive_total = sum(gaps.values())
    districts = sorted(set(wind_proxy.index).union(known_wind))
    for district in districts:
        gap = gaps.get(district, 0.0)
        share = gap / positive_total if positive_total else 0.0
        ws.append([district, float(wind_proxy.get(district, 0.0)), float(target.get(district, 0.0)), float(known_wind.get(district, 0.0)), gap, share, float(residual.get(district, 0.0)), BOUNDARY_URL, "Medium"])
    style_table_sheet(ws, header_row=3, freeze="A4")
    set_widths(ws, {"A": 24, "B": 18, "C": 19, "D": 23, "E": 18, "F": 16, "G": 20, "H": 76, "I": 14})
    for row in range(4, ws.max_row + 1):
        for col in (2, 3, 4, 5, 7):
            ws.cell(row, col).number_format = "#,##0.000"
        ws.cell(row, 6).number_format = "0.0000%"
    return ws


def write_solar_evidence(wb, solar_proxy, allocations):
    ws = reset_sheet(wb, "Solar District Evidence")
    ws.merge_cells("A1:G1")
    ws["A1"] = "Sep26 Solar Allocation - Updated District Evidence"
    ws["A1"].fill = TITLE_FILL
    ws["A1"].font = TITLE_FONT
    ws.merge_cells("A2:G2")
    ws["A2"] = "Validated shares are retained. Current plant-coordinate geography is used only to split the former combined Tirunelveli utility share between Tirunelveli and Tenkasi."
    ws["A2"].fill = NOTE_FILL
    headers = ["District", "Current OSM Solar Proxy MW", "Allocated Utility MW", "Allocated Rooftop MW", "Allocated Off-grid MW", "Total Allocated Solar MW", "Boundary Source"]
    ws.append(headers)
    solar = allocations[(allocations["Source"] == "Solar") & (allocations["Commissioning Group"] == "operational")]
    districts = sorted(set(solar["Allocated District"]).union(solar_proxy))
    for district in districts:
        subset = solar[solar["Allocated District"].eq(district)]
        utility = float(subset.loc[subset["Allocation Component"].eq("Solar Utility"), "Verified Allocated Capacity (MW)"].sum())
        rooftop = float(subset.loc[subset["Allocation Component"].eq("Solar Rooftop"), "Verified Allocated Capacity (MW)"].sum())
        offgrid = float(subset.loc[subset["Allocation Component"].eq("Solar Off-grid"), "Verified Allocated Capacity (MW)"].sum())
        ws.append([district, float(solar_proxy.get(district, 0.0)), utility, rooftop, offgrid, utility + rooftop + offgrid, BOUNDARY_URL])
    style_table_sheet(ws, header_row=3, freeze="A4")
    set_widths(ws, {"A": 24, "B": 26, "C": 22, "D": 22, "E": 22, "F": 24, "G": 76})
    for row in range(4, ws.max_row + 1):
        for col in range(2, 7):
            ws.cell(row, col).number_format = "#,##0.000"
    return ws


def write_source_qc_sheet(wb, source, allocations):
    name = f"{source} Allocation QC"
    ws = reset_sheet(wb, name)
    headers = ["Allocation ID", "Source Record ID", "Name of Power Plant", "Source MW", "Verified Allocated MW", "Difference MW", "Check", "Allocation Rows"]
    ws.append(headers)
    subset = allocations[allocations["Source"].eq(source)]
    grouped = subset.groupby(["Allocation ID", "Source Record ID", "Name of Power Plant", "Source Capacity (MW)"], dropna=False)["Verified Allocated Capacity (MW)"].agg(["sum", "size"]).reset_index()
    for _, row in grouped.iterrows():
        diff = float(row["sum"]) - float(row["Source Capacity (MW)"])
        ws.append([row["Allocation ID"], row["Source Record ID"], row["Name of Power Plant"], float(row["Source Capacity (MW)"]), float(row["sum"]), diff, "OK" if abs(diff) <= 1e-6 else "FAIL", int(row["size"])])
    style_table_sheet(ws)
    set_widths(ws, {"A": 26, "B": 16, "C": 66, "D": 18, "E": 22, "F": 18, "G": 12, "H": 16})
    for row in range(2, ws.max_row + 1):
        for col in (4, 5, 6):
            ws.cell(row, col).number_format = "#,##0.000000"
    return ws


def write_source_methodology(wb, source):
    name = f"{source} Methodology"
    ws = reset_sheet(wb, name)
    ws.merge_cells("A1:B1")
    ws["A1"] = f"Sep26 {source} Allocation Methodology"
    ws["A1"].fill = TITLE_FILL
    ws["A1"].font = TITLE_FONT
    if source == "Solar":
        rows = [
            ("Scope", "Only unresolved/statewide solar records are redistributed; direct Sep26 district records are retained."),
            ("Utility", "Validated hybrid OSM/official-anchor shares are retained and applied to Sep26 capacities."),
            ("Tenkasi", "The old Tirunelveli combined utility share is split using current OSM plant proxy MW for Tirunelveli and Tenkasi."),
            ("Rooftop", "The validated PM Surya Ghar installation shares are retained."),
            ("Off-grid", "The validated PM Surya Ghar proxy shares are retained at Low confidence."),
            ("Boundary source", BOUNDARY_URL),
        ]
    else:
        rows = [
            ("Scope", "Only the unresolved 256.375 MW operational wind record is redistributed."),
            ("Proxy", "Supplied OSM turbines use their existing Proxy MW values and are rejoined to current district polygons."),
            ("Residual gap", "Mapped proxy is scaled to 12,299.49 MW; known Sep26 district MW is subtracted; positive gaps are normalized to 256.375 MW."),
            ("Tenkasi", "Tenkasi is retained separately from Tirunelveli."),
            ("Boundary source", BOUNDARY_URL),
            ("Confidence", "Medium. Most turbine capacities are proxy values rather than explicit MW tags."),
        ]
    ws.append(["Topic", "Method / interpretation"])
    for row in rows:
        ws.append(row)
    for cell in ws[2]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    ws.sheet_view.showGridLines = False
    set_widths(ws, {"A": 26, "B": 110})
    for row in range(2, ws.max_row + 1):
        ws.cell(row, 1).alignment = Alignment(vertical="top", wrap_text=True)
        ws.cell(row, 2).alignment = Alignment(vertical="top", wrap_text=True)
        ws.row_dimensions[row].height = 34
    return ws


def verify(base, allocations):
    row_totals = allocations.groupby("Source Record ID")["Verified Allocated Capacity (MW)"].sum()
    expected_ids = [f"SEP26-{row:04d}" for row in range(2, len(base) + 2)]
    missing = set(expected_ids) - set(row_totals.index)
    extra = set(row_totals.index) - set(expected_ids)
    if missing or extra:
        raise AssertionError(f"Allocation record mismatch: missing={missing}, extra={extra}")
    diffs = []
    for idx, row in base.iterrows():
        record_id = f"SEP26-{idx + 2:04d}"
        diffs.append(float(row_totals[record_id]) - float(row["Capacity (MW)"]))
    if max(abs(value) for value in diffs) > 1e-6:
        raise AssertionError(f"Row reconciliation failed: max difference {max(abs(value) for value in diffs)}")

    base_totals = base.groupby(["Source", "Commissioning Group"])["Capacity (MW)"].sum().sort_index()
    alloc_totals = allocations.groupby(["Source", "Commissioning Group"])["Verified Allocated Capacity (MW)"].sum().sort_index()
    aligned = base_totals.to_frame("base").join(alloc_totals.to_frame("allocated"), how="outer").fillna(0)
    if float((aligned["base"] - aligned["allocated"]).abs().max()) > 1e-6:
        raise AssertionError("Technology/status reconciliation failed")

    targets = {
        ("Solar", "operational"): 14121.46,
        ("Solar", "pipeline"): 200.0,
        ("Wind", "operational"): 12299.49,
        ("Wind", "pipeline"): 973.5,
    }
    for key, expected in targets.items():
        actual = float(base_totals.loc[key])
        if not math.isclose(actual, expected, abs_tol=1e-6):
            raise AssertionError(f"Unexpected Sep26 total for {key}: {actual} != {expected}")

    unresolved_target = allocations[
        allocations["Allocation ID"].isin(
            ["solar_utility_251", "solar_utility_other", "solar_rooftop", "solar_offgrid", "wind_unspecified"]
        )
    ]
    if unresolved_target["Allocated District"].astype(str).str.upper().isin(["N.A.", "NA", "NAN"]).any():
        raise AssertionError("A targeted solar/wind allocation remains unresolved")
    return aligned


def main():
    base = pd.read_excel(BASE_PATH, sheet_name="PlantInfo")
    base["Capacity (MW)"] = pd.to_numeric(base["Capacity (MW)"], errors="raise")
    old_alloc = pd.read_excel(ENHANCED_PATH, sheet_name="District Allocation")

    boundaries = load_current_boundaries()
    wind_raw = read_evidence_sheet(ENHANCED_PATH, "Overpass Wind Turbines")
    solar_raw = read_evidence_sheet(ENHANCED_PATH, "Overpass Solar Plants")
    wind_points = spatially_assign(wind_raw, boundaries)
    solar_points = spatially_assign(solar_raw, boundaries)

    allocations, wind_proxy, wind_target, known_wind, wind_gaps, solar_proxy = build_allocations(
        base, old_alloc, wind_points, solar_points
    )
    aligned = verify(base, allocations)

    wb = load_workbook(ENHANCED_PATH)
    write_plant_info(wb, base)
    write_district_allocation(wb, allocations)
    write_allocation_qc(wb, base, allocations)
    write_technology_qc(wb, base, allocations)
    write_district_summary(wb, allocations)
    write_change_log(wb, old_alloc, allocations)
    write_methodology(wb, base, allocations, wind_points, solar_points)
    write_wind_evidence(wb, wind_proxy, wind_target, known_wind, wind_gaps, allocations)
    write_solar_evidence(wb, solar_proxy, allocations)
    write_source_qc_sheet(wb, "Solar", allocations)
    write_source_qc_sheet(wb, "Wind", allocations)
    write_source_methodology(wb, "Solar")
    write_source_methodology(wb, "Wind")

    # The former derived validation/report sheets describe the old allocation.
    for stale in ["Report Comparison", "Prayas Validation"]:
        if stale in wb.sheetnames:
            wb[stale].sheet_state = "hidden"

    wb.calculation = CalcProperties(calcMode="auto", fullCalcOnLoad=True, forceFullCalc=True)
    summary_index = wb.sheetnames.index("District Summary")
    for sheet in wb.worksheets:
        sheet.sheet_view.tabSelected = False
    wb["District Summary"].sheet_view.tabSelected = True
    wb.active = summary_index
    if wb.views:
        wb.views[0].activeTab = summary_index
        wb.views[0].firstSheet = summary_index
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUTPUT_PATH)

    print(f"OUTPUT={OUTPUT_PATH}")
    print(f"BASE_ROWS={len(base)}")
    print(f"ALLOCATION_ROWS={len(allocations)}")
    print(f"WIND_POINTS={len(wind_points)}")
    print(f"SOLAR_POINTS={len(solar_points)}")
    print(f"MAX_TECH_DIFF={float((aligned['base'] - aligned['allocated']).abs().max()):.12f}")
    wind_alloc = allocations[allocations["Allocation ID"].eq("wind_unspecified")]
    print("WIND_RESIDUAL_ALLOCATION")
    print(wind_alloc[["Allocated District", "Verified Allocated Capacity (MW)"]].to_string(index=False))


if __name__ == "__main__":
    main()
