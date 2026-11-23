"""Stack ordering and the daily supply-mix figure in the results report."""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from results_analysis.config import CARRIER_LABELS  # noqa: E402
from results_analysis.plots import (  # noqa: E402
    daily_generation_share_figure,
    daily_supply_shares,
    energy_balance_figure,
    order_by_variability,
)


def _balance(days: int = 3) -> pd.DataFrame:
    index = pd.date_range("2025-04-01", periods=days * 24, freq="h")
    hour = np.arange(len(index)) % 24
    solar = np.clip(np.sin((hour - 6) / 12 * np.pi), 0.0, None) * 500.0
    coal = 1_000.0 + 200.0 * np.arange(len(index)) / len(index)
    frame = pd.DataFrame(
        {
            "coal": coal,
            "nuclear": 300.0,
            "bio_power": 20.0 + np.arange(len(index)) % 2,
            "solar": solar,
        },
        index=index,
    )
    frame["demand"] = -frame.sum(axis=1)
    return frame


def test_order_puts_small_and_flat_carriers_at_the_bottom():
    ordered = order_by_variability(_balance().drop(columns="demand"))
    assert list(ordered.columns) == ["nuclear", "bio_power", "coal", "solar"]


def test_order_keeps_input_order_for_ties():
    frame = pd.DataFrame({"b": [1.0, 1.0], "a": [2.0, 2.0]})
    assert list(order_by_variability(frame).columns) == ["b", "a"]


def test_daily_supply_shares_sum_to_100_and_exclude_demand():
    shares = daily_supply_shares(_balance())
    assert len(shares) == 3
    assert "demand" not in shares
    np.testing.assert_allclose(shares.sum(axis=1), 100.0)
    first_day = _balance().drop(columns="demand").iloc[:24].sum()
    assert shares.iloc[0]["nuclear"] == pytest.approx(300.0 * 24 / first_day.sum() * 100.0)


def test_daily_share_figure_stacks_to_100_percent():
    figure = daily_generation_share_figure(_balance())
    axis = figure.axes[0]
    tops = {}
    for patch in axis.patches:
        tops[round(patch.get_x(), 6)] = max(
            tops.get(round(patch.get_x(), 6), 0.0), patch.get_y() + patch.get_height()
        )
    assert len(tops) == 3
    np.testing.assert_allclose(list(tops.values()), 100.0)
    labels = [text.get_text() for text in axis.get_legend().get_texts()]
    # Solar's daily share is the same every day here; coal's grows, so it
    # is the top layer, and the legend lists layers top-down.
    stacked = order_by_variability(daily_supply_shares(_balance())).columns
    # The last entry is the unserved-energy line on the right axis.
    assert labels[:-1] == [CARRIER_LABELS[carrier] for carrier in stacked[::-1]]
    assert labels[0] == "Coal"


def _with_unserved() -> pd.DataFrame:
    balance = _balance()
    unserved = np.zeros(len(balance))
    unserved[30:34] = 50.0  # four hours on the second day
    balance.insert(4, "unserved_energy", unserved)
    balance["demand"] -= unserved
    return balance


def test_unserved_energy_is_left_out_of_the_daily_mix():
    shares = daily_supply_shares(_with_unserved())
    assert "unserved_energy" not in shares
    np.testing.assert_allclose(shares.sum(axis=1), 100.0)


@pytest.mark.parametrize(
    "build, expected_peak",
    # Runs up to a month are drawn hourly; the daily mix uses daily means.
    [(energy_balance_figure, 50.0), (daily_generation_share_figure, 50.0 * 4 / 24)],
)
def test_unserved_energy_is_a_dotted_line_on_the_right_axis(build, expected_peak):
    figure = build(_with_unserved())
    primary, secondary = figure.axes
    unserved_label = f"{CARRIER_LABELS['unserved_energy']} (right axis)"
    assert CARRIER_LABELS["unserved_energy"] not in [
        text.get_text() for text in primary.get_legend().get_texts()
    ]
    (line,) = secondary.get_lines()
    assert line.get_label() == unserved_label
    assert line.get_linestyle() == ":"
    assert max(line.get_ydata()) == pytest.approx(expected_peak)
    assert unserved_label in [text.get_text() for text in primary.get_legend().get_texts()]
