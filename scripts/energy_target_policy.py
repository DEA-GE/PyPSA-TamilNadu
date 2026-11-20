"""How observed FY2025-26 energy is imposed on the targeted carriers.

Oil/gas, nuclear, bio-power and small hydro were historically forced to their
observed energy with equality constraints. Each carrier now has a mode:

``target``
    The observed-energy equality constraints, as before (the default).
``availability``
    No equality constraint. Each dispatchable unit's ``p_max_pu`` is scaled,
    period by period, so that the carrier's available energy over each period
    equals the observed energy. Cost optimisation then decides how much of it
    to use, so the carrier keeps the observed seasonal pattern only when its
    marginal cost places it in the merit order.
``free``
    No equality constraint and the network's own availability.

Units on a fixed schedule (``p_min_pu`` equal to ``p_max_pu`` at every hour,
such as the REA export reservations) are never rescaled; their energy is
subtracted from the observed energy before the dispatchable units are scaled.

The policy is stored in ``network.meta["energy_targets"]`` so that every place
that adds a target, including worker processes that receive a pickled copy,
can check it with :func:`target_enabled`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import pypsa

from bio_power_targets import read_bio_power_monthly_targets
from small_hydro_targets import read_small_hydro_monthly_targets

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "model"
NUCLEAR_BUDGET_FILE = MODEL_DIR / "nuclear_daily_energy_budget.csv"
OIL_GAS_BUDGET_FILE = MODEL_DIR / "oil_gas_daily_energy_budget.csv"
PLANT_GENERATION_FILE = ROOT / "data" / "model_inputs" / "niti_iced_tamil_nadu_monthly_generation.csv"
PLANT_MAPPING_FILE = ROOT / "data" / "model_inputs" / "niti_iced_plant_mapping.csv"

TARGET_CARRIERS = ("oil_gas", "nuclear", "bio_power", "small_hydro")
TARGET_MODES = ("target", "availability", "free")
AVAILABILITY_PERIODS = {"monthly": "MS", "daily": "D"}
AVAILABILITY_SOURCES = ("plant", "carrier")
# Carriers with plant-level monthly observations (NITI ICED).
PLANT_LEVEL_CARRIERS = ("oil_gas", "nuclear")
DEFAULT_POLICY: dict[str, str] = {
    **{carrier: "target" for carrier in TARGET_CARRIERS},
    "availability_period": "monthly",
    "availability_source": "plant",
}
_ALLOWED_VALUES = {
    "availability_period": tuple(AVAILABILITY_PERIODS),
    "availability_source": AVAILABILITY_SOURCES,
}
# Carriers whose marginal cost a run config may override, in INR/MWh.
COST_OVERRIDE_CARRIERS = ("oil_gas", "diesel", "bio_power", "nuclear", "small_hydro")
META_KEY = "energy_targets"


def normalise_policy(policy: Mapping[str, Any] | None) -> dict[str, str]:
    """Return a complete, validated policy; missing keys take the defaults."""
    resolved = dict(DEFAULT_POLICY)
    for key, value in (policy or {}).items():
        if key not in resolved:
            raise ValueError(
                f"Unknown energy_targets key '{key}'. Known keys: {sorted(resolved)}"
            )
        value = str(value).strip().lower()
        allowed = _ALLOWED_VALUES.get(key, TARGET_MODES)
        if value not in allowed:
            raise ValueError(f"energy_targets.{key} must be one of {sorted(allowed)}, got {value!r}")
        resolved[key] = value
    return resolved


def normalise_cost_overrides(overrides: Mapping[str, Any] | None) -> dict[str, float]:
    """Return the non-null marginal-cost overrides as floats (INR/MWh)."""
    resolved: dict[str, float] = {}
    for carrier, value in (overrides or {}).items():
        if carrier not in COST_OVERRIDE_CARRIERS:
            raise ValueError(
                f"Unknown marginal_costs carrier '{carrier}'. "
                f"Known carriers: {list(COST_OVERRIDE_CARRIERS)}"
            )
        if value is None:
            continue
        if isinstance(value, bool):
            raise ValueError(f"marginal_costs.{carrier} must be a number or null")
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"marginal_costs.{carrier} must be a number or null") from error
        if not np.isfinite(number):
            raise ValueError(f"marginal_costs.{carrier} must be finite")
        resolved[carrier] = number
    return resolved


def is_default(policy: Mapping[str, str], overrides: Mapping[str, float]) -> bool:
    return dict(policy) == DEFAULT_POLICY and not overrides


def policy_tag(policy: Mapping[str, str], overrides: Mapping[str, float]) -> str:
    """Short stable hash of a non-default policy, for checkpoint fingerprints."""
    text = json.dumps({"policy": dict(policy), "costs": dict(overrides)}, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def target_enabled(network: pypsa.Network, carrier: str) -> bool:
    """Whether ``carrier``'s observed-energy equality constraints apply.

    Networks without a stored policy (older scripts, tests) keep every target.
    """
    policy = (network.meta or {}).get(META_KEY) or DEFAULT_POLICY
    return policy.get(carrier, "target") == "target"


def observed_energy_mwh(carrier: str, frequency: str) -> pd.Series:
    """Observed FY2025-26 energy per period start, in MWh.

    Daily budgets exist for nuclear and oil/gas. Bio-power and small hydro are
    observed monthly; at daily frequency each month is spread evenly over its days.
    """
    if carrier in {"nuclear", "oil_gas"}:
        path = NUCLEAR_BUDGET_FILE if carrier == "nuclear" else OIL_GAS_BUDGET_FILE
        daily = (
            pd.read_csv(path, parse_dates=["date"])
            .set_index("date")["daily_energy_target_mwh"]
            .astype(float)
        )
        return daily.resample(frequency).sum()
    if carrier == "bio_power":
        monthly = read_bio_power_monthly_targets()
    elif carrier == "small_hydro":
        monthly = read_small_hydro_monthly_targets()
    else:
        raise ValueError(f"No observed energy series for carrier '{carrier}'")
    monthly = monthly.astype(float)
    monthly.index = monthly.index.to_timestamp(how="start")
    if frequency == "MS":
        return monthly
    days = pd.date_range(monthly.index[0], monthly.index[-1] + pd.offsets.MonthEnd(0), freq="D")
    per_day = monthly.reindex(days.to_period("M").to_timestamp(how="start")).to_numpy()
    return pd.Series(per_day / days.days_in_month.to_numpy(), index=days)


def _per_unit_series(network: pypsa.Network, attribute: str, units: pd.Index) -> pd.DataFrame:
    """Hourly ``attribute`` (p_max_pu or p_min_pu) for ``units``, static where not dynamic."""
    static = network.generators.loc[units, attribute].astype(float)
    frame = pd.DataFrame(
        np.tile(static.to_numpy(), (len(network.snapshots), 1)),
        index=network.snapshots,
        columns=units,
    )
    dynamic = getattr(network.generators_t, attribute)
    present = dynamic.columns.intersection(units)
    if len(present):
        frame[present] = dynamic[present].astype(float)
    return frame


def fixed_schedule_units(network: pypsa.Network, units: pd.Index) -> pd.Index:
    """Units whose output is fixed at every hour (p_min_pu == p_max_pu > 0)."""
    p_max = _per_unit_series(network, "p_max_pu", units)
    p_min = _per_unit_series(network, "p_min_pu", units)
    fixed = (p_max - p_min).abs().max().le(1e-9) & p_max.max().gt(0.0)
    return units[fixed.to_numpy()]


def scale_to_energy(
    base: np.ndarray, capacity_mw: np.ndarray, weights: np.ndarray, target_mwh: float,
) -> tuple[np.ndarray, float]:
    """Scale ``base`` (hours x units, per unit) so that available energy equals the target.

    Returns ``min(1, k * base)`` for the ``k`` that meets ``target_mwh`` and the
    available energy reached; the target cannot be exceeded once every unit
    that has any availability is at 1.0, so the energy reached is then lower.
    """
    if target_mwh <= 0.0:
        return np.zeros_like(base), 0.0

    def energy(scale: float) -> float:
        return float(np.minimum(1.0, scale * base) @ capacity_mw @ weights)

    full = np.where(base > 0, 1.0, 0.0)
    ceiling = float(full @ capacity_mw @ weights)
    if ceiling <= target_mwh:
        return full, ceiling
    low, high = 0.0, 1.0
    while energy(high) < target_mwh:
        high *= 2.0
    for _ in range(100):
        middle = 0.5 * (low + high)
        if energy(middle) < target_mwh:
            low = middle
        else:
            high = middle
        if high - low <= 1e-12 * high:
            break
    scaled = np.minimum(1.0, high * base)
    return scaled, energy(high)


def plant_groups(network: pypsa.Network, carrier: str) -> dict[str, pd.Index]:
    """Model units of ``carrier`` per observed (NITI ICED) plant.

    Every unit must belong to exactly one plant; plants without model units
    (an empty ``component_regex``) are returned with an empty index.
    """
    mapping = pd.read_csv(PLANT_MAPPING_FILE, dtype=str, keep_default_na=False)
    mapping = mapping.loc[mapping["carrier"].eq(carrier)]
    if mapping.empty:
        raise ValueError(f"No plant mapping for carrier {carrier}")
    units = network.generators.index[network.generators.carrier.eq(carrier)]
    names = pd.Series(units, index=units)
    groups: dict[str, pd.Index] = {}
    assigned = pd.Series(0, index=units)
    for row in mapping.itertuples():
        if row.component_regex:
            hits = names.str.contains(row.component_regex, regex=True)
            groups[row.plant_name] = units[hits.to_numpy()]
            assigned += hits.astype(int)
        else:
            groups[row.plant_name] = pd.Index([])
    if (assigned != 1).any():
        raise ValueError(
            f"{carrier} units must map to exactly one plant: "
            f"{assigned[assigned != 1].index.tolist()}"
        )
    return groups


def plant_monthly_shares(carrier: str, plants: list[str]) -> pd.DataFrame:
    """Each plant's share of the carrier's observed monthly generation.

    Rows are month starts, columns plant names; months in which the plants
    generated nothing have all-zero shares.
    """
    source = {"oil_gas": "oil-gas", "nuclear": "nuclear"}[carrier]
    data = pd.read_csv(PLANT_GENERATION_FILE, parse_dates=["month"])
    data = data.loc[data["source"].eq(source)]
    table = data.pivot_table(
        index="month", columns="plant_name", values="generation_mu", aggfunc="sum"
    )
    missing = sorted(set(plants) - set(table.columns))
    if missing:
        raise ValueError(f"No monthly generation for mapped {carrier} plants: {missing}")
    table = table[plants]
    total = table.sum(axis=1)
    return table.div(total.where(total > 0), axis=0).fillna(0.0)


def apply_availability_profiles(
    network: pypsa.Network, policy: Mapping[str, str],
) -> pd.DataFrame:
    """Rescale ``p_max_pu`` of every carrier in ``availability`` mode.

    With ``availability_source: plant``, oil/gas and nuclear are scaled plant
    by plant: each plant receives the carrier's observed energy in the period
    times its share of the carrier's NITI ICED generation in that month.
    Bio-power and small hydro, observed only as state totals, and every carrier
    with ``availability_source: carrier`` are scaled as one group.

    Returns one row per carrier, plant and period: observed energy, scheduled
    (fixed) energy, available dispatchable energy before and after, and the
    shortfall where installed capacity cannot reach the observed energy.
    """
    frequency = AVAILABILITY_PERIODS[policy["availability_period"]]
    weights = network.snapshot_weightings.generators.reindex(network.snapshots).astype(float)
    period_level = "M" if frequency == "MS" else "D"
    periods = pd.DatetimeIndex(network.snapshots.to_period(period_level).to_timestamp(how="start"))
    months = pd.DatetimeIndex(network.snapshots.to_period("M").to_timestamp(how="start"))
    rows: list[dict[str, object]] = []
    for carrier in TARGET_CARRIERS:
        if policy[carrier] != "availability":
            continue
        units = network.generators.index[network.generators.carrier.eq(carrier)]
        if not len(units):
            raise ValueError(f"Availability profile for {carrier} needs {carrier} generators")
        observed = observed_energy_mwh(carrier, frequency)
        by_plant = policy["availability_source"] == "plant" and carrier in PLANT_LEVEL_CARRIERS
        if by_plant:
            groups = plant_groups(network, carrier)
            shares = plant_monthly_shares(carrier, list(groups))
        else:
            groups = {"": units}
            shares = None
        scaled_columns: list[pd.DataFrame] = []
        clipped_minimum: list[pd.DataFrame] = []
        for plant, members in groups.items():
            fixed = fixed_schedule_units(network, members) if len(members) else pd.Index([])
            dispatchable = members.difference(fixed)
            capacity = network.generators.loc[dispatchable, "p_nom"].astype(float).to_numpy()
            fixed_energy = (
                _per_unit_series(network, "p_max_pu", fixed)
                .mul(network.generators.loc[fixed, "p_nom"], axis=1)
                .sum(axis=1)
                .mul(weights)
            )
            base = _per_unit_series(network, "p_max_pu", dispatchable)
            p_min = _per_unit_series(network, "p_min_pu", dispatchable)
            scaled = base.copy()
            for period in periods.unique():
                hours = np.asarray(periods == period)
                if period not in observed.index:
                    raise ValueError(f"No observed {carrier} energy for {period.date()}")
                carrier_energy = float(observed.at[period])
                if shares is None:
                    share = 1.0
                else:
                    month = months[hours][0]
                    if month not in shares.index:
                        raise ValueError(f"No plant-level {carrier} generation for {month:%Y-%m}")
                    if carrier_energy > 0 and shares.loc[month].sum() == 0:
                        raise ValueError(
                            f"Observed {carrier} energy in {month:%Y-%m} but no plant generated"
                        )
                    share = float(shares.at[month, plant])
                plant_energy = carrier_energy * share
                scheduled = float(fixed_energy[hours].sum())
                if not len(dispatchable) and plant_energy > scheduled + 1e-6:
                    raise ValueError(
                        f"{carrier} plant {plant!r} generated in {period.date()} "
                        "but has no dispatchable model units"
                    )
                target = max(plant_energy - scheduled, 0.0)
                block = base.to_numpy()[hours]
                hour_weights = weights.to_numpy()[hours]
                before = float(block @ capacity @ hour_weights)
                values, reached = scale_to_energy(block, capacity, hour_weights, target)
                scaled.iloc[hours] = values
                rows.append({
                    "carrier": carrier,
                    "plant": plant,
                    "period_start": period,
                    "observed_mwh": plant_energy,
                    "fixed_schedule_mwh": scheduled,
                    "dispatchable_target_mwh": target,
                    "available_before_mwh": before,
                    "available_after_mwh": reached,
                    "shortfall_mwh": max(target - reached, 0.0),
                    "dispatchable_capacity_mw": float(capacity.sum()),
                    "mean_p_max_pu_after": float(
                        (values @ capacity @ hour_weights)
                        / max(capacity.sum() * hour_weights.sum(), 1e-12)
                    ),
                })
            scaled_columns.append(scaled)
            # A minimum output above the new availability would make the model infeasible.
            clipped = p_min.where(p_min <= scaled, scaled)
            changed = clipped.columns[(clipped != p_min).any()]
            if len(changed):
                clipped_minimum.append(clipped[changed])
        scaled_all = pd.concat(scaled_columns, axis=1)
        if len(scaled_all.columns):
            p_max_t = network.generators_t.p_max_pu
            network.generators_t.p_max_pu = pd.concat(
                [p_max_t.drop(columns=p_max_t.columns.intersection(scaled_all.columns)), scaled_all],
                axis=1,
            )
        if clipped_minimum:
            clipped_all = pd.concat(clipped_minimum, axis=1)
            p_min_t = network.generators_t.p_min_pu
            network.generators_t.p_min_pu = pd.concat(
                [p_min_t.drop(columns=p_min_t.columns.intersection(clipped_all.columns)), clipped_all],
                axis=1,
            )
    return pd.DataFrame(rows)


def apply_marginal_cost_overrides(
    network: pypsa.Network, overrides: Mapping[str, float],
) -> pd.DataFrame:
    """Set every unit of each overridden carrier to the given marginal cost."""
    rows: list[dict[str, object]] = []
    for carrier, cost in overrides.items():
        units = network.generators.index[network.generators.carrier.eq(carrier)]
        if not len(units):
            raise ValueError(f"marginal_costs.{carrier} is set but there are no {carrier} generators")
        previous = network.generators.loc[units, "marginal_cost"].astype(float)
        dynamic = network.generators_t.marginal_cost
        present = dynamic.columns.intersection(units)
        if len(present):
            network.generators_t.marginal_cost = dynamic.drop(columns=present)
        network.generators.loc[units, "marginal_cost"] = cost
        rows.append({
            "carrier": carrier,
            "units": len(units),
            "previous_min_inr_per_mwh": float(previous.min()),
            "previous_max_inr_per_mwh": float(previous.max()),
            "marginal_cost_inr_per_mwh": cost,
            "dropped_time_series": len(present),
        })
    return pd.DataFrame(rows)


def apply_energy_target_policy(
    network: pypsa.Network,
    policy: Mapping[str, Any] | None,
    cost_overrides: Mapping[str, Any] | None,
) -> tuple[dict[str, str], dict[str, float], pd.DataFrame, pd.DataFrame]:
    """Validate, apply and record the policy on ``network`` (in place)."""
    resolved = normalise_policy(policy)
    costs = normalise_cost_overrides(cost_overrides)
    cost_report = apply_marginal_cost_overrides(network, costs)
    availability_report = apply_availability_profiles(network, resolved)
    network.meta = {**(network.meta or {}), META_KEY: resolved}
    return resolved, costs, availability_report, cost_report
