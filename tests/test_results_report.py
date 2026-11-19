import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_results_report as report  # noqa: E402


def _runner_outputs(result_dir: Path) -> None:
    result_dir.mkdir(parents=True)
    (result_dir / "validation.json").write_text(
        json.dumps(
            {
                "run_name": "monthly_test",
                "run_mode": "monthly",
                "unit_commitment": "relaxed",
                "start_date": "2025-07-01",
                "end_date": "2025-07-31",
                "all_solver_statuses_ok": True,
                "total_unserved_gwh": 0.0,
            }
        ),
        encoding="utf-8",
    )
    pd.DataFrame(
        {
            "carrier": ["coal", "solar"],
            "modeled_gwh": [100.0, 50.0],
            "observed_gwh": [110.0, 45.0],
            "difference_gwh": [-10.0, 5.0],
            "difference_pct": [-9.09, 11.1],
            "observed_source": ["NITI", "NITI"],
        }
    ).to_csv(result_dir / "generation_comparison_summary.csv", index=False)


def test_analysis_targets_finds_selected_week_networks(tmp_path):
    for week in ("week_2025-04-07", "week_2025-07-07"):
        (tmp_path / week).mkdir()
        (tmp_path / week / "solved_network.nc").touch()
    (tmp_path / "week_2025-10-13").mkdir()  # unfinished week: no network

    targets = report.analysis_targets(tmp_path)

    assert [path.name for path in targets] == ["week_2025-04-07", "week_2025-07-07"]


def test_analysis_targets_prefers_run_level_results(tmp_path):
    (tmp_path / "weekly_networks").mkdir()
    assert report.analysis_targets(tmp_path) == [tmp_path]


def test_report_without_networks_uses_runner_outputs(tmp_path):
    result_dir = tmp_path / "monthly_test"
    _runner_outputs(result_dir)

    path = report.build_report(result_dir, config_text="run:\n  name: monthly_test\n")

    text = path.read_text(encoding="utf-8")
    assert path.name == report.REPORT_FILENAME
    assert "9-BA model results: monthly_test" in text
    assert "Model (GWh)" in text and "110.00" in text
    assert "kept no solved networks" in text
    assert "run:\n  name: monthly_test" in text
    assert "This run is incomplete" not in text


def test_report_warns_when_rolling_run_is_incomplete(tmp_path):
    result_dir = tmp_path / "rolling_test"
    _runner_outputs(result_dir)
    validation = json.loads((result_dir / "validation.json").read_text(encoding="utf-8"))
    validation.update({"run_mode": "rolling", "complete": False, "windows_completed": 1, "windows_planned": 53})
    (result_dir / "validation.json").write_text(json.dumps(validation), encoding="utf-8")

    text = report.build_report(result_dir, detailed=False).read_text(encoding="utf-8")

    assert "This run is incomplete" in text
    assert "1 / 53" in text


def test_report_links_profile_logs_when_logging_was_on(tmp_path):
    result_dir = tmp_path / "profiled"
    _runner_outputs(result_dir)
    profile = result_dir / "profile"
    (profile / "highs").mkdir(parents=True)
    (profile / "highs" / "day_20250711.log").write_text("Running HiGHS", encoding="utf-8")
    (profile / "run.log").write_text("log", encoding="utf-8")
    (profile / "summary.txt").write_text("Total wall time 12 s", encoding="utf-8")
    (profile / "run_status.json").write_text('{"status": "completed"}', encoding="utf-8")

    text = report.build_report(result_dir, detailed=False).read_text(encoding="utf-8")

    assert "Run logging and profile" in text
    assert "Total wall time 12 s" in text
    assert 'href="profile/run.log"' in text
    assert "1 HiGHS solver logs" in text


def test_report_has_no_profile_section_when_logging_was_off(tmp_path):
    result_dir = tmp_path / "unprofiled"
    _runner_outputs(result_dir)
    text = report.build_report(result_dir, detailed=False).read_text(encoding="utf-8")
    assert "Run logging and profile" not in text


def test_rolling_window_summary_counts_problems():
    log = pd.DataFrame(
        {
            "window": [1, 2, 3],
            "termination_condition": ["optimal", "time_limit", "optimal"],
            "accepted_solution": [True, True, False],
            "solve_attempts": [1, 2, 1],
            "runtime_seconds": [60.0, 700.0, 80.0],
        }
    )

    summary = report.rolling_window_summary(log).set_index("Metric")["Value"]

    assert summary["Windows in log"] == "3"
    assert summary["Windows not optimal"] == "1"
    assert summary["Windows not accepted"] == "1"
    assert summary["Windows needing a retry"] == "1"
    assert summary["Slowest window"] == "2"
    assert summary["Solver time per window, max"] == "11.7 min"


def test_number_formatting_hides_solver_round_off():
    assert report._format_number(1.8e-12) == "0.00"
    assert report._format_number(0.004) == "0.004"
    assert report._format_number(15032.5) == "15,032.50"
    assert report._format_number(float("nan")) == "—"
