"""Monthly FY2025-26 energy charges of the central coal and lignite stations serving Tamil Nadu.

    python scripts/data_preparation/prepare_central_coal_energy_charges.py

Source: APSPDCL's FPPCA application for FY2025-26, Annexure-1, one sheet per
month (``04-25`` ... ``03-26``). Andhra Pradesh buys from the same stations as
Tamil Nadu, and a station's energy charge rate is the same for every
beneficiary, so the billed rate is actual variable cost divided by actual
energy (column h over column b).

Monthly bills also carry arrears and credits. A month whose billed rate
differs from the station's median month by more than 25% (or that has no
billed energy) is treated as such a billing adjustment, and its charge is
the next month's charge (the previous month's for the last month).

Writes data/model_inputs/central_coal_energy_charges_FY2025_26.csv.
"""

from __future__ import annotations

import re
from pathlib import Path

import openpyxl
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "apspdcl_fppca_fy2025_26" / "annexure_1_fppca.xlsx"
OUTPUT = ROOT / "data" / "model_inputs" / "central_coal_energy_charges_FY2025_26.csv"
# FPPCA station name -> station in data/model_inputs/coal_operating_costs.csv
STATIONS = {
    "NTECL Valluru": "NTECL Vallur",
    "NLC Stage-I": "NLC TPS-II Stage I",
    "NLC Stage-II": "NLC TPS-II Stage II",
    "NTPL(NLC TamilNadu)": "NTPL Tuticorin",
    "NLC NNTPS": "NLC New Neyveli TPS (NNTPS)",
    "NLC TPS- I Expn.": "NLC TPS-I Expansion",
    "NLC TPS- II Expn.": "NLC TPS-II Expansion",
}
ADJUSTMENT_TOLERANCE = 0.25
# Annexure-1 columns: b = actual energy (MU), h = actual variable cost (Rs million).
ENERGY_COLUMN, VARIABLE_COST_COLUMN = 5, 11


def read_bills(path: Path = SOURCE) -> pd.DataFrame:
    """Billed energy and variable cost per station and month."""
    rows = []
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    for sheet in workbook.worksheets:
        if not re.fullmatch(r"\d\d-\d\d", sheet.title.strip()):
            continue
        month = pd.Period(pd.Timestamp(f"20{sheet.title.strip()[3:]}-{sheet.title.strip()[:2]}-01"), "M")
        for row in sheet.iter_rows(min_row=5, values_only=True):
            name = " ".join(str(row[1]).split()) if row[1] is not None else ""
            if name in STATIONS:
                rows.append({"station": STATIONS[name], "month": month,
                             "billed_energy_mu": float(row[ENERGY_COLUMN] or 0.0),
                             "billed_variable_cost_rs_million": float(row[VARIABLE_COST_COLUMN] or 0.0)})
    bills = pd.DataFrame(rows)
    counts = bills.groupby("station").size()
    if set(counts.index) != set(STATIONS.values()) or not counts.eq(12).all():
        raise ValueError(f"Expected 12 months for each of {sorted(STATIONS.values())}, got {counts.to_dict()}")
    return bills.sort_values(["station", "month"], ignore_index=True)


def energy_charges(bills: pd.DataFrame) -> pd.DataFrame:
    """Billed rate per month and the energy charge with billing adjustments replaced."""
    table = bills.copy()
    energy = table["billed_energy_mu"]
    # Rs million / MU = Rs/kWh; x 1000 = INR/MWh.
    table["billed_rate_inr_per_mwh"] = (table["billed_variable_cost_rs_million"] / energy.where(energy > 0)) * 1000.0
    median = table.groupby("station")["billed_rate_inr_per_mwh"].transform("median")
    deviation = (table["billed_rate_inr_per_mwh"] / median - 1.0).abs()
    table["billing_adjustment"] = ~(deviation <= ADJUSTMENT_TOLERANCE)
    charge = table["billed_rate_inr_per_mwh"].where(~table["billing_adjustment"])
    table["energy_charge_inr_per_mwh"] = charge.groupby(table["station"]).transform(lambda s: s.bfill().ffill()).round(0)
    table["month"] = table["month"].astype(str)
    return table


def main() -> None:
    table = energy_charges(read_bills())
    table.round({"billed_energy_mu": 3, "billed_variable_cost_rs_million": 3, "billed_rate_inr_per_mwh": 0}).to_csv(
        OUTPUT, index=False
    )
    print(table.pivot(index="station", columns="month", values="energy_charge_inr_per_mwh").to_string())
    flagged = table[table["billing_adjustment"]]
    print("\nBilling adjustments replaced:")
    for row in flagged.itertuples():
        print(f"  {row.station} {row.month}: billed {row.billed_rate_inr_per_mwh:,.0f} -> {row.energy_charge_inr_per_mwh:,.0f}")
    print(f"\nWrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
