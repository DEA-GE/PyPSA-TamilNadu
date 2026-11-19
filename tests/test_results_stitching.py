"""Stitching rolling checkpoints into one network."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from results_analysis.loaders import AnalysisRun, load_analysis_run  # noqa: E402
from results_analysis.stitching import (  # noqa: E402
    STITCHED_FILENAME,
    load_saved_stitched,
    save_stitched,
    stitch_networks,
)
from results_analysis.validation import boundary_handoff_metrics  # noqa: E402
from test_results_statistics import _window  # noqa: E402


def _windows():
    first = _window("2025-04-01 00:00", coal=60.0)
    second = _window("2025-04-01 02:00", coal=40.0)
    return first, second


def test_stitch_joins_time_series_and_records_windows():
    first, second = _windows()
    first.add("GlobalConstraint", "weekly_target", sense="==", constant=1.0)
    first._objective = 123.0

    stitched = stitch_networks([second, first])

    assert list(stitched.snapshots) == list(pd.date_range("2025-04-01", periods=4, freq="h"))
    assert stitched.snapshots.name == "snapshot"
    assert stitched.generators_t.p["coal_1"].tolist() == [60.0, 60.0, 40.0, 40.0]
    assert stitched.global_constraints.empty
    assert stitched.objective is None
    windows = stitched.meta["stitched"]["windows"]
    assert [window["start"] for window in windows] == [
        "2025-04-01T00:00:00", "2025-04-01T02:00:00"
    ]


def test_missing_time_series_takes_the_checkpoint_static_value():
    first, second = _windows()
    first.generators_t.p_max_pu = pd.DataFrame(
        {"solar_1": [0.4, 0.2]}, index=first.snapshots
    ).rename_axis(columns="name")
    second.generators.loc["solar_1", "p_max_pu"] = 0.7

    stitched = stitch_networks([first, second])

    assert stitched.generators_t.p_max_pu["solar_1"].tolist() == [0.4, 0.2, 0.7, 0.7]


def test_checkpoints_with_different_components_are_refused():
    first, second = _windows()
    second.add("Generator", "extra", bus="Erode", carrier="coal", p_nom=1.0)

    with pytest.raises(ValueError, match="cannot be stitched"):
        stitch_networks([first, second])


def test_boundary_audit_uses_recorded_initial_storage():
    first, second = _windows()
    second.storage_units.loc["psp", "state_of_charge_initial"] = 25.0  # first ends at 25
    run = AnalysisRun(Path("."), "rolling", stitch_networks([first, second]), (), {})

    audit = boundary_handoff_metrics(run)

    assert len(audit) == 1
    assert audit.at[0, "boundary_timestamp"] == pd.Timestamp("2025-04-01 02:00")
    assert audit.at[0, "max_storage_initial_soc_error_mwh"] == pytest.approx(0.0)

    second.storage_units.loc["psp", "state_of_charge_initial"] = 20.0
    run = AnalysisRun(Path("."), "rolling", stitch_networks([first, second]), (), {})
    assert boundary_handoff_metrics(run).at[0, "max_storage_initial_soc_error_mwh"] == pytest.approx(5.0)


def test_saved_stitched_network_is_reused_until_a_checkpoint_changes(tmp_path, capsys):
    checkpoints = tmp_path / "weekly_networks"
    checkpoints.mkdir()
    paths = []
    for number, network in enumerate(_windows(), start=1):
        path = checkpoints / f"window_{number:03d}.nc"
        network.export_to_netcdf(path)
        paths.append(path)

    run = load_analysis_run(tmp_path)
    assert len(run.snapshots) == 4
    assert (tmp_path / STITCHED_FILENAME).exists()
    assert "Saved the stitched run" in capsys.readouterr().out

    reloaded = load_analysis_run(tmp_path)
    assert "Saved the stitched run" not in capsys.readouterr().out
    assert reloaded.network.generators_t.p["coal_1"].tolist() == [60.0, 60.0, 40.0, 40.0]
    assert len(reloaded.windows) == 2

    status = paths[1].stat()
    os.utime(paths[1], ns=(status.st_atime_ns, status.st_mtime_ns + 1_000_000_000))
    assert load_saved_stitched(tmp_path, paths) is None
    load_analysis_run(tmp_path)
    assert "Saved the stitched run" in capsys.readouterr().out


def test_save_stitched_writes_atomically(tmp_path):
    stitched = stitch_networks(list(_windows()))

    path = save_stitched(stitched, tmp_path)

    assert path.name == STITCHED_FILENAME
    assert not list(tmp_path.glob("*.tmp"))


def test_styled_stitched_network_works_with_pypsa_plots():
    import matplotlib

    matplotlib.use("Agg")
    from results_analysis.loaders import style_carriers

    stitched = stitch_networks(list(_windows()))
    style_carriers(stitched)

    assert stitched.carriers.color.str.startswith("#").all()
    assert stitched.carriers.at["AC", "nice_name"] == "AC transmission"
    figure, _, _ = stitched.statistics.energy_balance.plot.area(bus_carrier="AC")
    assert figure is not None
