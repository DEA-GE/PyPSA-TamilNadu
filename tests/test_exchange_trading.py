from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pypsa
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import exchange_trading as et  # noqa: E402


SNAPSHOTS = pd.date_range("2025-04-01 00:00", periods=3, freq="h")


def toy_network(metered: bool = True) -> pypsa.Network:
    """Tamil Nadu bus with coal and demand, one gateway with fixed interstate loads."""
    n = pypsa.Network()
    n.set_snapshots(SNAPSHOTS)
    n.add("Carrier", ["coal", "electricity"])
    n.add("Bus", "TN", carrier="AC", x=78.0, y=11.0)
    n.add("Bus", "Kerala", carrier="AC", x=76.0, y=10.0)
    n.add("Generator", "coal", bus="TN", carrier="coal", p_nom=3000.0, marginal_cost=4000.0)
    n.add("Load", "demand", bus="TN", p_set=1500.0)
    n.add("Link", "Kerala-TN", bus0="Kerala", bus1="TN", p_nom=1000.0, efficiency=0.97, marginal_cost=241.81)
    n.add("Link", "TN-Kerala", bus0="TN", bus1="Kerala", p_nom=1000.0, efficiency=0.97, marginal_cost=241.81)
    name = "interstate_metered_correction_import_kerala" if metered else "interstate_resolved_import_kerala"
    n.add("Load", name, bus="Kerala", carrier="electricity", p_set=-300.0)
    return n


def write_inputs(tmp_path: Path, price: list[float], imports: list[float], exports: list[float]) -> dict:
    pd.DataFrame({"timestamp": SNAPSHOTS, "price_inr_per_mwh": price}).to_csv(tmp_path / "price.csv", index=False)
    trades = pd.DataFrame({"snapshot": SNAPSHOTS, "exchange_import_mw": imports, "exchange_export_mw": exports})
    trades["exchange_net_import_mw"] = trades.exchange_import_mw - trades.exchange_export_mw
    trades.to_csv(tmp_path / "trades.csv", index=False)
    pd.DataFrame({"direction": ["import", "export"], "gateway": ["Kerala", "Kerala"],
                  "allocation_share": [1.0, 1.0]}).to_csv(tmp_path / "shares.csv", index=False)
    return et.validate_section({
        "enabled": True, "limit": "headroom",
        "price": str(tmp_path / "price.csv"), "trades": str(tmp_path / "trades.csv"),
        "gateway_shares": str(tmp_path / "shares.csv"), "access_charge_inr_per_mwh": 241.81,
        "import_steps": [[500, 0], [1000, 500]], "export_steps": [[500, 0], [1000, 500]],
    })


def test_apply_adds_market_bus_steps_and_observed_trade_reversal(tmp_path):
    n = toy_network()
    settings = write_inputs(tmp_path, [3000, 3000, 3000], [200, 0, 100], [0, 150, 100])

    result = et.apply_exchange_trading(n, settings)

    assert "IEX" in n.buses.index
    # One-way market links carry the short-term charge, sized to both link directions.
    assert n.links.at["IEX buy via Kerala", "marginal_cost"] == 241.81
    assert n.links.at["IEX sell via Kerala", "bus0"] == "Kerala"
    assert n.links.at["IEX sell via Kerala", "p_nom"] == 2000.0
    # Contracted flows lose their per-MWh charge.
    assert n.links.loc[["Kerala-TN", "TN-Kerala"], "marginal_cost"].tolist() == [0.0, 0.0]
    # Observed trades come out of the fixed schedule at the gateway.
    assert n.loads_t.p_set[et.REVERSAL_PREFIX + "kerala"].tolist() == [200.0, -150.0, 0.0]
    cost = n.generators_t.marginal_cost
    assert cost["exchange_import_2"].tolist() == [3500.0] * 3
    assert cost["exchange_export_2"].tolist() == [2500.0] * 3
    assert n.generators.at["exchange_export_1", "p_max_pu"] == 0.0
    assert list(result.gateways.bus) == ["Kerala"]


def test_observed_limit_fills_steps_in_order_and_enlarges_the_last():
    cap = pd.Series([0.0, 700.0, 2500.0], index=SNAPSHOTS)

    availability, sizes = et.step_availability(cap, [500.0, 1000.0])

    assert sizes == [500.0, 2000.0]
    np.testing.assert_allclose(availability[0], [0.0, 1.0, 1.0])
    np.testing.assert_allclose(availability[1], [0.0, 0.1, 1.0])


def test_model_buys_when_exchange_is_cheap_and_sells_when_dear(tmp_path):
    n = toy_network()
    settings = write_inputs(tmp_path, [1000, 9000, 4000], [0, 0, 0], [0, 0, 0])
    et.apply_exchange_trading(n, settings)

    status, _ = n.optimize(solver_name="highs")

    assert status == "ok"
    imports = n.generators_t.p.filter(like="exchange_import").sum(axis=1)
    exports = n.generators_t.p.filter(like="exchange_export").sum(axis=1)
    # Hour 1: exchange at 1000 + charges is cheaper than coal at 4000 -> import up
    # to the headroom: the 1000 MW link minus the 300 MW fixed import.
    assert imports.iloc[0] == pytest.approx(700.0)
    # Hour 2: exchange at 9000 is dearer than coal -> sell over the TN-Kerala link.
    assert exports.iloc[1] < -900.0
    # Hour 3: at 4000 (coal's cost) there is no trade: a purchase costs
    # (4000 + 241.81) / 0.97 delivered, and reselling the fixed import earns
    # 4000 - 241.81 for 0.97 MWh of coal at 4000 that would replace it.
    assert imports.iloc[2] == pytest.approx(0.0, abs=1e-6)
    assert exports.iloc[2] == pytest.approx(0.0, abs=1e-6)


def test_observed_limit_caps_trades_at_observed_volumes(tmp_path):
    n = toy_network()
    settings = write_inputs(tmp_path, [1000, 9000, 1000], [300, 0, 0], [0, 200, 0])
    settings["limit"] = "observed"
    et.apply_exchange_trading(n, settings)

    n.optimize(solver_name="highs")

    imports = n.generators_t.p.filter(like="exchange_import").sum(axis=1)
    exports = n.generators_t.p.filter(like="exchange_export").sum(axis=1)
    assert imports.tolist() == pytest.approx([300.0, 0.0, 0.0], abs=1e-6)
    assert exports.tolist() == pytest.approx([0.0, -200.0, 0.0], abs=1e-6)


def test_disabled_section_changes_nothing(tmp_path):
    n = toy_network()
    settings = write_inputs(tmp_path, [1000] * 3, [0] * 3, [0] * 3)
    settings["enabled"] = False

    assert et.apply_exchange_trading(n, settings) is None
    assert "IEX" not in n.buses.index


def test_transaction_only_basis_is_rejected(tmp_path):
    n = toy_network(metered=False)
    settings = write_inputs(tmp_path, [1000] * 3, [0] * 3, [0] * 3)

    with pytest.raises(et.ExchangeTradingError, match="metered interchange basis"):
        et.apply_exchange_trading(n, settings)


def test_decreasing_adders_are_rejected():
    with pytest.raises(et.ExchangeTradingError, match="must not decrease"):
        et.validate_section({
            "enabled": True, "limit": "headroom", "price": "p.csv", "trades": "t.csv",
            "gateway_shares": "s.csv", "access_charge_inr_per_mwh": 0.0,
            "import_steps": [[500, 1000], [500, 0]], "export_steps": [[500, 0]],
        })
