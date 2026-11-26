"""Tamil Nadu's 400 kV inter-connecting transformers (ICTs) by balancing area, 31 March 2026.

    python scripts/data_preparation/prepare_tn_ict_inventory.py
    uv run --no-project --with pypdf --with pandas python scripts/data_preparation/prepare_tn_ict_inventory.py --compendium <pdf>

The base is the ICT list of SRLDC's "Compendium of Southern Region
Transmission Elements 2023" (status 31 May 2023). With ``--compendium`` the
Tamil Nadu rows (765/400, 400/230 and 400/110 kV, plus Puducherry's 400/230
kV) are extracted again from the PDF into
data/source/srldc_transmission_elements/tn_icts_2023-05.csv. The compendium is
an internal SRLDC document, so only the extracted rows are kept in the
repository.

Units commissioned from June 2023 to March 2026 come from the hand-curated
``tn_ict_additions_2023-06_2026-03.csv`` (SRLDC annual compendiums 2023-24,
2024-25 and 2025-26). A unit whose substation and ICT number already exist
replaces the earlier unit; any other unit is added.

Writes every unit with its balancing area
(``tn_icts_2026-03.csv``, next to the inputs) and the capacity per area and
voltage ratio (data/model_inputs/tn_ict_capacity_by_area_FY2025_26.csv).
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "srldc_transmission_elements"
BASE = SOURCE / "tn_icts_2023-05.csv"
ADDITIONS = SOURCE / "tn_ict_additions_2023-06_2026-03.csv"
UNITS = SOURCE / "tn_icts_2026-03.csv"
OUTPUT = ROOT / "data" / "model_inputs" / "tn_ict_capacity_by_area_FY2025_26.csv"

# Balancing area of every substation with a 400 kV ICT, and how it was found:
# "corridors" = the only area shared by all model corridors the substation's
# circuits belong to (circuits sheet of the grid workbook); "OSM" = the
# substation's OpenStreetMap polygon (Julius Meier's Overpass export of
# 13 Oct 2026) inside the balancing-area polygons; "district" = the
# district-to-area table in data/model_inputs/Balancing_areas.txt.
SUBSTATION_AREAS = {
    "ALAMATHI": ("Chennai North", "corridors"),
    "ANAIKADAVU": ("Coimbatore", "OSM way/417399737"),
    "ARASUR-PG": ("Coimbatore", "OSM way/336535480"),
    "ARIYALUR": ("Villupuram", "corridors"),
    "HOSUR-PG": ("Vellore", "corridors"),
    "KALIVANTHAPATU": ("Chennai South", "corridors"),
    "KAMUTHI": ("Madurai", "district (Ramanathapuram)"),
    "KANARPATTI": ("Tirunelveli", "OSM way/421884729"),
    "KARAIKUDI-PG": ("Madurai", "corridors"),
    "KARAMADAI": ("Coimbatore", "OSM way/337239209"),
    "KARUR PS": ("Coimbatore", "OSM way/1222160794"),
    "KAYATHAR 400/230/110": ("Tirunelveli", "OSM way/618762309"),
    "KKNPP": ("Tirunelveli", "district (Tirunelveli)"),
    "KORATTUR": ("Chennai North", "district (Chennai)"),
    "MADURAI-PG": ("Madurai", "corridors"),
    "MANALI": ("Chennai North", "OSM way/102565511"),
    "MTPS-3": ("Erode", "corridors"),
    "MYWADI": ("Coimbatore", "corridors"),
    "NLC STG2": ("Villupuram", "corridors"),
    "NNTPP": ("Villupuram", "corridors"),
    "NORTH CHENNAI PS": ("Chennai North", "OSM way/1074489047"),
    "NTPL": ("Tirunelveli", "OSM way/323661901"),
    "PALAVADI": ("Vellore", "corridors"),
    "PONDY": ("Puducherry", "corridors"),
    "PUGALUR-PG": ("Trichy", "corridors"),
    "PULIYANTHOP": ("Chennai North", "OSM (Pulianthope Substation)"),
    "RASIPALAYAM": ("Coimbatore", "OSM way/508747385"),
    "S V CHATHRAM": ("Chennai South", "corridors"),
    "SALEM-TN": ("Erode", "district (Salem)"),
    # Chennai district, but the workbook puts the 230 kV KITZ (Sholinganallur)
    # substation in Chennai South; keep both voltage levels in one area.
    "SHOLINGANALLUR": ("Chennai South", "workbook (KITZ Sholinganallur)"),
    "SRIPERUMBUDUR": ("Chennai South", "corridors"),
    "THAPAKUNDU": ("Madurai", "OSM way/508747392"),
    "THENAMPATTY": ("Tirunelveli", "OSM (Thennampatti)"),
    "THERVOI KANDIGAI": ("Chennai North", "OSM way/1355309900"),
    "THIRUVALAM-PG": ("Vellore", "corridors"),
    "THIRUVALAM-TN 400/230": ("Vellore", "corridors"),
    "TIRUNELVELI-PG": ("Tirunelveli", "corridors"),
    "TRICHY-PG": ("Trichy", "corridors"),
    "TTGS": ("Tirunelveli", "OSM way/510513135 (Tuticorin GIS)"),
    "VELLALAVIDUTHI": ("Trichy", "OSM way/1198555727"),
    "VIRUDHUNAGAR": ("Tirunelveli", "OSM way/1206014676"),
}

ICT_ROW = re.compile(
    r"^(?P<sno>\d+)\s+(?P<owner>\S+)\s+(?P<kv>\d{3}/\d{2,3})\s+(?P<bus>.+?)\s+(?P<ict_id>\d+)\s+"
    r"(?P<mva>\d+(?:\.\d+)?)\s+(?P<taps>\d+)\s+(?P<phase>\d)\s+(?P<make>.+?)\s*$"
)
TN_RATIOS = ("400/230", "400/110")


def parse_ict_rows(lines: list[str]) -> pd.DataFrame:
    """ICT rows of the compendium's "Inter-Connecting Transformer Details" pages."""
    rows = [m.groupdict() for line in lines if (m := ICT_ROW.match(line))]
    table = pd.DataFrame(rows, columns=list(ICT_ROW.groupindex))
    return table.astype({"sno": int, "ict_id": int, "mva": float})


def tamil_nadu_rows(icts: pd.DataFrame) -> pd.DataFrame:
    """400/230 and 400/110 kV units (Tamil Nadu and Puducherry) and 765/400 kV units in Tamil Nadu."""
    keep = icts.kv.isin(TN_RATIOS) | (icts.kv.eq("765/400") & icts.bus.isin(SUBSTATION_AREAS))
    return icts.loc[keep, ["owner", "kv", "bus", "ict_id", "mva"]].reset_index(drop=True)


def extract_compendium(pdf: Path) -> pd.DataFrame:
    from pypdf import PdfReader

    pages = [page.extract_text() or "" for page in PdfReader(pdf).pages]
    section = [i for i, text in enumerate(pages) if "Inter-Connecting Transformer Details" in text]
    lines = [line for i in section for line in pages[i].splitlines()]
    return tamil_nadu_rows(parse_ict_rows(lines))


def apply_additions(base: pd.DataFrame, additions: pd.DataFrame) -> pd.DataFrame:
    """Units at 31 March 2026: a new unit with an existing substation and ICT number replaces it."""
    units = base.assign(commissioned="before 2023-06", mva_basis="compendium 2023")
    for row in additions.itertuples(index=False):
        same = units.bus.eq(row.bus) & units.ict_id.eq(row.ict_id)
        if same.sum() > 1:
            raise ValueError(f"{row.bus} ICT {row.ict_id} is listed twice in the base")
        new = {"owner": row.owner, "kv": row.kv, "bus": row.bus, "ict_id": row.ict_id, "mva": row.mva,
               "commissioned": row.date, "mva_basis": row.mva_basis}
        if same.any():
            if units.loc[same, "kv"].item() != row.kv:
                raise ValueError(f"{row.bus} ICT {row.ict_id}: replacement changes the voltage ratio")
            units = units.loc[~same]
        units = pd.concat([units, pd.DataFrame([new])], ignore_index=True)
    return units.sort_values(["bus", "ict_id"]).reset_index(drop=True)


def with_areas(units: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(set(units.bus) - set(SUBSTATION_AREAS))
    if missing:
        raise ValueError(f"No balancing area for {missing}")
    areas = units.bus.map(SUBSTATION_AREAS)
    return units.assign(area=areas.str[0], area_basis=areas.str[1])


def capacity_by_area(base: pd.DataFrame, units: pd.DataFrame) -> pd.DataFrame:
    def summary(frame: pd.DataFrame, label: str) -> pd.DataFrame:
        return frame.groupby(["area", "kv"]).mva.agg(["count", "sum"]).set_axis([f"units_{label}", f"mva_{label}"], axis=1)

    table = summary(with_areas(base), "2023_05").join(summary(units, "2026_03"), how="outer").fillna(0)
    assumed = units[units.mva_basis.str.startswith("assumed")].groupby(["area", "kv"]).mva.sum()
    table["mva_2026_03_assumed"] = assumed.reindex(table.index).fillna(0)
    return table.astype(int).reset_index().rename(columns={"kv": "ratio_kv"})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--compendium", type=Path, help="SRLDC compendium PDF; re-extracts the 2023 base")
    args = parser.parse_args()

    if args.compendium:
        extract_compendium(args.compendium).to_csv(BASE, index=False, lineterminator="\n")
    base = pd.read_csv(BASE)
    additions = pd.read_csv(ADDITIONS)
    units = with_areas(apply_additions(base, additions))
    units.to_csv(UNITS, index=False, lineterminator="\n")
    table = capacity_by_area(base, units)
    table.to_csv(OUTPUT, index=False, lineterminator="\n")
    tn = table[table.area.ne("Puducherry")]
    print(f"Wrote {OUTPUT.name}: {len(units)} units; Tamil Nadu step-down from 400 kV at 31 March 2026:")
    print(tn[tn.ratio_kv.isin(TN_RATIOS)].groupby("ratio_kv")[["units_2026_03", "mva_2026_03"]].sum().to_string())


if __name__ == "__main__":
    main()
