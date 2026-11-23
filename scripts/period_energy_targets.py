"""Energy targets matched to the exact period being solved.

Observed FY2025-26 energy targets are applied as Linopy constraints over the
solved snapshots, never as PyPSA GlobalConstraints. A GlobalConstraint has one
fixed constant, so an annual value cannot follow a daily, weekly or rolling
horizon.
"""
import pandas as pd
import pypsa
import xarray as xr

from bio_power_targets import bio_power_energy_target_mwh
from energy_target_policy import target_enabled
from nuclear_energy_targets import generator_dimension
from small_hydro_targets import small_hydro_energy_target_mwh

# Names used by model builds before the targets moved to the run scripts.
LEGACY_NATIVE_ENERGY_TARGETS = (
    "observed_fy2025_26_oil_gas_generation_target",
    "observed_fy2025_26_bio_power_generation_target",
    "observed_fy2025_26_small_hydro_generation_target",
    "observed_fy2025_26_nuclear_generation_target",
)


def drop_native_energy_targets(network: pypsa.Network) -> None:
    """Remove annual energy GlobalConstraints left by an older model build."""
    present = network.global_constraints.index.intersection(LEGACY_NATIVE_ENERGY_TARGETS)
    if len(present):
        network.remove("GlobalConstraint", present)


def prorated_monthly_targets_mwh(
    snapshots: pd.DatetimeIndex, snapshot_weights: pd.Series,
) -> dict[str, float]:
    """Bio-power and small-hydro targets for these snapshots.

    Each monthly total is spread uniformly over the hours of its month.
    """
    return {
        "bio_power": bio_power_energy_target_mwh(snapshots, snapshot_weights),
        "small_hydro": small_hydro_energy_target_mwh(snapshots, snapshot_weights),
    }


def add_prorated_monthly_targets(
    network: pypsa.Network, snapshots: pd.DatetimeIndex, name_suffix: str,
) -> dict[str, float]:
    """Add bio-power and small-hydro energy equalities over ``snapshots``.

    Returns the observed targets of both carriers; a carrier whose target is
    released by the energy-target policy is reported but not constrained.
    """
    snapshots = pd.DatetimeIndex(snapshots)
    weights = network.snapshot_weightings.generators
    targets = prorated_monthly_targets_mwh(snapshots, weights)
    dispatch = network.model["Generator-p"]
    generator_dim = generator_dimension(dispatch)
    hour_weights = xr.DataArray(
        weights.reindex(snapshots), dims="snapshot", coords={"snapshot": snapshots}
    )
    for carrier, target in targets.items():
        if not target_enabled(network, carrier):
            continue
        units = network.generators.index[network.generators.carrier.eq(carrier)]
        if not len(units):
            raise ValueError(f"A {carrier} energy target requires {carrier} generators")
        energy = (dispatch.sel({generator_dim: units, "snapshot": snapshots}) * hour_weights).sum()
        network.model.add_constraints(
            energy == float(target), name=f"PeriodEnergyTarget-{carrier}-{name_suffix}"
        )
    return targets


def period_target_deviation_mwh(
    network: pypsa.Network, snapshots: pd.DatetimeIndex, targets: dict[str, float],
) -> dict[str, float]:
    """Solved energy minus target, per carrier, over ``snapshots``."""
    snapshots = pd.DatetimeIndex(snapshots)
    weights = network.snapshot_weightings.generators.reindex(snapshots)
    deviation = {}
    for carrier, target in targets.items():
        units = network.generators.index[network.generators.carrier.eq(carrier)]
        energy = network.generators_t.p.loc[snapshots, units].sum(axis=1).mul(weights).sum()
        deviation[carrier] = float(energy) - float(target)
    return deviation
