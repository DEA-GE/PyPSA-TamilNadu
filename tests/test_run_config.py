from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_config  # noqa: E402
from run_config import ConfigError, load_run_config, run_selection_kwargs  # noqa: E402


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("path", sorted((ROOT / "config" / "runs").glob("*.yaml")))
def test_shipped_run_configs_are_valid(path):
    config = load_run_config(path)
    kwargs = run_selection_kwargs(config)
    assert kwargs["run_name"] == config["run"]["name"]
    assert kwargs["network_path"].is_absolute()


def test_run_config_overrides_only_given_keys(tmp_path):
    path = _write(
        tmp_path / "run.yaml",
        "run:\n  name: demo\n  mode: rolling\nformulation:\n  unit_commitment: full\n",
    )
    config = load_run_config(path)
    assert config["run"]["mode"] == "rolling"
    assert config["formulation"]["unit_commitment"] == "full"
    # Untouched values come from config/defaults.yaml.
    assert config["solver"]["threads"] == "Default"
    assert config["analysis"]["enabled"] is True


def test_unquoted_yaml_dates_are_accepted(tmp_path):
    path = _write(
        tmp_path / "run.yaml",
        "run:\n  name: demo\n  reference_date: 2025-08-01\n"
        "selected_weeks:\n  week_starts: [2025-04-07]\n",
    )
    config = load_run_config(path)
    assert config["run"]["reference_date"] == "2025-08-01"
    assert config["selected_weeks"]["week_starts"] == ["2025-04-07"]


@pytest.mark.parametrize(
    "text, message",
    [
        ("run:\n  name: demo\nsolver:\n  thread: 4\n", "unknown key 'solver.thread'"),
        ("run:\n  name: demo\nsolvers: {}\n", "unknown section 'solvers'"),
        ("run:\n  mode: daily\n", "run.name is required"),
        ("run:\n  name: demo\n  mode: weekly\n", "run.mode must be one of"),
        (
            "run:\n  name: demo\n  mode: annual\nformulation:\n  unit_commitment: relaxed\n",
            "requires formulation.unit_commitment: none",
        ),
        ("run:\n  name: demo\nsolver:\n  threads: many\n", "solver.threads"),
        ("run:\n  name: demo\n  reference_date: 11/07/2025\n", "YYYY-MM-DD"),
    ],
)
def test_invalid_configs_are_rejected(tmp_path, text, message):
    path = _write(tmp_path / "run.yaml", text)
    with pytest.raises(ConfigError, match=message):
        load_run_config(path)


def test_command_line_overrides_are_parsed_as_yaml(tmp_path):
    path = _write(tmp_path / "run.yaml", "run:\n  name: demo\n  mode: rolling\n")
    config = load_run_config(
        path,
        [
            "run.name=other",
            "solver.threads=4",
            "rolling.time_limit=null",
            "rolling.max_windows=2",
            "run.resume=false",
        ],
    )
    kwargs = run_selection_kwargs(config)
    assert kwargs["run_name"] == "other"
    assert kwargs["solver_threads"] == 4
    assert kwargs["rolling_time_limit"] is None
    assert kwargs["max_windows"] == 2
    assert kwargs["resume"] is False


@pytest.mark.parametrize("value", ["None", "unlimited"])
def test_unlimited_rolling_time_limit_spellings(tmp_path, value):
    path = _write(tmp_path / "run.yaml", f"run:\n  name: demo\nrolling:\n  time_limit: {value}\n")
    assert load_run_config(path)["rolling"]["time_limit"] is None


def test_logging_settings_map_to_profiler_arguments(tmp_path):
    path = _write(
        tmp_path / "run.yaml",
        "run:\n  name: demo\nlogging:\n  profile: false\n  level: debug\n  sample_interval: 2\n",
    )
    kwargs = run_selection_kwargs(load_run_config(path))
    assert kwargs["profile"] is False
    assert kwargs["profile_log_level"] == "DEBUG"
    assert kwargs["profile_sample_interval"] == 2.0


def test_profiling_is_on_by_default():
    kwargs = run_selection_kwargs(load_run_config(ROOT / "config" / "runs" / "daily_demo.yaml"))
    assert kwargs["profile"] is True
    assert kwargs["profile_log_level"] == "INFO"


@pytest.mark.parametrize(
    "text, message",
    [
        ("logging:\n  level: verbose\n", "logging.level must be one of"),
        ("logging:\n  sample_interval: 0\n", "logging.sample_interval"),
        ("logging:\n  profile: maybe\n", "logging.profile must be true or false"),
    ],
)
def test_invalid_logging_settings_are_rejected(tmp_path, text, message):
    path = _write(tmp_path / "run.yaml", "run:\n  name: demo\n" + text)
    with pytest.raises(ConfigError, match=message):
        load_run_config(path)


@pytest.mark.parametrize("flag, expected", [("--no-profile", False), ("--profile", True)])
def test_profile_flag_overrides_config(tmp_path, capsys, flag, expected):
    import run_model
    import yaml

    path = _write(
        tmp_path / "run.yaml",
        f"run:\n  name: demo\nlogging:\n  profile: {str(not expected).lower()}\n",
    )
    assert run_model.main([str(path), flag, "--dry-run"]) == 0
    assert yaml.safe_load(capsys.readouterr().out)["logging"]["profile"] is expected


def test_override_requires_section_and_key():
    with pytest.raises(ConfigError, match="section.key"):
        run_config.parse_override("threads=4")


def test_resolved_config_round_trips(tmp_path):
    original = load_run_config(ROOT / "config" / "runs" / "selected_weeks_relaxed.yaml")
    saved = _write(tmp_path / "run_config.yaml", "# header\n" + run_config.dump_config(original))
    assert load_run_config(saved) == original


def test_annual_lp_method_and_crossover_settings(tmp_path):
    from highs_solver_options import highs_solver_options

    annual = ["run.name=x", "run.mode=annual", "formulation.unit_commitment=none"]
    config = load_run_config(None, annual)
    assert config["solver"]["lp_method"] == "Default"
    assert config["solver"]["crossover"] is True
    assert run_selection_kwargs(config)["lp_method"] == "Default"

    config = load_run_config(None, [*annual, "solver.lp_method=IPM", "solver.crossover=false"])
    assert config["solver"]["lp_method"] == "ipm"
    assert run_selection_kwargs(config)["crossover"] is False

    with pytest.raises(ConfigError, match="annual only"):
        load_run_config(None, ["run.name=x", "solver.lp_method=ipm"])
    with pytest.raises(ConfigError, match="lp_method"):
        load_run_config(None, [*annual, "solver.lp_method=barrier"])
    with pytest.raises(ConfigError, match="crossover"):
        load_run_config(None, [*annual, "solver.crossover=maybe"])

    assert highs_solver_options("none", None, 8, lp_solver="ipm", crossover=False)["run_crossover"] == "off"
    assert highs_solver_options("none", None, 8, lp_solver="ipm")["run_crossover"] == "on"
    assert highs_solver_options("none", None, 3)["solver"] == "simplex"  # weekly windows unchanged
