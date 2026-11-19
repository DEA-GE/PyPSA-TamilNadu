import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pypsa
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from linopy_compat import configure_for_pypsa_124  # noqa: E402
from run_profiling import (  # noqa: E402
    RunProfiler,
    activate,
    format_summary,
    get_profiler,
    optimize_profiled,
    parse_highs_log,
    summarize_profile,
)

configure_for_pypsa_124()


def _network(committable: bool) -> pypsa.Network:
    network = pypsa.Network()
    network.set_snapshots(pd.date_range("2025-01-01", periods=3, freq="h"))
    network.add("Bus", "bus")
    network.add("Load", "load", bus="bus", p_set=[40.0, 90.0, 60.0])
    network.add(
        "Generator", "coal", bus="bus", p_nom=100.0, marginal_cost=10.0,
        committable=committable, p_min_pu=0.3 if committable else 0.0,
        start_up_cost=500.0 if committable else 0.0,
    )
    network.add("Generator", "peaker", bus="bus", p_nom=100.0, marginal_cost=50.0)
    return network


def _extra(network: pypsa.Network, snapshots: pd.Index) -> None:
    dispatch = network.model["Generator-p"]
    network.model.add_constraints(
        dispatch.sel(name="peaker").sum() >= 5.0, name="peaker_minimum_energy"
    )


@pytest.mark.parametrize("committable", [False, True])
def test_profiled_optimize_matches_network_optimize(tmp_path, committable):
    options = {"log_to_console": False, "threads": 1}
    reference = _network(committable)
    reference_status = reference.optimize(
        solver_name="highs", solver_options=options, extra_functionality=_extra
    )

    profiler = RunProfiler(run_label="test", output_dir=tmp_path, sample_interval=0.05)
    with activate(profiler):
        network = _network(committable)
        with profiler.phase("window"):
            status = optimize_profiled(
                network,
                solver_name="highs",
                solver_options=options,
                extra_functionality=_extra,
                tag="unit test",
                assign_all_duals=True,
            )
        record = get_profiler().last_solve
    summary = profiler.close()

    assert status == reference_status == ("ok", "optimal")
    assert np.isclose(network.objective, reference.objective)
    pd.testing.assert_frame_equal(network.generators_t.p, reference.generators_t.p)

    for key in ("build_model_s", "extra_functionality_s", "highs_run_s", "assign_s"):
        assert record[key] >= 0.0
    assert record["custom_constraint_rows"] == 1
    assert record["log_original_rows"] >= 1
    if committable:
        assert record["log_original_integer_cols"] > 0
        assert record["log_mip_timing"] >= 0.0
        assert record["log_mip_solve_s"] >= 0.0
        assert record["log_mip_lp_iterations_heuristics"] >= 0
        assert record["log_mip_first_incumbent_s"] >= 0.0
        assert record["highs_mip_node_count"] is not None
    else:
        assert record["log_run_time"] >= 0.0

    profile_dir = tmp_path / "profile"
    for name in ("run.log", "events.jsonl", "solves.jsonl", "solves.csv",
                 "resources.csv", "environment.json", "summary.json", "summary.txt",
                 "run_status.json"):
        assert (profile_dir / name).exists(), name
    assert (profile_dir / "highs" / "unit_test.log").exists()
    paths = {json.loads(line)["path"] for line in (profile_dir / "events.jsonl").read_text().splitlines()}
    assert {"window", "window/optimize", "window/optimize/build_model",
            "window/optimize/solve", "window/optimize/assign_duals"} <= paths
    assert summary["solves"]["attempts"] == 1
    assert "Optimizations: 1 attempt(s)" in format_summary(summary)


def test_profiler_is_inactive_by_default():
    profiler = get_profiler()
    assert not profiler.enabled
    with profiler.phase("anything") as record:
        record["ignored"] = True


def test_failed_phase_is_recorded(tmp_path):
    profiler = RunProfiler(run_label="test", output_dir=tmp_path)
    with activate(profiler):
        with pytest.raises(ValueError):
            with profiler.phase("broken"):
                raise ValueError("boom")
    profiler.close(status="failed", error=ValueError("boom"))
    summary = summarize_profile(tmp_path / "profile")
    assert summary["run"]["status"] == "failed"
    assert summary["phases"][0]["errors"] == 1


def test_parse_mip_log(tmp_path):
    log = tmp_path / "mip.log"
    log.write_text(
        "MIP  linopy-problem-x has 120 rows; 96 cols; 400 nonzeros; 24 integer variables (24 binary)\n"
        "Presolve reductions: rows 80(-40); columns 70(-26); nonzeros 300(-100) \n"
        "         0       0         0   0.00%   -inf            inf                  inf        0      0      0         0     0.0s\n"
        " R       0       0         0   0.00%   100.5           130.2             22.81%        0      0      0        50     1.5s\n"
        " T      12       3         4  40.00%   110.0           120.0              8.33%       10      5      0       900     4.0s\n"
        "Solving report\n"
        "  Status            Optimal\n"
        "  Primal bound      120.0\n"
        "  Dual bound        119.5\n"
        "  Gap               0.417% (tolerance: 1%)\n"
        "  Timing            4.20\n"
        "  Nodes             15\n"
        "  Timing            4.20\n"
        "                    0.30 (Presolve)\n"
        "                    3.80 (Solve)\n"
        "                    0.10 (Postsolve)\n"
        "  LP iterations     1000\n"
        "                    120 (strong br.)\n"
        "                    40 (separation)\n"
        "                    300 (heuristics)\n"
        "HiGHS run time      :          4.21\n",
        encoding="utf-8",
    )
    parsed = parse_highs_log(log)
    assert parsed["log_original_integer_cols"] == 24
    assert parsed["log_presolved_rows"] == 80
    assert parsed["log_mip_first_incumbent_s"] == 1.5
    assert parsed["log_mip_incumbent_updates"] == 2
    assert parsed["log_mip_nodes"] == 15
    assert parsed["log_mip_lp_iterations"] == 1000
    assert parsed["log_mip_timing"] == 4.20
    assert parsed["log_mip_presolve_s"] == 0.30
    assert parsed["log_mip_solve_s"] == 3.80
    assert parsed["log_mip_lp_iterations_strong_branching"] == 120
    assert parsed["log_mip_lp_iterations_heuristics"] == 300
    assert parsed["log_run_time"] == 4.21
