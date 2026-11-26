from pathlib import Path
import sys

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "data_preparation"))

import build_9ba_model as build  # noqa: E402
import prepare_central_coal_energy_charges as prep  # noqa: E402


def bills(rates: list[float], energy: list[float] | None = None) -> pd.DataFrame:
    months = pd.period_range("2025-04", periods=len(rates), freq="M")
    energy = energy or [10.0] * len(rates)
    return pd.DataFrame({
        "station": "NTECL Vallur", "month": months, "billed_energy_mu": energy,
        "billed_variable_cost_rs_million": [rate / 1000.0 * mu for rate, mu in zip(rates, energy)],
    })


def test_billing_adjustments_take_the_next_months_charge():
    # A credit month, an arrears month, and the last month as an arrears month.
    table = prep.energy_charges(bills([4000, -4500, 3900, 8000, 3800, 9000]))

    assert table["billing_adjustment"].tolist() == [False, True, False, True, False, True]
    assert table["energy_charge_inr_per_mwh"].tolist() == [4000, 3900, 3900, 3800, 3800, 3800]


def test_month_without_billed_energy_is_an_adjustment():
    table = prep.energy_charges(bills([3000, 3100, 3200], energy=[10.0, 0.0, 10.0]))

    assert table["billing_adjustment"].tolist() == [False, True, False]
    assert table["energy_charge_inr_per_mwh"].tolist() == [3000, 3200, 3200]


def charges_file(tmp_path: Path) -> Path:
    path = tmp_path / "charges.csv"
    pd.DataFrame({"station": ["NTECL Vallur"] * 2, "month": ["2025-04", "2025-05"],
                  "energy_charge_inr_per_mwh": [4079.0, 3744.0]}).to_csv(path, index=False)
    return path


def test_monthly_costs_follow_the_snapshot_month(tmp_path):
    audit = pd.DataFrame({"name": ["vallur_tpp__unit_01", "mettur_tps__unit_01"],
                          "station": ["NTECL Vallur", "Mettur TPS-I"]})
    snapshots = pd.Series(pd.to_datetime(["2025-04-30 23:00", "2025-05-01 00:00"]))

    frame = build.coal_monthly_marginal_costs(audit, snapshots, charges_file(tmp_path))

    assert list(frame.columns) == ["vallur_tpp__unit_01"]
    assert frame["vallur_tpp__unit_01"].tolist() == [4079.0, 3744.0]


def test_monthly_costs_must_cover_every_snapshot_month(tmp_path):
    audit = pd.DataFrame({"name": ["vallur_tpp__unit_01"], "station": ["NTECL Vallur"]})
    snapshots = pd.Series(pd.to_datetime(["2025-05-31 23:00", "2025-06-01 00:00"]))

    with pytest.raises(ValueError, match="do not cover every snapshot month"):
        build.coal_monthly_marginal_costs(audit, snapshots, charges_file(tmp_path))


def test_monthly_costs_for_unknown_station_fail(tmp_path):
    audit = pd.DataFrame({"name": ["mettur_tps__unit_01"], "station": ["Mettur TPS-I"]})
    snapshots = pd.Series(pd.to_datetime(["2025-04-01 00:00"]))

    with pytest.raises(ValueError, match="without coal units"):
        build.coal_monthly_marginal_costs(audit, snapshots, charges_file(tmp_path))


def test_committed_charges_match_the_filing():
    committed = pd.read_csv(prep.OUTPUT)
    rebuilt = prep.energy_charges(prep.read_bills())

    pd.testing.assert_series_equal(
        committed["energy_charge_inr_per_mwh"], rebuilt["energy_charge_inr_per_mwh"], check_dtype=False
    )
