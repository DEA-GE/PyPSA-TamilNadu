from pathlib import Path
import sys

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "data_preparation"))

import prepare_tn_ict_inventory as inventory  # noqa: E402

# Lines as pypdf extracts them from the compendium's ICT pages.
COMPENDIUM_LINES = [
    "Sno. Owner Voltage (kV) Bus ID MVA Taps Phase Make",
    "17 PGCIL 765/400 THIRUVALAM-PG 1 1500 23 1 CGL",
    "19 PGCIL 765/400 VEMAGIRI-PG 1 1500 23 1 ALSTOM",
    "75 TANTRANSCO 400/230 KAYATHAR 400/230/110 2 315 17 3 T&R",
    "88 TANTRANSCO 400/230 S V CHATHRAM 1 315 17 3 BHEL",
    "120 TANTRANSCO 400/110 ALAMATHI 1 200 17 3 BHEL",
    "130 KPTCL 400/220 HASSAN 1 315 17 3 BHEL",
    "207 PGCIL 400/220 HIRIYUR-PG 3 500 17 5 TOSHIBA",
]


def test_compendium_rows_keep_bus_names_with_spaces_and_ratios():
    icts = inventory.parse_ict_rows(COMPENDIUM_LINES)

    assert len(icts) == 7
    assert icts.loc[icts.sno.eq(75), "bus"].item() == "KAYATHAR 400/230/110"
    assert icts.loc[icts.sno.eq(88), ["bus", "ict_id", "mva"]].values.tolist() == [["S V CHATHRAM", 1, 315.0]]


def test_only_tamil_nadu_ratios_and_substations_are_kept():
    kept = inventory.tamil_nadu_rows(inventory.parse_ict_rows(COMPENDIUM_LINES))

    assert sorted(kept.bus) == ["ALAMATHI", "KAYATHAR 400/230/110", "S V CHATHRAM", "THIRUVALAM-PG"]


def test_additions_replace_by_substation_and_number():
    base = pd.DataFrame({"owner": ["PGCIL"] * 2, "kv": ["400/230"] * 2, "bus": ["TRICHY-PG"] * 2,
                         "ict_id": [1, 2], "mva": [315.0, 315.0]})
    additions = pd.DataFrame({"date": ["2024-01-31", "2024-02-01"], "owner": ["PGCIL", "PGCIL"],
                              "bus": ["TRICHY-PG", "TRICHY-PG"], "ict_id": [2, 3], "kv": ["400/230", "400/230"],
                              "mva": [500.0, 500.0], "mva_basis": ["stated", "stated"]})

    units = inventory.apply_additions(base, additions)

    assert units.mva.tolist() == [315.0, 500.0, 500.0]
    assert units.commissioned.tolist() == ["before 2023-06", "2024-01-31", "2024-02-01"]


def test_replacement_must_keep_the_voltage_ratio():
    base = pd.DataFrame({"owner": ["TANTRANSCO"], "kv": ["400/110"], "bus": ["SALEM-TN"], "ict_id": [4], "mva": [200.0]})
    additions = pd.DataFrame({"date": ["2024-05-20"], "owner": ["TANTRANSCO"], "bus": ["SALEM-TN"], "ict_id": [4],
                              "kv": ["400/230"], "mva": [500.0], "mva_basis": ["stated"]})

    with pytest.raises(ValueError, match="changes the voltage ratio"):
        inventory.apply_additions(base, additions)


def test_committed_inventory_matches_grid_india_limiting_transformers():
    """Grid-India's December 2025 S1-(S2&S3) declaration names these 400/230 kV groups."""
    units = pd.read_csv(inventory.UNITS)
    declared = {"SALEM-TN": [315, 315, 500], "PUGALUR-PG": [315, 315, 500], "KALIVANTHAPATU": [315, 315, 500],
                "NNTPP": [500, 500], "NLC STG2": [250, 250]}

    for bus, mva in declared.items():
        found = units[units.bus.eq(bus) & units.kv.eq("400/230")].mva
        assert sorted(found.astype(int)) == mva, bus
    assert units.area.notna().all()
