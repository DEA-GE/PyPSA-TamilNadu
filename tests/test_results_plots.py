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
    assert labels == [CARRIER_LABELS[carrier] for carrier in stacked[::-1]]
    assert labels[0] == "Coal"

