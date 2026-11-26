from pathlib import Path
import sys

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import prepare_ists_re_plant_output as plants  # noqa: E402
import validate_re_profiles as validate  # noqa: E402


def blocks(account: str, start: str, values: list[float]) -> pd.DataFrame:
    stamps = pd.date_range(start, periods=len(values), freq="15min")
    return pd.DataFrame({"stamp": stamps, "account": account, "actual_mw": values,
                         "scheduled_mw": values, "available_mw": 100.0})


def test_hourly_keeps_complete_hours_and_drops_silent_accounts():
    frame = pd.concat([
        blocks("mytrah_tuticorin", "2025-04-01 00:00", [10.0, 20.0, 30.0, 40.0, 50.0]),  # 1 full hour + 1 block
        blocks("jswrenew_karur_infirm", "2025-04-01 00:00", [0.0] * 4),
    ])

    table = plants.hourly(frame)

    assert table.account.tolist() == ["mytrah_tuticorin"]
    assert table.actual_mw.tolist() == [25.0]
    assert plants.plants().loc["mal_tuticorin_qca6", "qca"]


def test_observed_capacity_factor_uses_hours_when_all_reporting_accounts_report():
    hours = pd.date_range("2025-06-01", periods=3, freq="h")
    table = pd.DataFrame({
        "snapshot": list(hours) + list(hours[:2]),
        "account": ["a"] * 3 + ["b"] * 2,
        "actual_mw": [50.0, 50.0, 50.0, 100.0, 0.0],
        "available_mw": [100.0, 100.0, 100.0, 300.0, 300.0],
    })

    cf = validate.observed(table, ["a", "b"])

    assert cf.index.tolist() == list(hours[:2])  # the hour without b is dropped
    assert cf.tolist() == pytest.approx([150 / 400, 50 / 400])
