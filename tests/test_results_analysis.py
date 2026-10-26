from pathlib import Path

import pandas as pd

from scripts.results_analysis import loaders
from scripts.results_analysis.metrics import seasonality_fit


def test_primal_network_files_excludes_dual_sidecars():
    primal = Path("window_001_2025-04-01.nc")
    duals = Path("window_001_2025-04-01.duals.nc")

    assert loaders._primal_network_files([duals, primal]) == [primal]


def test_seasonality_fit_skips_carrier_without_overlapping_values():
    months = pd.date_range("2025-04-01", periods=2, freq="MS")
    monthly = pd.DataFrame(
        {
            "wind_modeled_cf": [float("nan"), float("nan")],
            "wind_observed_cf": [0.2, 0.3],
        },
        index=months,
    )

    result = seasonality_fit(monthly)

    assert result.empty
