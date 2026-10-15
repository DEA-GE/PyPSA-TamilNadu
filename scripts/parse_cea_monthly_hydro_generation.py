"""Parse saved CEA Sub-report 16 station-generation reports for FY2025-26.

The layout-preserved text extracts are retained under
``data/source/cea_fy2025_26_monthly_generation``.  This parser deliberately
uses them to build the consolidated machine-readable index; the original PDFs
may be removed after the index has been validated.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "source" / "cea_fy2025_26_monthly_generation"
OUTPUT = ROOT / "data" / "model_inputs" / "cea_hydro_monthly_generation_FY2025_26.csv"
CHECKS = ROOT / "data" / "model_inputs" / "cea_hydro_monthly_generation_checks_FY2025_26.csv"

# CEA labels are deliberately kept in their reported form.  The standardised
# name is a stable key for matching to the model in the next analysis step.
STATIONS = {
    "ALIYAR HPS": "aliyar",
    "BHAWANI BARRAGE-II HPS": "bhawani_barrage_ii",
    "BHAWANI BARRAGE-III HPS": "bhawani_barrage_iii",
    "BHAWANI KATTAL": "bhavani_kattalai",
    "KADAMPARI HPS": "kadamparai",
    "KODAYAR-I HPS": "kodayar_i",
    "KODAYAR-II HPS": "kodayar_ii",
    "KUNDAH-I HPS": "kundah_i",
    "KUNDAH-II HPS": "kundah_ii",
    "KUNDAH-III HPS": "kundah_iii",
    "KUNDAH-IV HPS": "kundah_iv",
    "KUNDAH-V HPS": "kundah_v",
    "LOWER METTUR-I HPS": "lower_mettur_i",
    "LOWER METTUR-II HPS": "lower_mettur_ii",
    "LOWER METTUR-III HPS": "lower_mettur_iii",
    "LOWER METTUR-IV HPS": "lower_mettur_iv",
    "METTUR DAM HPS": "mettur_dam",
    "METTUR TUNNEL HPS": "mettur_tunnel",
    "MOYAR HPS": "moyar",
    "PAPANASAM HPS": "papanasam",
    "PARSON`S VALLEY HPS": "parsons_valley",
    "PERIYAR HPS": "periyar",
    "PYKARA HPS": "pykara",
    "PYKARA ULTMATE HPS": "pykara_ultimate",
    "SARKARPATHY HPS": "sarkarpathy",
    "SHOLAYAR HPS (TN)": "sholayar",
    "SURULIYAR HPS": "suruliyar",
}
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def parse_report(path: Path) -> list[dict[str, object]]:
    month = pd.Period(re.search(r"(\d{4}-\d{2})", path.name).group(1), freq="M")
    text = path.read_text(encoding="utf-8", errors="replace")
    rows: list[dict[str, object]] = []
    for label, system in STATIONS.items():
        matches = list(re.finditer(rf"^\s*{re.escape(label)}\s+(.+)$", text, flags=re.MULTILINE))
        if len(matches) != 1:
            raise ValueError(f"Expected one {label!r} row in {path.name}, found {len(matches)}")
        values = [float(value) for value in NUMBER.findall(matches[0].group(1))]
        # Sub-report 16 fields after station name are capacity, annual target,
        # current-month program, current-month actual, ..., cumulative program,
        # cumulative actual, ... .  Some rows have blank capacity, but all
        # selected Tamil Nadu stations include both leading fields.
        if len(values) < 9:
            raise ValueError(f"Too few numeric fields for {label!r} in {path.name}: {values}")
        rows.append({
            "report_month": month.to_timestamp(),
            "cea_station": label,
            "hydro_system": system,
            "monthly_generation_gwh": values[3],
            "apr_to_report_generation_gwh": values[8],
            "source_file": path.name.replace(".txt", ".pdf"),
            "source_text_file": path.name,
        })
    return rows


def main() -> None:
    files = sorted(SOURCE.glob("cea_subreport16_*.txt"))
    if len(files) != 12:
        raise ValueError(f"Expected 12 extracted CEA reports, found {len(files)}")
    data = pd.DataFrame(row for path in files for row in parse_report(path))
    data = data.sort_values(["hydro_system", "report_month"]).reset_index(drop=True)
    if data.duplicated(["report_month", "hydro_system"]).any() or len(data) != 12 * len(STATIONS):
        raise ValueError("CEA station/month parse is incomplete or duplicated")

    data["reconstructed_apr_to_report_gwh"] = data.groupby("hydro_system")["monthly_generation_gwh"].cumsum()
    data["cumulative_residual_gwh"] = (
        data["apr_to_report_generation_gwh"] - data["reconstructed_apr_to_report_gwh"]
    )
    checks = data.groupby("hydro_system", as_index=False).agg(
        report_months=("report_month", "count"),
        annual_from_monthly_gwh=("monthly_generation_gwh", "sum"),
        march_reported_cumulative_gwh=("apr_to_report_generation_gwh", "last"),
        max_abs_cumulative_residual_gwh=("cumulative_residual_gwh", lambda x: x.abs().max()),
    )
    checks["pass"] = checks["max_abs_cumulative_residual_gwh"].le(0.02)
    if not checks["pass"].all():
        failed = checks.loc[~checks["pass"], "hydro_system"].tolist()
        raise ValueError(f"CEA cumulative reconciliation failed for {failed}")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(OUTPUT, index=False, date_format="%Y-%m-%d")
    checks.to_csv(CHECKS, index=False)
    print(f"Wrote {len(data)} station-month observations to {OUTPUT}")
    print(f"Validated {len(checks)} systems against CEA cumulative generation")


if __name__ == "__main__":
    main()
