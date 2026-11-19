"""Scenario workflow for the Tamil Nadu nine-balancing-area model.

Every config in config/scenarios/ is a scenario (see config/workflow.yaml):

    snakemake --cores 8                                      all scenarios
    snakemake --cores 8 --config scenarios=[baseline,battery]
    snakemake --cores 8 -n                                   show what would run

For each scenario:

1. capacity_expansion (only if a site has capacity_mw: optimise): one annual
   LP without unit commitment chooses those capacities, which are saved to
   <results_dir>/<scenario>/optimised_capacities.csv;
2. operation: the scenario's own run (mode, unit commitment, ...), with the
   optimised capacities fixed, writes its results and report under
   <results_dir>/<scenario>/;
3. compare: headline tables of all scenarios side by side in
   <results_dir>/comparison/;
4. effects: for every scenario other than the baseline, what its added
   technologies change against the baseline (displacement, costs, CO2,
   congestion), in <results_dir>/effects/<scenario>/effects_report.html.

The base network is model/ as built by scripts/build_9ba_model.py; rebuild it
there when its inputs change. Each run records where it wrote its results in
<results_dir>/<scenario>/<stage>.yaml.
"""

import subprocess
import sys
from pathlib import Path

import yaml

configfile: "config/workflow.yaml"

ROOT = Path(workflow.basedir)
sys.path.insert(0, str(ROOT / "scripts"))

from scenario_technologies import has_optimised_sites, load_parameters  # noqa: E402

SCENARIO_DIR = Path(config["scenario_dir"])
RESULTS = Path(config["results_dir"])
OVERRIDES = list(config.get("overrides") or [])
EXPANSION = config.get("capacity_expansion") or {}
THREADS = int(config.get("threads_per_run", 8))
BASELINE = str(config.get("baseline_scenario", "baseline"))


def _scenario_names():
    available = sorted(
        path.stem for path in SCENARIO_DIR.glob("*.yaml") if not path.name.startswith("_")
    )
    requested = config.get("scenarios") or available
    if isinstance(requested, str):
        requested = [requested]
    missing = sorted(set(requested) - set(available))
    if missing:
        raise ValueError(f"No scenario config for {missing} in {SCENARIO_DIR}; available: {available}")
    return list(requested)


SCENARIOS = _scenario_names()
# Scenarios compared with the baseline (only when the baseline runs too).
EFFECT_SCENARIOS = [name for name in SCENARIOS if name != BASELINE] if BASELINE in SCENARIOS else []


def _scenario_file(name):
    return SCENARIO_DIR / f"{name}.yaml"


def _technologies(name):
    """The scenario's technologies section on top of the defaults."""
    raw = yaml.safe_load(_scenario_file(name).read_text(encoding="utf-8")) or {}
    defaults = yaml.safe_load((ROOT / "config" / "defaults.yaml").read_text(encoding="utf-8"))
    return {**defaults["technologies"], **(raw.get("technologies") or {})}


def optimises(name):
    return has_optimised_sites(_technologies(name))


def scenario_inputs(wildcards):
    """Files a scenario's runs depend on, so edits trigger a rerun."""
    technologies = _technologies(wildcards.scenario)
    files = [_scenario_file(wildcards.scenario), "config/defaults.yaml"]
    if technologies.get("data"):
        files.append(technologies["data"])
    profiles = (load_parameters(technologies).get("offshore_wind") or {}).get("profiles")
    if profiles and Path(profiles).exists():
        files.append(profiles)
    files += sorted(str(path) for path in Path("model").glob("*.csv"))
    return files


def run_model(scenario_file, overrides, summary):
    command = [sys.executable, "scripts/run_model.py", str(scenario_file), "--summary", str(summary)]
    for item in overrides:
        command += ["--set", item]
    subprocess.run(command, check=True)


rule all:
    input:
        expand(str(RESULTS / "{scenario}" / "operation.yaml"), scenario=SCENARIOS),
        RESULTS / "comparison" / "system_metrics.csv",
        expand(str(RESULTS / "effects" / "{scenario}" / "effects_report.html"), scenario=EFFECT_SCENARIOS),


rule capacity_expansion:
    input:
        scenario_inputs,
    output:
        summary=RESULTS / "{scenario}" / "capacity_expansion.yaml",
        capacities=RESULTS / "{scenario}" / "optimised_capacities.csv",
    params:
        overrides=lambda wildcards: [
            "run.name=capacity_expansion",
            "run.mode=annual",
            "formulation.unit_commitment=none",
            f"paths.results_root={(RESULTS / wildcards.scenario).as_posix()}",
            f"analysis.enabled={'true' if EXPANSION.get('analysis') else 'false'}",
            *(EXPANSION.get("overrides") or []),
        ],
    threads: THREADS
    run:
        run_model(_scenario_file(wildcards.scenario), params.overrides, output.summary)
        subprocess.run(
            [sys.executable, "scripts/workflow/extract_capacities.py", output.summary, output.capacities],
            check=True,
        )


rule operation:
    input:
        files=scenario_inputs,
        capacities=lambda wildcards: (
            [RESULTS / wildcards.scenario / "optimised_capacities.csv"]
            if optimises(wildcards.scenario)
            else []
        ),
    output:
        summary=RESULTS / "{scenario}" / "operation.yaml",
    params:
        overrides=lambda wildcards: [
            "run.name=operation",
            f"paths.results_root={(RESULTS / wildcards.scenario).as_posix()}",
            *OVERRIDES,
        ],
    threads: THREADS
    run:
        overrides = list(params.overrides)
        if input.capacities:
            overrides.insert(0, f"technologies.fixed_capacities={Path(input.capacities[0]).as_posix()}")
        run_model(_scenario_file(wildcards.scenario), overrides, output.summary)


rule compare:
    input:
        expand(str(RESULTS / "{scenario}" / "operation.yaml"), scenario=SCENARIOS),
    output:
        RESULTS / "comparison" / "system_metrics.csv",
    run:
        runs = [f"{name}={path}" for name, path in zip(SCENARIOS, input)]
        subprocess.run(
            [sys.executable, "scripts/workflow/compare_scenarios.py", str(RESULTS / "comparison"), *runs],
            check=True,
        )


rule effects:
    input:
        baseline=RESULTS / BASELINE / "operation.yaml",
        scenario=RESULTS / "{scenario}" / "operation.yaml",
    output:
        RESULTS / "effects" / "{scenario}" / "effects_report.html",
    wildcard_constraints:
        scenario=rf"(?!{BASELINE}$)[^/\\]+",
    run:
        subprocess.run(
            [
                sys.executable, "scripts/workflow/scenario_effects.py",
                input.baseline, input.scenario, str(Path(output[0]).parent),
                "--name", wildcards.scenario,
            ],
            check=True,
        )
