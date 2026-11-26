"""Transfer capability (TTC/ATC) declarations for the Southern Region, FY2025-26.

    uv run --no-project --with pypdf --with pandas python scripts/data_preparation/prepare_grid_india_ttc.py --import-from <folder>

Reads Grid-India's monthly PDF declarations (state import capability of the
Southern Region states, and the S1-(S2&S3) corridor with S3 import), copies
each distinct file into data/source/grid_india_ttc_fy2025_26/pdf under a
normalised name (``<kind>_<YYYY-MM>_rev<n>.pdf``) and writes two tables
next to them:

- ``transfer_capability_fy2025_26.csv``: one row per area, month, revision
  and time period, with TTC, reliability margin, ATC, net GNA and the margin
  for T-GNA (MW), and ``latest`` marking the highest revision present for
  that area and month;
- ``limiting_constraints_fy2025_26.csv``: the limiting constraints listed
  for each area, month and revision.

Months outside FY2025-26 are skipped. pypdf is not in requirements.txt,
hence the ``uv run --with pypdf`` call.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "data" / "source" / "grid_india_ttc_fy2025_26"
FY_START, FY_END = pd.Period("2025-04", "M"), pd.Period("2026-03", "M")

MONTH = re.compile(r"1st\s+to\s+\d+\w*\s+([A-Za-z]+)\s+(\d{4})")
ROW = re.compile(
    r"(?P<period>\d{2}\s*-\s*\d{2})(?:\s*Hrs)?\s+(?P<blocks>\d+\s*-\s*\d+)\s+"
    r"(?P<ttc>\d+)\s+(?P<rm>\d+)\s+(?P<atc>\d+)\s+(?P<net_gna>\d+|-)\s+(?P<tgna_margin>\d+|-)\s+"
    r"(?P<change>[+-]?\d+|-)\s+(?P<remark>R\d+|-)"
)
CONSTRAINT = re.compile(r"^\s*(?:ix|iv|v?i{1,3}|v|x)\.\s+(.+)$", re.IGNORECASE)


def area_of(text: str) -> str | None:
    """The area a page declares: a state, the S1-(S2&S3) corridor or S3 import."""
    if re.search(r"Transfer Capability between S1-\s*\(S2&S3\)", text):
        return "S1-(S2&S3)"
    if re.search(r"Import Capability of S3", text):
        return "S3"
    found = re.search(r"Import Transfer Capability of\s+(.+?)\s+for", text)
    return found.group(1).strip().replace("Andra", "Andhra") if found else None


def parse_page(text: str) -> tuple[dict, list[dict], list[str]] | None:
    """Header, data rows and limiting constraints of one declaration page."""
    area, month = area_of(text), MONTH.search(text)
    rows = [m.groupdict() for m in ROW.finditer(text)]
    if area is None or month is None or not rows:
        return None
    revision = re.search(r"Revision No\.?\s*(\d+)", text)
    issued = re.search(r"Issue Date:\s*(\d{2}/\d{2}/\d{4})", text)
    header = {
        "area": area,
        "month": pd.Period(f"{month.group(1)[:3]} {month.group(2)}", "M"),
        "revision": int(revision.group(1)) if revision else None,
        "issue_date": pd.to_datetime(issued.group(1), dayfirst=True).date() if issued else None,
    }
    constraints = [m.group(1).strip() for line in text.splitlines() if (m := CONSTRAINT.match(line))]
    return header, rows, constraints


def read_pdf(path: Path) -> list[str]:
    from pypdf import PdfReader

    return [page.extract_text() or "" for page in PdfReader(path).pages]


def collect(folder: Path, pdf_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Parse every PDF in ``folder``; copy distinct files to ``pdf_dir``."""
    pdf_dir.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    rows, constraints = [], []
    for path in sorted(folder.glob("*.pdf")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen:
            continue
        pages = [parsed for text in read_pdf(path) if (parsed := parse_page(text))]
        if not pages:
            print(f"  Skipped {path.name}: no declaration table found")
            continue
        seen.add(digest)
        month = pages[0][0]["month"]
        if not FY_START <= month <= FY_END:
            print(f"  Skipped {path.name}: {month} is outside FY2025-26")
            continue
        revision = pages[0][0]["revision"]
        if revision is None:
            revision = int(re.search(r"(?i)R(?:ev)?\s*(\d+)", path.stem).group(1))
        kind = "s1_s2s3_corridor" if any(h["area"] == "S1-(S2&S3)" for h, _, _ in pages) else "sr_state_import"
        target = pdf_dir / f"{kind}_{month}_rev{revision}.pdf"
        shutil.copyfile(path, target)
        for header, data, listed in pages:
            header = {**header, "revision": revision, "source_file": target.name, "original_file": path.name}
            rows += [{**header, **row} for row in data]
            constraints += [{**header, "constraint": text} for text in listed]
    table = pd.DataFrame(rows)
    for column in ("ttc", "rm", "atc", "net_gna", "tgna_margin"):
        table[f"{column}_mw"] = pd.to_numeric(table.pop(column), errors="coerce")
    table["period"] = table.period.str.replace(r"\s+", "", regex=True)
    table["blocks"] = table.blocks.str.replace(r"\s+", "", regex=True)
    table["latest"] = table.revision.eq(table.groupby(["area", "month"]).revision.transform("max"))
    table = table.sort_values(["area", "month", "revision", "blocks"])
    order = ["area", "month", "revision", "latest", "issue_date", "period", "blocks", "ttc_mw", "rm_mw",
             "atc_mw", "net_gna_mw", "tgna_margin_mw", "change", "remark", "source_file", "original_file"]
    limits = pd.DataFrame(constraints).sort_values(["area", "month", "revision"])
    limits = limits[["area", "month", "revision", "constraint", "source_file"]]
    return table[order], limits


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--import-from", type=Path, required=True, help="folder with the downloaded PDFs")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    table, limits = collect(args.import_from, args.output_dir / "pdf")
    table.to_csv(args.output_dir / "transfer_capability_fy2025_26.csv", index=False, lineterminator="\n")
    limits.to_csv(args.output_dir / "limiting_constraints_fy2025_26.csv", index=False, lineterminator="\n")
    months = pd.period_range(FY_START, FY_END, freq="M")
    for area, group in table.groupby("area"):
        missing = months.difference(pd.PeriodIndex(group.month.unique()))
        gaps = f"; missing {', '.join(str(m) for m in missing)}" if len(missing) else ""
        print(f"{area}: {group.month.nunique()} months{gaps}")


if __name__ == "__main__":
    main()
