"""Load, merge and validate YAML run configurations for ``run_model.py``.

A run config (for example ``config/runs/rolling_relaxed.yaml``) is deep-merged
on top of ``config/defaults.yaml``. Command-line ``--set section.key=value``
overrides are applied last. Unknown sections or keys are rejected so a typo
fails before the network is loaded.
"""

from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from scenario_technologies import (
    TechnologyError,
    needs_capacity_expansion,
    validate_section,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS_PATH = ROOT / "config" / "defaults.yaml"

RUN_MODES = {"daily", "monthly", "peak", "selected_weeks", "annual", "rolling"}
UNIT_COMMITMENT_MODES = {"full", "relaxed", "none"}
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
_UNLIMITED = {"none", "null", "unlimited"}


class ConfigError(ValueError):
    """Raised when a run configuration is malformed."""


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except yaml.YAMLError as error:
        raise ConfigError(f"{path}: invalid YAML: {error}") from error
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping of sections")
    return data


def _merge(base: dict[str, Any], override: Mapping[str, Any], source: str) -> dict[str, Any]:
    """Deep-merge ``override`` into a copy of ``base``; reject unknown keys."""
    merged = copy.deepcopy(base)
    for section, values in override.items():
        if section not in merged:
            raise ConfigError(
                f"{source}: unknown section '{section}'. Known sections: {sorted(merged)}"
            )
        if values is None:
            continue
        if not isinstance(values, Mapping):
            raise ConfigError(f"{source}: section '{section}' must be a mapping")
        for key, value in values.items():
            if key not in merged[section]:
                raise ConfigError(
                    f"{source}: unknown key '{section}.{key}'. "
                    f"Known keys: {sorted(merged[section])}"
                )
            merged[section][key] = value
    return merged


def parse_override(text: str) -> dict[str, dict[str, Any]]:
    """Turn ``section.key=value`` into a nested mapping; the value is parsed as YAML."""
    if "=" not in text:
        raise ConfigError(f"Override '{text}' must look like section.key=value")
    dotted, raw_value = text.split("=", 1)
    parts = dotted.strip().split(".")
    if len(parts) != 2 or not all(parts):
        raise ConfigError(f"Override '{text}' must name exactly section.key")
    try:
        value = yaml.safe_load(raw_value) if raw_value.strip() else None
    except yaml.YAMLError as error:
        raise ConfigError(f"Override '{text}': invalid value: {error}") from error
    return {parts[0]: {parts[1]: value}}


def _as_date_string(value: Any, label: str) -> str:
    # YAML reads an unquoted 2025-07-11 as a date object; accept both forms.
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value.strip()).isoformat()
        except ValueError:
            pass
    raise ConfigError(f"{label} must be a YYYY-MM-DD date, got {value!r}")


def _default_or_int(value: Any, label: str) -> int | str:
    if isinstance(value, str) and value.strip().casefold() == "default":
        return "Default"
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{label} must be 'Default' or an integer, got {value!r}")
    return value


def _number(value: Any, label: str, *, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{label} must be a number, got {value!r}")
    return float(value)


def _validate(config: dict[str, Any]) -> dict[str, Any]:
    run = config["run"]
    if not run.get("name"):
        raise ConfigError("run.name is required: set a distinct output folder name")
    run["name"] = str(run["name"])
    mode = str(run["mode"]).strip().lower()
    if mode not in RUN_MODES:
        raise ConfigError(f"run.mode must be one of {sorted(RUN_MODES)}, got {run['mode']!r}")
    run["mode"] = mode
    run["reference_date"] = _as_date_string(run["reference_date"], "run.reference_date")
    if not isinstance(run["resume"], bool):
        raise ConfigError("run.resume must be true or false")

    weeks = config["selected_weeks"]
    starts = weeks["week_starts"]
    if isinstance(starts, (str, dt.date)):
        starts = [starts]
    if not isinstance(starts, list) or (mode == "selected_weeks" and not starts):
        raise ConfigError("selected_weeks.week_starts must be a list of YYYY-MM-DD dates")
    weeks["week_starts"] = [
        _as_date_string(value, "selected_weeks.week_starts entry") for value in starts
    ]
    for key in ("warmup_days", "lookahead_days"):
        value = weeks[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError(f"selected_weeks.{key} must be a non-negative integer")
    fraction = _number(
        weeks["reservoir_initial_soc_fraction"], "selected_weeks.reservoir_initial_soc_fraction"
    )
    if not 0.0 <= fraction <= 1.0:
        raise ConfigError("selected_weeks.reservoir_initial_soc_fraction must be within 0..1")
    weeks["reservoir_initial_soc_fraction"] = fraction
    weeks["soc_boundary_tolerance_pu"] = _number(
        weeks["soc_boundary_tolerance_pu"], "selected_weeks.soc_boundary_tolerance_pu"
    )

    formulation = config["formulation"]
    unit_commitment = str(formulation["unit_commitment"]).strip().lower()
    if unit_commitment not in UNIT_COMMITMENT_MODES:
        raise ConfigError(
            f"formulation.unit_commitment must be one of {sorted(UNIT_COMMITMENT_MODES)}"
        )
    if mode == "annual" and unit_commitment != "none":
        raise ConfigError("run.mode 'annual' requires formulation.unit_commitment: none")
    formulation["unit_commitment"] = unit_commitment

    solver = config["solver"]
    solver["name"] = str(solver["name"])
    solver["threads"] = _default_or_int(solver["threads"], "solver.threads")
    solver["parallel_workers"] = _default_or_int(
        solver["parallel_workers"], "solver.parallel_workers"
    )
    solver["mip_rel_gap"] = _number(solver["mip_rel_gap"], "solver.mip_rel_gap")
    if solver["simplex_strategy"] is not None:
        if isinstance(solver["simplex_strategy"], bool) or not isinstance(
            solver["simplex_strategy"], int
        ):
            raise ConfigError("solver.simplex_strategy must be null or an integer")

    rolling = config["rolling"]
    limit = rolling["time_limit"]
    if isinstance(limit, str):
        folded = limit.strip().casefold()
        if folded == "default":
            limit = "Default"
        elif folded in _UNLIMITED:
            limit = None
        else:
            raise ConfigError("rolling.time_limit must be 'Default', seconds, or null")
    elif limit is not None:
        limit = _number(limit, "rolling.time_limit")
        if limit <= 0:
            raise ConfigError("rolling.time_limit must be positive")
    rolling["time_limit"] = limit
    rolling["max_time_limit_mip_gap"] = _number(
        rolling["max_time_limit_mip_gap"], "rolling.max_time_limit_mip_gap"
    )
    max_windows = rolling["max_windows"]
    if max_windows is not None and (
        isinstance(max_windows, bool) or not isinstance(max_windows, int) or max_windows < 1
    ):
        raise ConfigError("rolling.max_windows must be null or a positive integer")

    paths = config["paths"]
    for key in ("network", "results_root"):
        paths[key] = str(paths[key])

    logging_settings = config["logging"]
    level = str(logging_settings["level"]).strip().upper()
    if level not in LOG_LEVELS:
        raise ConfigError(f"logging.level must be one of {list(LOG_LEVELS)}, got {logging_settings['level']!r}")
    logging_settings["level"] = level
    interval = _number(logging_settings["sample_interval"], "logging.sample_interval")
    if interval <= 0:
        raise ConfigError("logging.sample_interval must be a positive number of seconds")
    logging_settings["sample_interval"] = interval

    for section, key in (
        ("logging", "profile"),
        ("logging", "console_log"),
        ("analysis", "enabled"),
        ("analysis", "compare_observed"),
    ):
        if not isinstance(config[section][key], bool):
            raise ConfigError(f"{section}.{key} must be true or false")

    technologies = config["technologies"]
    try:
        validate_section(technologies)
        expansion = needs_capacity_expansion(technologies)
    except TechnologyError as error:
        raise ConfigError(str(error)) from error
    if expansion and mode != "annual":
        raise ConfigError(
            "Sites with capacity_mw: optimise need run.mode: annual (one continuous "
            "LP over the year); other modes solve periods separately. Run the "
            "capacity expansion first and pass its result as "
            "technologies.fixed_capacities, or use the Snakemake workflow, which "
            "does both."
        )
    return config


def load_run_config(
    path: str | Path | None,
    overrides: Iterable[str] = (),
    defaults_path: str | Path = DEFAULTS_PATH,
) -> dict[str, Any]:
    """Return the validated, fully merged configuration as a nested dict."""
    defaults = _read_yaml(Path(defaults_path))
    config = copy.deepcopy(defaults)
    if path is not None:
        config_path = Path(path)
        if not config_path.exists():
            raise ConfigError(f"Config file does not exist: {config_path}")
        config = _merge(config, _read_yaml(config_path), str(config_path))
    for text in overrides:
        config = _merge(config, parse_override(text), f"--set {text}")
    return _validate(config)


def resolve_path(value: str | Path) -> Path:
    """Resolve a config path relative to the repository root."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else (ROOT / path).resolve()


def run_selection_kwargs(config: Mapping[str, Any]) -> dict[str, Any]:
    """Map a validated config onto ``run_9ba_selection.run_selection`` arguments."""
    run = config["run"]
    weeks = config["selected_weeks"]
    solver = config["solver"]
    rolling = config["rolling"]
    return {
        "run_mode": run["mode"],
        "reference_date": run["reference_date"],
        "week_starts": list(weeks["week_starts"]),
        "warmup_days": weeks["warmup_days"],
        "lookahead_days": weeks["lookahead_days"],
        "reservoir_initial_soc_fraction": weeks["reservoir_initial_soc_fraction"],
        "soc_boundary_tolerance_pu": weeks["soc_boundary_tolerance_pu"],
        "solver_name": solver["name"],
        "mip_rel_gap": solver["mip_rel_gap"],
        "solver_threads": solver["threads"],
        "simplex_strategy": solver["simplex_strategy"],
        "parallel_workers": solver["parallel_workers"],
        "unit_commitment": config["formulation"]["unit_commitment"],
        "run_name": run["name"],
        "rolling_time_limit": rolling["time_limit"],
        "rolling_max_time_limit_mip_gap": rolling["max_time_limit_mip_gap"],
        "network_path": resolve_path(config["paths"]["network"]),
        "results_root": resolve_path(config["paths"]["results_root"]),
        "resume": run["resume"],
        "max_windows": rolling["max_windows"],
        "profile": config["logging"]["profile"],
        "profile_log_level": config["logging"]["level"],
        "profile_sample_interval": config["logging"]["sample_interval"],
        "technologies": config["technologies"],
    }


def dump_config(config: Mapping[str, Any]) -> str:
    """Serialize a resolved config for provenance (stable key order)."""
    return yaml.safe_dump(dict(config), sort_keys=False, default_flow_style=False)
