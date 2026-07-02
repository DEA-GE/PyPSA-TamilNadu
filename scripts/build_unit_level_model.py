"""Build record-level PyPSA components from the operational ICED plant data."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = ROOT / "inputs" / "tamil_nadu_ra_2025_26"
WORKBOOK = ROOT / "additional_data" / "TamilNadu_ICED_all_source_1782910007272.xlsx.xlsx"
PROFILE_TEMPLATE = INPUT_DIR / "technology-p_max-pu.csv"

BUS = "Tamil_Nadu"
HYDRO_ENERGY_LIMIT_MWH = 5_528_000.0

SOURCE_TO_CARRIER = {
    "Bio Power": "bio_power",
    "Coal": "coal",
    "Hydro": "hydro",
    "Nuclear": "nuclear",
    "Oil & Gas": "oil_gas",
    "Small-Hydro": "small_hydro",
    "Solar": "solar",
    "Wind": "wind",
}

GENERATOR_COLUMNS = [
    "name",
    "bus",
    "p_nom",
    "p_min_pu",
    "p_max_pu",
    "p_init",
    "e_sum_max",
    "carrier",
    "marginal_cost",
    "committable",
    "start_up_cost",
    "shut_down_cost",
    "min_up_time",
    "min_down_time",
    "up_time_before",
    "down_time_before",
    "ramp_limit_up",
    "ramp_limit_down",
    "ramp_limit_start_up",
    "ramp_limit_shut_down",
]

THERMAL_DEFAULTS = {
    "coal": {
        "p_min_pu": 0.55,
        "p_max_pu": 0.85,
        "marginal_cost": 3087.5,
        "start_up_cost_per_mw": 5_000_000.0 / (15_032.5 / 12),
        "shut_down_cost_per_mw": 1_000_000.0 / (15_032.5 / 12),
        "min_up_time": 8,
        "min_down_time": 8,
        "ramp_limit_up": 0.6,
        "ramp_limit_down": 0.6,
        "ramp_limit_start_up": 0.55,
        "ramp_limit_shut_down": 0.55,
    },
    "oil_gas": {
        "p_min_pu": 0.4,
        "p_max_pu": 0.9,
        "marginal_cost": 6025.0,
        "start_up_cost_per_mw": 500_000.0 / (844.58 / 2),
        "shut_down_cost_per_mw": 100_000.0 / (844.58 / 2),
        "min_up_time": 2,
        "min_down_time": 2,
        "ramp_limit_up": 1.0,
        "ramp_limit_down": 1.0,
        "ramp_limit_start_up": 0.4,
        "ramp_limit_shut_down": 0.4,
    },
    "bio_power": {
        "p_min_pu": 0.5,
        "p_max_pu": 0.6,
        "marginal_cost": 4512.5,
        "start_up_cost_per_mw": 200_000.0 / (1_055.12 / 2),
        "shut_down_cost_per_mw": 50_000.0 / (1_055.12 / 2),
        "min_up_time": 4,
        "min_down_time": 4,
        "ramp_limit_up": 1.0,
        "ramp_limit_down": 1.0,
        "ramp_limit_start_up": 0.5,
        "ramp_limit_shut_down": 0.5,
    },
}

PROFILE_BY_SOURCE = {
    "Hydro": "hydro_reservoir",
    "Small-Hydro": "small_hydro",
    "Wind": "wind_fleet",
}


def slugify(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return text or "unnamed"


def is_aggregate_record(row: pd.Series) -> bool:
    """Identify source rows that explicitly describe regional/fleet totals."""
    if row["Source"] in {"Solar", "Wind"}:
        return True
    name = str(row["Name of Power Plant"])
    return bool(
        re.search(
            r"others|districts? not specified|bunched|rooftop|off\s*-?\s*grid",
            name,
            flags=re.I,
        )
    )


def component_id(row: pd.Series) -> str:
    kind = row["record_kind"]
    sequence = int(row["unit_sequence"])
    return f"{row['plant_slug']}__{kind}_{sequence:02d}"


def empty_generator(name: str, carrier: str, capacity: float) -> dict[str, object]:
    return {
        "name": name,
        "bus": BUS,
        "p_nom": capacity,
        "p_min_pu": 0.0,
        "p_max_pu": 1.0,
        "p_init": np.nan,
        "e_sum_max": np.inf,
        "carrier": carrier,
        "marginal_cost": 0.0,
        "committable": False,
        "start_up_cost": 0.0,
        "shut_down_cost": 0.0,
        "min_up_time": 0,
        "min_down_time": 0,
        "up_time_before": 1,
        "down_time_before": 0,
        "ramp_limit_up": np.nan,
        "ramp_limit_down": np.nan,
        "ramp_limit_start_up": np.nan,
        "ramp_limit_shut_down": np.nan,
    }


def build_generator(row: pd.Series, conventional_hydro_mw: float) -> dict[str, object]:
    source = row["Source"]
    carrier = SOURCE_TO_CARRIER[source]
    capacity = float(row["Capacity (MW)"])
    item = empty_generator(row["component_id"], carrier, capacity)

    if carrier in THERMAL_DEFAULTS:
        defaults = THERMAL_DEFAULTS[carrier]
        item.update(
            p_min_pu=defaults["p_min_pu"],
            p_max_pu=defaults["p_max_pu"],
            p_init=0.0,
            marginal_cost=defaults["marginal_cost"],
            committable=True,
            start_up_cost=capacity * defaults["start_up_cost_per_mw"],
            shut_down_cost=capacity * defaults["shut_down_cost_per_mw"],
            min_up_time=defaults["min_up_time"],
            min_down_time=defaults["min_down_time"],
            up_time_before=0,
            down_time_before=defaults["min_down_time"],
            ramp_limit_up=defaults["ramp_limit_up"],
            ramp_limit_down=defaults["ramp_limit_down"],
            ramp_limit_start_up=defaults["ramp_limit_start_up"],
            ramp_limit_shut_down=defaults["ramp_limit_shut_down"],
        )
    elif carrier == "nuclear":
        item.update(
            p_min_pu=0.6120077448977017,
            p_max_pu=0.6120077448977017,
            marginal_cost=800.0,
        )
    elif carrier == "hydro":
        item.update(
            marginal_cost=300.0,
            e_sum_max=HYDRO_ENERGY_LIMIT_MWH * capacity / conventional_hydro_mw,
        )

    return item


def profile_column(row: pd.Series) -> str | None:
    source = row["Source"]
    if source == "Solar":
        name = str(row["Name of Power Plant"])
        is_dre = bool(re.search(r"rooftop|off\s*-?\s*grid", name, flags=re.I))
        return "solar_DRE_rooftop" if is_dre else "solar_utility"
    return PROFILE_BY_SOURCE.get(source)


def serialise_date(value: object) -> str:
    if pd.isna(value):
        return ""
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.notna(parsed):
        return parsed.date().isoformat()
    return str(value)


def main() -> None:
    plants = pd.read_excel(WORKBOOK, sheet_name="PlantInfo")
    operational = plants.loc[plants["Commissioning Group"].eq("operational")].copy()
    operational["excel_row"] = operational.index + 2
    operational["plant_slug"] = operational["Name of Power Plant"].map(slugify).str[:64]
    operational["record_kind"] = np.where(
        operational.apply(is_aggregate_record, axis=1), "aggregate", "unit"
    )
    ordering = operational.assign(
        _commissioning_sort=pd.to_datetime(operational["Commissioning Date"], errors="coerce")
    ).sort_values(
        [
            "plant_slug",
            "record_kind",
            "_commissioning_sort",
            "Capacity (MW)",
            "District",
            "Implementing Agency",
            "excel_row",
        ],
        na_position="last",
    )
    ordering["unit_sequence"] = ordering.groupby(
        ["plant_slug", "record_kind"], sort=False
    ).cumcount() + 1
    operational["unit_sequence"] = ordering["unit_sequence"].astype(int)
    operational["component_id"] = operational.apply(component_id, axis=1)
    operational["duplicate_group_size"] = operational.groupby(
        list(plants.columns), dropna=False
    )["Source"].transform("size")

    assert len(operational) == 280
    assert operational["component_id"].is_unique
    assert set(operational["Source"]) == set(SOURCE_TO_CARRIER)

    is_psp = operational["Name of Power Plant"].str.casefold().eq("kadmparai power house")
    psp = operational.loc[is_psp].copy()
    generator_plants = operational.loc[~is_psp].copy()
    assert len(psp) == 4 and np.isclose(psp["Capacity (MW)"].sum(), 400.0)

    conventional_hydro_mw = generator_plants.loc[
        generator_plants["Source"].eq("Hydro"), "Capacity (MW)"
    ].sum()
    assert np.isclose(conventional_hydro_mw, 1_803.2)

    generators = pd.DataFrame(
        [build_generator(row, conventional_hydro_mw) for _, row in generator_plants.iterrows()],
        columns=GENERATOR_COLUMNS,
    )

    mechanisms = pd.DataFrame(
        [
            {
                **empty_generator("STOA_MTOA_import", "market_import", 6_727.0),
                "e_sum_max": 17_752_000.0,
                "marginal_cost": 6_500.0,
            },
            {
                **empty_generator("unserved_energy", "unserved_energy", 100_000.0),
                "marginal_cost": 100_000.0,
            },
        ],
        columns=GENERATOR_COLUMNS,
    )
    generators = pd.concat([generators, mechanisms], ignore_index=True)
    generators.to_csv(INPUT_DIR / "generators.csv", index=False)

    storage = pd.DataFrame(
        {
            "name": psp["component_id"],
            "bus": BUS,
            "p_nom": psp["Capacity (MW)"].astype(float),
            "p_min_pu": -0.95,
            "p_max_pu": 0.95,
            "carrier": "hydro",
            "marginal_cost": 10.0,
            "cyclic_state_of_charge": True,
            "max_hours": 6.0,
            "efficiency_store": np.sqrt(0.8),
            "efficiency_dispatch": np.sqrt(0.8),
        }
    )
    storage.to_csv(INPUT_DIR / "storage_units.csv", index=False)

    templates = pd.read_csv(PROFILE_TEMPLATE, index_col=0)
    required = {"hydro_reservoir", "wind_fleet", "solar_utility", "solar_DRE_rooftop", "small_hydro"}
    assert required.issubset(templates.columns)
    profiles: dict[str, pd.Series] = {}
    for _, row in generator_plants.iterrows():
        template = profile_column(row)
        if template:
            profiles[row["component_id"]] = templates[template]
    pd.DataFrame(profiles, index=templates.index).to_csv(INPUT_DIR / "generators-p_max_pu.csv")

    metadata = operational[
        [
            "component_id",
            "excel_row",
            "record_kind",
            "unit_sequence",
            "Source",
            "Name of Power Plant",
            "State",
            "District",
            "Commissioning Date",
            "Capacity (MW)",
            "Implementing Agency",
            "duplicate_group_size",
        ]
    ].copy()
    metadata.insert(0, "component_type", np.where(is_psp, "StorageUnit", "Generator"))
    metadata.columns = [
        "component_type",
        "component_id",
        "excel_row",
        "record_kind",
        "unit_sequence",
        "source",
        "plant_name",
        "state",
        "district",
        "commissioning_date",
        "capacity_mw",
        "implementing_agency",
        "duplicate_group_size",
    ]
    metadata["commissioning_date"] = metadata["commissioning_date"].map(serialise_date)
    metadata.to_csv(INPUT_DIR / "component_metadata.csv", index=False)

    source_summary = operational.groupby("Source", sort=True).agg(
        operational_records=("Source", "size"),
        capacity_mw=("Capacity (MW)", "sum"),
    )
    source_summary["capacity_mw"] = source_summary["capacity_mw"].round(6)
    representations = {
        "Bio Power": "57 workbook-record generators",
        "Coal": "49 workbook-record generators",
        "Hydro": "65 generators plus four 100 MW pumped-storage units",
        "Nuclear": "4 workbook-record generators",
        "Oil & Gas": "14 workbook-record generators",
        "Small-Hydro": "39 workbook-record generators",
        "Solar": "27 workbook-record generators",
        "Wind": "21 workbook-record generators",
    }
    source_summary["model_representation"] = pd.Series(representations)
    source_summary.reset_index().rename(columns={"Source": "source"}).to_csv(
        INPUT_DIR / "operational_technology_capacities.csv", index=False
    )

    modeled = generators.loc[
        ~generators["carrier"].isin(["market_import", "unserved_energy"])
    ].groupby("carrier")["p_nom"].sum()
    modeled = modeled.add(storage.groupby("carrier")["p_nom"].sum(), fill_value=0)
    expected = operational.assign(carrier=operational["Source"].map(SOURCE_TO_CARRIER)).groupby(
        "carrier"
    )["Capacity (MW)"].sum()
    pd.testing.assert_series_equal(modeled.sort_index(), expected.sort_index(), check_names=False)

    print(f"Wrote {len(generators)} generators ({len(generator_plants)} workbook records + 2 mechanisms)")
    print(f"Wrote {len(storage)} pumped-storage units")
    print(f"Wrote {len(profiles)} generator availability profiles")
    print(f"Operational capacity: {expected.sum():.4f} MW")


if __name__ == "__main__":
    main()
