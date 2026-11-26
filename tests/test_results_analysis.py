from pathlib import Path

from scripts.results_analysis import loaders


def test_primal_network_files_excludes_dual_sidecars():
    primal = Path("window_001_2025-04-01.nc")
    duals = Path("window_001_2025-04-01.duals.nc")

    assert loaders._primal_network_files([duals, primal]) == [primal]
