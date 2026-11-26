"""Coal unit outages and auxiliary consumption, FY2025-26, for the outage module.

Reads the NITI Dashboard (ICED) unit outage and auxiliary consumption exports in
``data/source/NITI dashboard/`` and writes two model inputs used by
``scripts/generator_outages.py``:

``data/model_inputs/coal_unit_outages_FY2025_26.csv``
    One row per model coal unit and day out of service: ``component``, ``date``,
    ``kind``, ``reason``, ``outage_days`` (share of the day, 0..1),
    ``capacity_mw`` and ``source_unit``. ``kind`` is

    - ``planned``: annual, capital or other planned maintenance;
    - ``forced``: technical failures, raw water, grid, fuel shortage;
    - ``commercial``: no fuel supply agreement, no PPA, financial problem;
    - ``reserve_shutdown``: units off for lack of demand. These are economic
      decisions the unit commitment makes itself; they are kept for reference
      and are never imposed;
    - ``commissioning``: days before the unit's commissioning date
      (``model/component_metadata.csv``).

``data/model_inputs/coal_auxiliary_consumption.csv``
    Auxiliary consumption in % of gross generation for every committable coal
    unit, with its basis: CEA plant data from the NITI export (latest year)
    where available, else the TNERC approved FY2025-26 value, else the CERC
    2024 norm, else an assumption for units below 200 MW (no norm exists).

``data/model_inputs/coal_unit_capability_FY2025_26.csv``
    Demonstrated capability per unit: its best whole day of FY2025-26 as a
    share of its rating (gross daily energy / (rating x 24 h)), on days with
    no outage of any kind in the record, from the NITI daily unit generation
    (``data/model_inputs/niti_dashboard_tamil_nadu_generation_FY2025_26.csv``).
    It stands in for partial deratings, which the outage record does not
    contain. A daily average is a lower bound on what a unit can do in an
    hour, so the cap errs on the low side. Units with fewer than
    ``MIN_CLEAN_DAYS`` such days are not capped (capability 1).

NITI's outage tables give, per unit and day, the share of the day the unit was
out (generation loss = capacity x 24 h x share), compiled from CEA's daily
outage reports (Sub-report 10). Units are matched to the model by plant name
and unit number; the build fails if a coal unit cannot be matched or its
capacity differs.

    python scripts/prepare_coal_outages.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "data" / "source" / "NITI dashboard"
PLANNED_FILE = SOURCE_DIR / "unit outages Tamil Nadu" / "Electricity_Planned_Outage_1795169249625_coal.xlsx"
FORCED_FILE = SOURCE_DIR / "unit outages Tamil Nadu" / "Electricity_Forced_Outage1795168629170_Coal.xlsx"
AUX_FILE = SOURCE_DIR / "auxiliary consumption Tamil Nadu" / "Aux-Consumption_Metatable_1795078497354.xlsx"
MODEL_DIR = ROOT / "model"
UNIT_GENERATION_FILE = ROOT / "data" / "model_inputs" / "niti_dashboard_tamil_nadu_generation_FY2025_26.csv"
OUTAGE_OUTPUT = ROOT / "data" / "model_inputs" / "coal_unit_outages_FY2025_26.csv"
AUX_OUTPUT = ROOT / "data" / "model_inputs" / "coal_auxiliary_consumption.csv"
CAPABILITY_OUTPUT = ROOT / "data" / "model_inputs" / "coal_unit_capability_FY2025_26.csv"
MIN_CLEAN_DAYS = 20

FY_START = pd.Timestamp("2025-04-01")
FY_END = pd.Timestamp("2026-03-31")

# Every reason in the NITI exports; an unknown reason fails the build.
REASON_KIND = {
    "Annual Maintenance": "planned",
    "Annual Maintenance(Boiler Overhaul)": "planned",
    "Boiler Inspection/ Re-Certification": "planned",
    "Capital Maintenance": "planned",
    "Other Planned Maintenance Works": "planned",
    "Financial Problem": "commercial",
    "Technical Reason": "forced",
    "Technical Grid": "forced",
    "Raw Water Unavailability": "forced",
    "Coal and Other Fuel Shortage": "forced",
    "Natural Disaster": "forced",
    "Pollution Problem": "forced",
    "No Fuel Supply Agreement": "commercial",
    "No Power Purchase Agreement": "commercial",
    "Other Commercial Reasons": "commercial",
    "Reserve Shut Down/ Low Schedule": "reserve_shutdown",
    "No Demand from Distribution/Reserve Shut Down": "reserve_shutdown",
}
OUTAGE_COLUMNS = ["component", "date", "kind", "reason", "outage_days", "capacity_mw", "source_unit"]

TNERC_2022 = (
    "TNERC Order No. 07 of 2022 (T.P. No. 1 of 2022), Table 4-41, approved FY2025-26; "
    "https://jmkresearch.com/wp-content/uploads/2022/09/tnerc-tariff-order-FY-2023.pdf"
)
CERC_2024 = (
    "CERC (Terms and Conditions of Tariff) Regulations 2024, Reg. 70(E); "
    "https://www.cercind.gov.in/regulations/notification-2024.pdf"
)
NITI_AUX = "NITI ICED auxiliary consumption export (CEA plant data)"

# Auxiliary consumption per model plant (and unit numbers where a plant has
# several vintages): ("niti", NITI plant name, vintage) uses the latest CEA
# value in the NITI export; ("norm", percent, basis, source) uses a norm or an
# assumption.
AUX_RULES: dict[str, list[tuple[tuple[int, ...] | None, tuple]]] = {
    "ITPCL or Cuddalore TPP": [(None, ("niti", "ITPCL or Cuddalore TPP", "2010-2019"))],
    "Mettur TPS": [
        ((1, 2, 3, 4), ("niti", "Mettur TPS", "1980-1989")),
        ((5,), ("niti", "Mettur TPS", "2010-2019")),
    ],
    "Muthiara TPP": [(None, ("niti", "Muthiara TPP", "2010-2019"))],
    "Neyveli (EXT) TPS I": [(None, ("niti", "Neyveli (EXT) TPS I", "2000-2009"))],
    "Neyveli (EXT) TPS II": [(None, ("niti", "Neyveli (EXT) TPS II", "2010-2019"))],
    "Neyveli (New) TPP /Neyveli Extension": [
        (None, ("norm", 6.75, "CERC 2024 norm: 500 MW set with steam-driven boiler feed pumps "
                "5.25% + 1.5 percentage points for lignite CFBC (pump drive assumed)", CERC_2024)),
    ],
    "Neyveli TPS (Z)": [(None, ("niti", "Neyveli TPS (Z)", "2000-2009"))],
    "Neyveli TPS II": [(None, ("niti", "Neyveli TPS II", "1980-1989"))],
    "North Chennai TPS": [
        ((1, 2, 3), ("niti", "North Chennai TPS", "1990-1999")),
        ((4, 5), ("norm", 7.0, "TNERC approved FY2025-26 (NCTPS II)", TNERC_2022)),
        ((6,), ("norm", 7.0, "TNERC approved FY2025-26 (NCTPS Stage-III)", TNERC_2022)),
    ],
    "OPG Power Generation Private Ltd.": [
        (None, ("norm", 10.0, "assumption: CERC sets no norm for units below 200 MW", CERC_2024)),
    ],
    "TCP Limited": [
        (None, ("norm", 10.0, "assumption: CERC sets no norm for units below 200 MW", CERC_2024)),
    ],
    "Tulsyan NEC Limited": [
        (None, ("norm", 10.0, "assumption: CERC sets no norm for units below 200 MW", CERC_2024)),
    ],
    "Tuticorin (P) TPP": [(None, ("niti", "Tuticorin (P) TPP", "2010-2019"))],
    "Tuticorin Joint Venture": [(None, ("niti", "Tuticorin Joint Venture", "2010-2019"))],
    "Tuticorin TPS": [(None, ("niti", "Tuticorin TPS", "1970-1979"))],
    "Tuticorin Thermal Power Plant": [
        (None, ("norm", 5.25, "CERC 2024 norm: 500 MW and above with steam-driven boiler "
                "feed pumps (pump drive assumed)", CERC_2024)),
    ],
    "Vallur TPP": [(None, ("niti", "Vallur TPP", "2010-2019"))],
}


def coal_units(model_dir: Path = MODEL_DIR) -> pd.DataFrame:
    """Committable coal units of the model with plant name, unit number and commissioning date."""
    generators = pd.read_csv(model_dir / "generators.csv", index_col=0)
    metadata = pd.read_csv(model_dir / "component_metadata.csv")
    committable = generators.index[
        (generators.carrier == "coal") & generators.committable.astype(bool)
    ]
    units = metadata[metadata.component_id.isin(committable)].copy()
    missing = committable.difference(units.component_id)
    if len(missing):
        raise ValueError(f"No component metadata for coal units: {list(missing)}")
    units["unit_no"] = units.unit_sequence.astype(int)
    units["commissioning_date"] = pd.to_datetime(units.commissioning_date)
    columns = ["component_id", "plant_name", "unit_no", "capacity_mw", "commissioning_date"]
    return units[columns].rename(columns={"component_id": "component"}).reset_index(drop=True)


def _read_outage_table(path: Path, sheet: str, days_column: str) -> pd.DataFrame:
    table = pd.read_excel(path, sheet_name=sheet).dropna(subset=["Plant Unit"])
    table = table.rename(columns={days_column: "outage_days"})
    table["date"] = pd.to_datetime(table["Duration"])
    return table


def read_outage_records(
    planned_file: Path = PLANNED_FILE, forced_file: Path = FORCED_FILE
) -> pd.DataFrame:
    """Both NITI outage tables for FY2025-26, one row per unit, day and reason."""
    planned = _read_outage_table(planned_file, "Plant Outage Table data", "Plant Outage Days (In Days)")
    forced = _read_outage_table(forced_file, "Generation Loss Table data", "Outage Days (In Days)")
    table = pd.concat([planned, forced], ignore_index=True)
    table = table[(table.date >= FY_START) & (table.date <= FY_END)].copy()
    unknown = sorted(set(table["Outage Reasons"]) - set(REASON_KIND))
    if unknown:
        raise ValueError(f"Unclassified outage reasons: {unknown}")
    table["kind"] = table["Outage Reasons"].map(REASON_KIND)
    table["unit_no"] = table["Plant Unit"].str.extract(r"Unit\s*(\d+)\s*$")[0].astype(float)
    if table.unit_no.isna().any():
        bad = sorted(table.loc[table.unit_no.isna(), "Plant Unit"].unique())
        raise ValueError(f"No unit number in outage rows: {bad}")
    table["unit_no"] = table.unit_no.astype(int)
    return table


def build_outage_record(records: pd.DataFrame, units: pd.DataFrame) -> pd.DataFrame:
    """Match the NITI rows to model units and add the pre-commissioning days."""
    matched = records.merge(
        units, left_on=["Plant Name", "unit_no"], right_on=["plant_name", "unit_no"], how="left"
    )
    # The coal export also carries nuclear units; any other unmatched plant is an error.
    unmatched = matched[matched.component.isna()]
    coal_plants = set(units.plant_name)
    bad = unmatched[unmatched["Plant Name"].isin(coal_plants)]
    if not bad.empty:
        raise ValueError(f"Coal outage rows match no model unit: {sorted(bad['Plant Unit'].unique())}")
    matched = matched[matched.component.notna()].copy()
    wrong = matched[(matched["Capacity (In MW)"] - matched.capacity_mw).abs() > 0.01]
    if not wrong.empty:
        raise ValueError(f"Unit capacity differs from the model: {sorted(wrong['Plant Unit'].unique())}")
    duplicated = matched.duplicated(["component", "date"], keep=False)
    if duplicated.any():
        rows = matched.loc[duplicated, ["component", "date", "Outage Reasons"]]
        raise ValueError(f"More than one outage row per unit and day:\n{rows}")
    matched["outage_days"] = matched.outage_days.clip(lower=0.0, upper=1.0)
    record = pd.DataFrame(
        {
            "component": matched.component,
            "date": matched.date,
            "kind": matched.kind,
            "reason": matched["Outage Reasons"],
            "outage_days": matched.outage_days,
            "capacity_mw": matched.capacity_mw,
            "source_unit": matched["Plant Unit"],
        }
    )
    # Before commissioning a unit is unavailable; drop any outage row then.
    late = units[(units.commissioning_date > FY_START) & (units.commissioning_date <= FY_END + pd.Timedelta(days=1))]
    commissioning = []
    for unit in late.itertuples():
        days = pd.date_range(FY_START, unit.commissioning_date - pd.Timedelta(days=1), freq="D")
        record = record[~((record.component == unit.component) & (record.date < unit.commissioning_date))]
        commissioning.append(
            pd.DataFrame(
                {
                    "component": unit.component,
                    "date": days,
                    "kind": "commissioning",
                    "reason": f"Not yet commissioned (commissioning date {unit.commissioning_date.date()})",
                    "outage_days": 1.0,
                    "capacity_mw": unit.capacity_mw,
                    "source_unit": "model/component_metadata.csv",
                }
            )
        )
    record = pd.concat([record, *commissioning], ignore_index=True)
    record = record[record.outage_days > 0]
    return record.sort_values(["component", "date"]).reset_index(drop=True)[OUTAGE_COLUMNS]


def build_auxiliary_table(units: pd.DataFrame, aux_file: Path = AUX_FILE) -> pd.DataFrame:
    """Auxiliary consumption (% of gross generation) for every model coal unit."""
    niti = pd.read_excel(aux_file).dropna(subset=["State"])
    niti = niti[niti.Source.eq("coal")]
    rows = []
    for unit in units.itertuples():
        rules = AUX_RULES.get(unit.plant_name)
        if rules is None:
            raise ValueError(f"No auxiliary consumption rule for plant {unit.plant_name!r}")
        rule = next((r for numbers, r in rules if numbers is None or unit.unit_no in numbers), None)
        if rule is None:
            raise ValueError(f"No auxiliary consumption rule for {unit.component}")
        if rule[0] == "niti":
            _, plant, vintage = rule
            values = niti[(niti["Plant Name"] == plant) & (niti["Plant Vintage"] == vintage)]
            if values.empty:
                raise ValueError(f"No NITI auxiliary consumption for {plant} ({vintage})")
            latest = values.sort_values("Year").iloc[-1]
            percent = float(latest["AuxPowerConsumption (in %)"])
            basis = f"CEA actual {latest['Year']} ({plant}, vintage {vintage})"
            source = NITI_AUX
        else:
            _, percent, basis, source = rule
        rows.append(
            {
                "component": unit.component,
                "plant_name": unit.plant_name,
                "unit_no": unit.unit_no,
                "capacity_mw": unit.capacity_mw,
                "auxiliary_consumption_pct": round(percent, 2),
                "basis": basis,
                "source": source,
            }
        )
    return pd.DataFrame(rows)


def build_capability_table(
    units: pd.DataFrame, record: pd.DataFrame, generation_file: Path = UNIT_GENERATION_FILE
) -> pd.DataFrame:
    """Best whole-day output per unit on days without any recorded outage."""
    daily = pd.read_csv(generation_file, encoding="utf-8-sig")
    daily = daily[(daily.source == "coal") & (daily.frequency == "daily")].copy()
    daily["date"] = pd.to_datetime(daily.date)
    daily["unit_no"] = daily.unit_name.str.extract(r"Unit\s*(\d+)\s*$")[0].astype(float)
    daily = daily.dropna(subset=["unit_no"]).astype({"unit_no": int})
    matched = daily.merge(units, on=["plant_name", "unit_no"], how="inner")
    energy = matched.groupby(["component", "date"], as_index=False).generation_mu.sum()
    energy = energy.merge(units[["component", "capacity_mw"]], on="component")
    energy["daily_share"] = energy.generation_mu * 1000.0 / (energy.capacity_mw * 24.0)
    outage_days = set(zip(record.component, record.date))
    without_outage = pd.Series(
        [(c, d) not in outage_days for c, d in zip(energy.component, energy.date)], index=energy.index
    )
    clean = energy[without_outage & (energy.daily_share > 0)]
    stats = clean.groupby("component").daily_share.agg(
        clean_days="size", median="median", p95=lambda s: s.quantile(0.95), best_day="max"
    )
    table = units[["component", "plant_name", "unit_no", "capacity_mw"]].merge(
        stats, left_on="component", right_index=True, how="left"
    )
    table["clean_days"] = table.clean_days.fillna(0).astype(int)
    enough = table.clean_days >= MIN_CLEAN_DAYS
    table["capability_pu"] = table.best_day.where(enough).clip(upper=1.0).fillna(1.0).round(3)
    table["basis"] = "best whole day without outage, FY2025-26 (NITI daily unit generation)"
    table.loc[~enough, "basis"] = f"not capped: fewer than {MIN_CLEAN_DAYS} days without outage"
    for column in ("median", "p95", "best_day"):
        table[column] = table[column].round(3)
    return table


def main() -> None:
    units = coal_units()
    record = build_outage_record(read_outage_records(), units)
    auxiliary = build_auxiliary_table(units)
    capability = build_capability_table(units, record)
    OUTAGE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    out = record.copy()
    out["date"] = out.date.dt.strftime("%Y-%m-%d")
    out.to_csv(OUTAGE_OUTPUT, index=False, float_format="%.4g")
    auxiliary.to_csv(AUX_OUTPUT, index=False)
    capability.to_csv(CAPABILITY_OUTPUT, index=False)
    unit_days = record.groupby("kind").outage_days.agg(["size", "sum"]).rename(
        columns={"size": "rows", "sum": "unit_days"}
    )
    print(f"Wrote {OUTAGE_OUTPUT.relative_to(ROOT)} ({len(record)} rows, {record.component.nunique()} units)")
    print(unit_days.round(1).to_string())
    weighted = (auxiliary.auxiliary_consumption_pct * auxiliary.capacity_mw).sum() / auxiliary.capacity_mw.sum()
    print(f"Wrote {AUX_OUTPUT.relative_to(ROOT)} ({len(auxiliary)} units, capacity-weighted {weighted:.2f}%)")
    capped = capability[capability.capability_pu < 1.0]
    lost = ((1.0 - capped.capability_pu) * capped.capacity_mw).sum()
    print(
        f"Wrote {CAPABILITY_OUTPUT.relative_to(ROOT)} ({len(capped)} of {len(capability)} units capped, "
        f"{lost:,.0f} MW of {capability.capacity_mw.sum():,.0f} MW rating)"
    )


if __name__ == "__main__":
    main()
