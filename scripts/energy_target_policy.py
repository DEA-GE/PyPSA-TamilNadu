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
``must_run``
    As ``availability``, and ``p_min_pu`` is set to the scaled ``p_max_pu``,
    so the units produce exactly their available energy whatever their cost
    (for plants that are must-run under the grid code, such as nuclear).
``energy_cap`` (oil/gas and bio-power only)
    No equality constraint. Availability is flat (``<carrier>_cap_availability``)
    and energy over each ``cap_period`` (annual or monthly) is capped at the
    observed energy, per plant for oil/gas: the CEA planning treatment of
    fuel-limited gas and biomass (see :func:`apply_energy_caps`).
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
import xarray as xr

from bio_power_targets import read_bio_power_monthly_targets
from small_hydro_targets import read_small_hydro_monthly_targets

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "model"
NUCLEAR_BUDGET_FILE = MODEL_DIR / "nuclear_daily_energy_budget.csv"
OIL_GAS_BUDGET_FILE = MODEL_DIR / "oil_gas_daily_energy_budget.csv"
PLANT_GENERATION_FILE = ROOT / "data" / "model_inputs" / "niti_iced_tamil_nadu_monthly_generation.csv"
PLANT_MAPPING_FILE = ROOT / "data" / "model_inputs" / "niti_iced_plant_mapping.csv"

TARGET_CARRIERS = ("oil_gas", "nuclear", "bio_power", "small_hydro")
TARGET_MODES = ("target", "availability", "must_run", "free")
# Modes per carrier; energy_cap applies to the fuel-limited carriers only.
CARRIER_MODES = {
    "oil_gas": TARGET_MODES + ("energy_cap",),
    "bio_power": TARGET_MODES + ("energy_cap",),
    "nuclear": TARGET_MODES,
    "small_hydro": TARGET_MODES,
}
CAP_PERIODS = {"annual": "YS-APR", "monthly": "MS"}
CAP_TRANSFERS = ("prorate", "allocation", "shadow_price")
AVAILABILITY_PERIODS = {"monthly": "MS", "daily": "D"}
AVAILABILITY_SOURCES = ("plant", "carrier")
# Carriers with plant-level monthly observations (NITI ICED).
PLANT_LEVEL_CARRIERS = ("oil_gas", "nuclear")
DEFAULT_POLICY: dict[str, Any] = {
    **{carrier: "target" for carrier in TARGET_CARRIERS},
    "availability_period": "monthly",
    "availability_source": "plant",
    "cap_period": "annual",
    "oil_gas_cap_availability": 0.9,
    "bio_power_cap_availability": 0.6,
    "cap_transfer": "allocation",
    # Produced by config/runs/annual_capped_monthly.yaml.
    "cap_source": "results/run_results/annual_2025-04-01_2026-03-31_LP/annual_capped_monthly",
}
_ALLOWED_VALUES = {
    "availability_period": tuple(AVAILABILITY_PERIODS),
    "availability_source": AVAILABILITY_SOURCES,
    "cap_period": tuple(CAP_PERIODS),
    "cap_transfer": CAP_TRANSFERS,
    **CARRIER_MODES,
}
_FRACTION_KEYS = ("oil_gas_cap_availability", "bio_power_cap_availability")
# Carriers whose marginal cost a run config may override, in INR/MWh.
COST_OVERRIDE_CARRIERS = ("oil_gas", "diesel", "bio_power", "nuclear", "small_hydro")
META_KEY = "energy_targets"


def normalise_policy(policy: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a complete, validated policy; missing keys take the defaults."""
    resolved = dict(DEFAULT_POLICY)
    for key, value in (policy or {}).items():
        if key not in resolved:
            raise ValueError(
                f"Unknown energy_targets key '{key}'. Known keys: {sorted(resolved)}"
            )
        if key == "cap_source":
            resolved[key] = "" if value is None else str(value).strip()
            continue
        if key in _FRACTION_KEYS:
            if isinstance(value, bool):
                raise ValueError(f"energy_targets.{key} must be a number in (0, 1]")
            try:
                number = float(value)
            except (TypeError, ValueError) as error:
                raise ValueError(f"energy_targets.{key} must be a number in (0, 1]") from error
            if not 0.0 < number <= 1.0:
                raise ValueError(f"energy_targets.{key} must be a number in (0, 1]")
            resolved[key] = number
            continue
        value = str(value).strip().lower()
        allowed = _ALLOWED_VALUES[key]
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
        if policy[carrier] not in ("availability", "must_run"):
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
            if policy[carrier] == "must_run":
                # Output is fixed at the availability profile.
                clipped = scaled.copy()
            else:
                # A minimum output above the new availability would make the model infeasible.
                clipped = p_min.where(p_min <= scaled, scaled)
            changed = (
                clipped.columns if policy[carrier] == "must_run"
                else clipped.columns[(clipped != p_min).any()]
            )
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


CAPS_KEY = "energy_caps"


def _cap_groups(network: pypsa.Network, carrier: str) -> tuple[dict[str, pd.Index], pd.DataFrame | None]:
    """Unit groups that share a cap: ICED plants for oil/gas, the whole carrier otherwise."""
    if carrier in PLANT_LEVEL_CARRIERS:
        groups = plant_groups(network, carrier)
        return groups, plant_monthly_shares(carrier, list(groups))
    units = network.generators.index[network.generators.carrier.eq(carrier)]
    return {"": units}, None


def apply_energy_caps(network: pypsa.Network, policy: Mapping[str, Any]) -> pd.DataFrame:
    """Flat availability and per-period energy caps for ``energy_cap`` carriers.

    Following CEA's planning studies (NEP 2022-32, optimal generation mix
    2029-30), gas and bio-power are dispatchable plants with a flat
    availability (``<carrier>_cap_availability``) whose energy is limited by
    fuel supply. The cap is the observed FY2025-26 energy over each
    ``cap_period`` (the year or each month): per NITI ICED plant for oil/gas
    (the carrier's observed energy times the plant's share of ICED generation),
    for the whole carrier for bio-power. Plants that did not generate get a
    zero cap. Units with zero availability (commissioning, fixed schedules)
    keep it. The caps are stored in ``network.meta`` and added to each solve by
    :func:`add_energy_caps`.
    """
    frequency = CAP_PERIODS[policy["cap_period"]]
    weights = network.snapshot_weightings.generators.reindex(network.snapshots).astype(float)
    caps: list[dict[str, object]] = []
    for carrier in TARGET_CARRIERS:
        if policy[carrier] != "energy_cap":
            continue
        availability = float(policy[f"{carrier}_cap_availability"])
        monthly = observed_energy_mwh(carrier, "MS")
        groups, shares = _cap_groups(network, carrier)
        for plant, members in groups.items():
            fixed = fixed_schedule_units(network, members) if len(members) else pd.Index([])
            dispatchable = members.difference(fixed)
            if shares is None:
                plant_monthly = monthly.copy()
            else:
                share = shares.reindex(monthly.index)
                if share.isna().any().any():
                    raise ValueError(f"No plant-level {carrier} generation for some FY2025-26 months")
                plant_monthly = monthly * share[plant]
            fixed_energy = (
                _per_unit_series(network, "p_max_pu", fixed)
                .mul(network.generators.loc[fixed, "p_nom"], axis=1)
                .sum(axis=1)
                .mul(weights)
            )
            fixed_monthly = fixed_energy.groupby(
                network.snapshots.to_period("M").to_timestamp(how="start")
            ).sum().reindex(plant_monthly.index, fill_value=0.0)
            dispatchable_monthly = (plant_monthly - fixed_monthly).clip(lower=0.0)
            per_period = dispatchable_monthly.resample(frequency).sum()
            if len(dispatchable):
                base = _per_unit_series(network, "p_max_pu", dispatchable)
                flat = base.where(base <= 0.0, availability)
                p_max_t = network.generators_t.p_max_pu
                network.generators_t.p_max_pu = pd.concat(
                    [p_max_t.drop(columns=p_max_t.columns.intersection(dispatchable)), flat], axis=1
                )
                p_min = _per_unit_series(network, "p_min_pu", dispatchable)
                clipped = p_min.where(p_min <= flat, flat)
                changed = clipped.columns[(clipped != p_min).any()]
                if len(changed):
                    p_min_t = network.generators_t.p_min_pu
                    network.generators_t.p_min_pu = pd.concat(
                        [p_min_t.drop(columns=p_min_t.columns.intersection(changed)), clipped[changed]],
                        axis=1,
                    )
            elif float(per_period.sum()) > 1e-6:
                raise ValueError(
                    f"{carrier} plant {plant!r} generated but has no dispatchable model units"
                )
            capacity = float(network.generators.loc[dispatchable, "p_nom"].sum())
            for start, cap in per_period.items():
                end = start + pd.tseries.frequencies.to_offset(frequency)
                hours = (network.snapshots >= start) & (network.snapshots < end)
                caps.append({
                    "carrier": carrier,
                    "plant": plant,
                    "units": [str(unit) for unit in dispatchable],
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                    "period_hours": float(weights[hours].sum()),
                    "cap_mwh": float(cap),
                    "dispatchable_capacity_mw": capacity,
                    "cap_capacity_factor": float(cap) / max(capacity * float(weights[hours].sum()), 1e-12),
                })
    report = pd.DataFrame([{key: value for key, value in row.items() if key != "units"} for row in caps])
    if caps and policy["cap_transfer"] != "prorate":
        source = load_cap_source(policy)
        if policy["cap_transfer"] == "allocation":
            caps, allocation = allocate_caps_from_source(caps, source)
            report = report.merge(
                allocation[["carrier", "plant", "allocated_mwh", "allocated_days"]],
                on=["carrier", "plant"], how="left",
            )
        else:
            prices = cap_shadow_prices(caps, source)
            report = report.assign(
                used_in_source_mwh=prices["used_mwh"].to_numpy(),
                binding_in_source=prices["binding"].to_numpy(),
                shadow_price_inr_per_mwh=prices["shadow_price"].to_numpy(),
                partial_hours=prices["partial_hours"].to_numpy(),
            )
            caps = apply_cap_shadow_prices(network, caps, prices)
    network.meta = {**(network.meta or {}), CAPS_KEY: caps}
    return report


def load_cap_source(policy: Mapping[str, Any]) -> pypsa.Network:
    """Solved network of the annual LP run named by ``cap_source``."""
    source = str(policy.get("cap_source") or "").strip()
    if not source:
        raise ValueError(
            f"energy_targets.cap_transfer: {policy['cap_transfer']} needs energy_targets.cap_source "
            "(the results folder of an annual run with energy caps)"
        )
    path = Path(source)
    if not path.is_absolute():
        path = ROOT / path
    network_file = path / "solved_network.nc" if path.is_dir() else path
    if not network_file.exists():
        raise ValueError(
            f"energy_targets.cap_source has no solved network: {network_file}. Run the annual LP "
            "with the same energy caps first (python scripts/run_model.py "
            "config/runs/annual_capped_monthly.yaml), or set energy_targets.cap_transfer: prorate."
        )
    return pypsa.Network(str(network_file))


def _group_key(cap: Mapping[str, Any]) -> tuple[str, str]:
    return str(cap["carrier"]), str(cap["plant"])


def _source_dispatch(source: pypsa.Network, units: list[str]) -> pd.DataFrame:
    missing = sorted(set(units) - set(source.generators.index))
    if missing:
        raise ValueError(f"cap_source network lacks units: {missing[:5]}")
    return source.generators_t.p.reindex(columns=units, fill_value=0.0)


def allocate_caps_from_source(
    caps: list[dict[str, Any]], source: pypsa.Network,
) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    """Replace each group's caps by its daily dispatch in the annual LP (option 2).

    The annual LP sees the whole year, so its dispatch is the allocation of the
    cap over time. Each group's cap becomes one entry per day; a solve is then
    capped at the sum of the allocations of the days it covers.
    """
    weights = source.snapshot_weightings.generators
    allocated: list[dict[str, Any]] = []
    rows = []
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for cap in caps:
        groups.setdefault(_group_key(cap), []).append(cap)
    for (carrier, plant), entries in groups.items():
        units = list(entries[0]["units"])
        total_cap = sum(float(entry["cap_mwh"]) for entry in entries)
        if not units:
            allocated.extend(entries)
            continue
        daily = (
            _source_dispatch(source, units).sum(axis=1).mul(weights)
            .groupby(source.snapshots.normalize()).sum()
        )
        if float(daily.sum()) > total_cap * (1 + 1e-6) + 1.0:
            raise ValueError(
                f"cap_source dispatch of {carrier} {plant!r} ({daily.sum():,.0f} MWh) exceeds its cap "
                f"({total_cap:,.0f} MWh): it was not solved with the same energy caps"
            )
        for day, energy in daily.items():
            allocated.append({
                **{key: value for key, value in entries[0].items()
                   if key not in {"period_start", "period_end", "period_hours", "cap_mwh", "cap_capacity_factor"}},
                "period_start": day.isoformat(),
                "period_end": (day + pd.Timedelta(days=1)).isoformat(),
                "period_hours": float(weights.loc[source.snapshots.normalize() == day].sum()),
                "cap_mwh": max(float(energy), 0.0),
                # Allocations are summed over each solve rather than enforced per day.
                "allocated": True,
            })
        rows.append({"carrier": carrier, "plant": plant, "cap_mwh": total_cap,
                     "allocated_mwh": float(daily.sum()), "allocated_days": int((daily > 1e-6).sum())})
    return allocated, pd.DataFrame(rows)


def cap_shadow_prices(caps: list[dict[str, Any]], source: pypsa.Network) -> pd.DataFrame:
    """Recover each cap's shadow price (INR/MWh) from the annual LP solution.

    In the LP a capped unit is dispatched when its bus price exceeds its
    marginal cost plus the cap's shadow price. Hours in which a unit runs
    part-loaded pin the shadow price at price minus marginal cost (their
    median is used); otherwise it lies between the largest price-minus-cost
    gap of hours the unit is off and the smallest gap of hours it runs at full
    availability (the midpoint is used). A cap that is not binding has a zero
    shadow price.
    """
    weights = source.snapshot_weightings.generators
    prices = source.buses_t.marginal_price
    rows = []
    for position, cap in enumerate(caps):
        units = list(cap["units"])
        row = {"position": position, "carrier": cap["carrier"], "plant": cap["plant"],
               "period_start": cap["period_start"], "cap_mwh": float(cap["cap_mwh"])}
        if not units or float(cap["cap_mwh"]) <= 0.0:
            rows.append({**row, "used_mwh": 0.0, "binding": False, "shadow_price": 0.0,
                         "partial_hours": 0, "lower": np.nan, "upper": np.nan})
            continue
        start, end = pd.Timestamp(cap["period_start"]), pd.Timestamp(cap["period_end"])
        hours = source.snapshots[(source.snapshots >= start) & (source.snapshots < end)]
        dispatch = _source_dispatch(source, units).loc[hours]
        available = (
            _per_unit_series(source, "p_max_pu", pd.Index(units)).loc[hours]
            .mul(source.generators.loc[units, "p_nom"], axis=1)
        )
        cost = source.generators.loc[units, "marginal_cost"].astype(float)
        if len(source.generators_t.marginal_cost.columns.intersection(units)):
            raise ValueError("Time-varying marginal costs are not supported for capped units")
        gap = prices.loc[hours, source.generators.loc[units, "bus"]].to_numpy() - cost.to_numpy()
        gap = pd.DataFrame(gap, index=hours, columns=units)
        tolerance = 1e-3
        on = dispatch >= available - tolerance
        off = (dispatch <= tolerance) & (available > tolerance)
        partial = ~on & ~off & (available > tolerance)
        used = float(dispatch.sum(axis=1).mul(weights.loc[hours]).sum())
        binding = used >= float(cap["cap_mwh"]) - max(1.0, 1e-4 * float(cap["cap_mwh"]))
        lower = float(gap[off].max().max()) if off.any().any() else np.nan
        upper = float(gap[on & (available > tolerance)].min().min()) if (on & (available > tolerance)).any().any() else np.nan
        if not binding:
            value = 0.0
        elif partial.any().any():
            value = float(np.nanmedian(gap[partial].to_numpy()))
        elif np.isfinite(lower) and np.isfinite(upper):
            value = (lower + upper) / 2.0 if upper >= lower else upper
        else:
            value = upper if np.isfinite(upper) else lower
        rows.append({**row, "used_mwh": used, "binding": bool(binding),
                     "shadow_price": max(float(value), 0.0) if np.isfinite(value) else 0.0,
                     "partial_hours": int(partial.to_numpy().sum()), "lower": lower, "upper": upper})
    return pd.DataFrame(rows)


def apply_cap_shadow_prices(
    network: pypsa.Network, caps: list[dict[str, Any]], prices: pd.DataFrame,
) -> list[dict[str, Any]]:
    """Add each cap's shadow price to its units' marginal cost (option 3).

    Caps with a positive energy limit are dropped; zero caps (plants without
    fuel) stay as hard limits.
    """
    adders: dict[str, pd.Series] = {}
    for cap, row in zip(caps, prices.itertuples()):
        if not cap["units"] or float(cap["cap_mwh"]) <= 0.0 or row.shadow_price <= 0.0:
            continue
        start, end = pd.Timestamp(cap["period_start"]), pd.Timestamp(cap["period_end"])
        hours = (network.snapshots >= start) & (network.snapshots < end)
        for unit in cap["units"]:
            series = adders.setdefault(unit, pd.Series(0.0, index=network.snapshots))
            series[hours] += row.shadow_price
    if adders:
        static = network.generators.loc[list(adders), "marginal_cost"].astype(float)
        frame = pd.DataFrame(adders).add(static, axis=1)
        dynamic = network.generators_t.marginal_cost
        network.generators_t.marginal_cost = pd.concat(
            [dynamic.drop(columns=dynamic.columns.intersection(frame.columns)), frame], axis=1
        )
    return [cap for cap in caps if cap["units"] and float(cap["cap_mwh"]) <= 0.0]


def add_energy_caps(network: pypsa.Network, snapshots: pd.DatetimeIndex) -> None:
    """Add the stored energy caps to the model being solved over ``snapshots``.

    Each cap period (a month or the year) gets one constraint over the solved
    hours inside it, with the cap times the share of the period's hours solved:
    a solve covering whole periods gets their caps exactly, shorter solves
    (daily, weekly or rolling windows) the prorated share. Daily allocations
    from ``cap_transfer: allocation`` are instead summed over the solve into
    one constraint per group, so a window may shift energy between its days.

    A constraint whose hours span several months is written sparsely: one
    auxiliary energy variable per month, defined by a row over that month's
    hours, and one row capping their sum. This is the same constraint as a
    single row over all hours, but no row couples the whole year; a dense
    annual row (bio-power: about 80 units x 8,760 hours) stalled HiGHS's dual
    simplex in the annual LP.
    """
    caps = (network.meta or {}).get(CAPS_KEY) or []
    if not caps:
        return
    snapshots = pd.DatetimeIndex(snapshots)
    weights = network.snapshot_weightings.generators.reindex(snapshots).astype(float)
    dispatch = network.model["Generator-p"]
    dimension = next(dim for dim in dispatch.dims if dim != "snapshot")
    stamp = f"{snapshots[0]:%Y%m%d%H}"
    allocated: dict[tuple[str, str], float] = {}
    allocated_hours: dict[tuple[str, str], pd.DatetimeIndex] = {}
    units_of: dict[tuple[str, str], list[str]] = {}
    for position, cap in enumerate(caps):
        if not cap["units"]:
            continue
        start, end = pd.Timestamp(cap["period_start"]), pd.Timestamp(cap["period_end"])
        hours = snapshots[(snapshots >= start) & (snapshots < end)]
        if not len(hours):
            continue
        share = min(float(weights.loc[hours].sum()) / float(cap["period_hours"]), 1.0)
        limit = float(cap["cap_mwh"]) * share
        key = _group_key(cap)
        if cap.get("allocated"):
            allocated[key] = allocated.get(key, 0.0) + limit
            allocated_hours[key] = allocated_hours.get(key, pd.DatetimeIndex([])).union(hours)
            units_of[key] = list(cap["units"])
            continue
        _add_cap_constraint(
            network, dispatch, dimension, list(cap["units"]), hours, weights, limit,
            f"EnergyCap-{cap['carrier']}-{position}-{stamp}",
        )
    for number, (key, limit) in enumerate(allocated.items()):
        _add_cap_constraint(
            network, dispatch, dimension, units_of[key], allocated_hours[key], weights, limit,
            f"EnergyCap-{key[0]}-allocated{number}-{stamp}",
        )


def _add_cap_constraint(
    network: pypsa.Network,
    dispatch,
    dimension: str,
    units: list[str],
    hours: pd.DatetimeIndex,
    weights: pd.Series,
    limit: float,
    name: str,
) -> None:
    """Energy of ``units`` over ``hours`` <= ``limit``, sparse across months."""
    model = network.model
    months = hours.to_period("M")

    def energy(selected: pd.DatetimeIndex):
        hour_weights = xr.DataArray(
            weights.loc[selected].to_numpy(), dims="snapshot", coords={"snapshot": selected}
        )
        return (dispatch.sel({dimension: units, "snapshot": selected}) * hour_weights).sum()

    unique_months = months.unique()
    if len(unique_months) <= 1:
        model.add_constraints(energy(hours) <= limit, name=name)
        return
    # Auxiliary names have no "-" so PyPSA does not try to map them to a component.
    label = name.replace("-", "_")
    month_index = pd.Index([str(month) for month in unique_months], name=f"{label}_month")
    monthly = model.add_variables(lower=0.0, coords=[month_index], name=f"{label}_monthly_energy")
    for month in unique_months:
        model.add_constraints(
            monthly.sel({month_index.name: str(month)}) - energy(hours[months == month]) == 0.0,
            name=f"{name}-{month}",
        )
    model.add_constraints(monthly.sum() <= limit, name=name)


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
    cap_report = apply_energy_caps(network, resolved)
    if not cap_report.empty:
        availability_report = pd.concat(
            [availability_report, cap_report.assign(kind="energy_cap")], ignore_index=True
        )
    network.meta = {**(network.meta or {}), META_KEY: resolved}
    return resolved, costs, availability_report, cost_report
