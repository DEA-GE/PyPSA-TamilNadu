"""Coal unit availability: auxiliary consumption, planned maintenance and forced outages.

The ``outages`` section of a run config (see ``config/defaults.yaml``) turns
this on. For every committable coal unit, the hourly ``p_max_pu`` becomes

    base availability x capability x (1 - share of the hour the unit is out)

**Capability** (``partial_derating: demonstrated``, the default) caps each unit
at its best whole day of FY2025-26 without any recorded outage, as a share of
its rating (``data/model_inputs/coal_unit_capability_FY2025_26.csv``). It
stands in for partial deratings of running units, which the outage record
does not contain; ``none`` leaves units at their full rating.

**Base availability** (``base_availability``) replaces the model's static coal
``p_max_pu`` of 0.85, which was CEA's "85% availability" and so already
included outages:

- ``net_of_auxiliary``: 1 - auxiliary consumption, so the model dispatches net
  (ex-bus) output. Tamil Nadu demand is ex-bus, as in CEA's load-generation
  balance (net availability = gross generation minus auxiliary consumption).
- ``gross``: 1.0, gross output at the generator terminals.
- ``model``: keep the network's static ``p_max_pu``.

**Outages** come from ``data/model_inputs/coal_unit_outages_FY2025_26.csv``
(``scripts/prepare_coal_outages.py``), one row per unit and day out with the
share of the day:

- ``planned``: ``actual`` replays the FY2025-26 maintenance record;
  ``optimise`` lets PyPSA's maintenance scheduling (``maintainable``
  generators) choose when each unit takes its FY2025-26 maintenance duration;
  ``none`` imposes no maintenance.
- ``forced``: ``actual`` replays the FY2025-26 forced outages; ``sampled``
  draws them at random (fixed ``seed``) from the record's outage frequency and
  durations per unit size class; ``none`` imposes none.
- Always imposed when the section is enabled: days before commissioning,
  commercial unavailability (no fuel supply agreement, no PPA) and units out of
  service for the whole record period (e.g. Tuticorin TPS units 1-2 after the
  March 2025 fire).
- Reserve shutdowns are never imposed: whether a unit runs is the unit
  commitment's decision.

A day's outage share is placed in hours: at the start of the day when the unit
was out the day before, at the end when it is out the day after, otherwise in
the middle of the day.

**Central stations** (``central_availability: declared``, the default): the
seven central coal stations (Vallur, NTPL, NNTPS and four NLC stations) use
their declared capacity from the SRPC Regional Energy Accounts
(``data/model_inputs/central_station_declared_capacity_FY2025_26.csv``,
``scripts/prepare_declared_capacity.py``). With ``planned: actual`` and
``forced: actual`` it replaces the outage record and capability hour by hour,
split across each station's identical units (``allocate_declared``). In the
other modes each station gets one factor instead of the capability cap:
declared capacity over the capacity the outage record leaves available.
``record`` treats them like the other units.

**Resolution** (``resolution``): ``hourly`` (the default) keeps the hourly
availability; ``monthly`` or ``annual`` replace it by its mean per month or
year, for a less restricted run. A unit available on average a share of its
best hour then acts as a smaller unit: its minimum load is scaled by the same
share. Commissioning dates stay exact.

PyPSA's maintenance scheduling needs the whole year in one solve (each event
must fit inside the optimisation horizon), so ``optimise`` solves one MILP over
the year at daily resolution without unit commitment and then fixes the
schedule as hourly availability for every run mode, including rolling runs.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import pypsa
import xarray as xr

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECORD = "data/model_inputs/coal_unit_outages_FY2025_26.csv"
DEFAULT_AUXILIARY = "data/model_inputs/coal_auxiliary_consumption.csv"
DEFAULT_CAPABILITY = "data/model_inputs/coal_unit_capability_FY2025_26.csv"
DEFAULT_DECLARED = "data/model_inputs/central_station_declared_capacity_FY2025_26.csv"
DEFAULT_DECLARED_UNITS = "data/model_inputs/central_station_units.csv"

BASE_CHOICES = ("net_of_auxiliary", "gross", "model")
DERATING_CHOICES = ("demonstrated", "none")
CENTRAL_CHOICES = ("declared", "record")
RESOLUTION_CHOICES = ("hourly", "monthly", "annual")
PLANNED_CHOICES = ("none", "actual", "optimise")
FORCED_CHOICES = ("none", "actual", "sampled")
SECTION_KEYS = {
    "enabled", "base_availability", "partial_derating", "central_availability", "resolution",
    "planned", "forced", "seed", "record", "auxiliary", "capability", "declared_capacity",
    "declared_units", "maintenance_schedule",
}
ALWAYS_IMPOSED = ("commissioning", "commercial")
KINDS = ("planned", "forced", "commercial", "commissioning", "reserve_shutdown")
# Unit size classes for the sampled forced-outage parameters (MW, upper bounds).
SIZE_CLASSES = ((200.0, "below 200 MW"), (400.0, "200-400 MW"), (np.inf, "400 MW and above"))
# Maintenance scheduling: weight of residual load in the maintenance cost (INR
# per MW out per hour at the year's highest daily residual load), which breaks
# ties between equally cheap dates in favour of low-residual-load days.
MAINTENANCE_RESIDUAL_LOAD_WEIGHT = 100.0
MAINTENANCE_MIP_GAP = 0.001
MAINTENANCE_TIME_LIMIT_S = 900.0
HOURS_PER_DAY = 24
OUT_OF_SERVICE_SHARE = 0.99


class OutageError(ValueError):
    """Raised when the outages settings or data are invalid."""


def validate_section(section: Mapping[str, Any]) -> dict[str, Any]:
    """Check and normalise the ``outages`` config section."""
    unknown = set(section) - SECTION_KEYS
    if unknown:
        raise OutageError(f"outages: unknown keys {sorted(unknown)}")
    settings = dict(section)
    if not isinstance(settings.get("enabled"), bool):
        raise OutageError("outages.enabled must be true or false")
    for key, choices in (
        ("base_availability", BASE_CHOICES),
        ("partial_derating", DERATING_CHOICES),
        ("central_availability", CENTRAL_CHOICES),
        ("resolution", RESOLUTION_CHOICES),
        ("planned", PLANNED_CHOICES),
        ("forced", FORCED_CHOICES),
    ):
        value = str(settings.get(key)).strip().lower()
        if value not in choices:
            raise OutageError(f"outages.{key} must be one of {list(choices)}, got {settings.get(key)!r}")
        settings[key] = value
    if settings["central_availability"] == "declared" and settings["base_availability"] == "model":
        raise OutageError(
            "outages.central_availability: declared needs base_availability net_of_auxiliary or "
            "gross; declared capacity is measured ex-bus"
        )
    seed = settings.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise OutageError("outages.seed must be a non-negative integer")
    for key in ("record", "auxiliary", "capability", "declared_capacity", "declared_units"):
        if not settings.get(key):
            raise OutageError(f"outages.{key} must name a CSV file")
        settings[key] = str(settings[key])
    schedule = settings.get("maintenance_schedule")
    if schedule is not None:
        if settings["planned"] != "optimise":
            raise OutageError("outages.maintenance_schedule is used only with outages.planned: optimise")
        settings["maintenance_schedule"] = str(schedule)
    return settings


def _resolve(path: str | Path) -> Path:
    path = Path(path).expanduser()
    return path if path.is_absolute() else ROOT / path


def read_record(path: str | Path = DEFAULT_RECORD) -> pd.DataFrame:
    """The unit outage record, one row per unit and day out."""
    record = pd.read_csv(_resolve(path), parse_dates=["date"])
    missing = {"component", "date", "kind", "outage_days"} - set(record.columns)
    if missing:
        raise OutageError(f"{path}: missing columns {sorted(missing)}")
    unknown = set(record.kind) - set(KINDS)
    if unknown:
        raise OutageError(f"{path}: unknown outage kinds {sorted(unknown)}")
    record["outage_days"] = record.outage_days.clip(lower=0.0, upper=1.0)
    return record


def read_auxiliary(path: str | Path = DEFAULT_AUXILIARY) -> pd.Series:
    """Auxiliary consumption per unit in % of gross generation."""
    table = pd.read_csv(_resolve(path))
    if table.component.duplicated().any():
        raise OutageError(f"{path}: duplicate components")
    values = table.set_index("component").auxiliary_consumption_pct.astype(float)
    if ((values < 0) | (values >= 100)).any():
        raise OutageError(f"{path}: auxiliary consumption must be within 0..100 %")
    return values


def read_capability(path: str | Path = DEFAULT_CAPABILITY) -> pd.Series:
    """Demonstrated capability per unit, as a share of its rating (0..1)."""
    table = pd.read_csv(_resolve(path))
    if table.component.duplicated().any():
        raise OutageError(f"{path}: duplicate components")
    values = table.set_index("component").capability_pu.astype(float)
    if ((values <= 0) | (values > 1)).any():
        raise OutageError(f"{path}: capability must be within 0..1")
    return values


def read_declared(
    path: str | Path = DEFAULT_DECLARED, units_path: str | Path = DEFAULT_DECLARED_UNITS
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Hourly declared capacity (MW, ex-bus) per station and the station units."""
    declared = pd.read_csv(_resolve(path), index_col=0, parse_dates=True)
    units = pd.read_csv(_resolve(units_path))
    missing = set(units.station) - set(declared.columns)
    if missing:
        raise OutageError(f"{path}: no declared capacity for stations {sorted(missing)}")
    if units.component.duplicated().any():
        raise OutageError(f"{units_path}: a unit belongs to more than one station")
    if (declared < 0).any().any():
        raise OutageError(f"{path}: negative declared capacity")
    return declared, units


def allocate_declared(
    declared_mw: pd.Series, rating_mw: float, priority: pd.DataFrame, p_min_pu: float = 0.0
) -> pd.DataFrame:
    """Split a station's declared capacity across its identical units, each hour.

    The capacity is spread evenly over the number of units that gives the most
    usable capacity, each at or below its rating and at or above its minimum
    load (``p_min_pu`` of the rating), preferring fewer units on a tie: 942 MW
    on three 500 MW units is two units at 471 MW. ``priority`` (hours x units)
    ranks the units each hour, highest first (e.g. their availability in the
    outage record); ties keep the column order. Returns MW per unit and hour.
    """
    declared = declared_mw.reindex(priority.index).to_numpy(dtype=float)
    count = priority.shape[1]
    best_capacity = np.zeros(len(declared))
    best_units = np.zeros(len(declared), dtype=int)
    for units_on in range(1, count + 1):
        level = np.minimum(declared / units_on, rating_mw)
        usable = (declared > 0) & (level >= p_min_pu * rating_mw - 1e-9)
        capacity = np.where(usable, units_on * level, 0.0)
        better = capacity > best_capacity + 1e-9
        best_units[better] = units_on
        best_capacity[better] = capacity[better]
    level = np.divide(best_capacity, best_units, out=np.zeros_like(best_capacity), where=best_units > 0)
    rank = (-priority).rank(axis=1, method="first").to_numpy()
    allocated = np.where(rank <= best_units[:, None], level[:, None], 0.0)
    return pd.DataFrame(allocated, index=priority.index, columns=priority.columns)


def smooth_availability(
    availability: pd.DataFrame, commissioned: pd.DataFrame, resolution: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Mean availability per month or year, and the factor that scales minimum load.

    Means are taken over the hours each unit is commissioned and multiplied
    back by the commissioned share, so commissioning dates stay exact. A unit
    available on average a share ``phi`` of its best hour is treated as a
    smaller unit: its minimum load is scaled by the same ``phi / best``.
    Returns the smoothed availability and that scaling factor.
    """
    index = pd.DatetimeIndex(availability.index)
    period = index.to_period("M") if resolution == "monthly" else pd.Index(np.zeros(len(index), dtype=int))
    weight = commissioned.reindex_like(availability).fillna(1.0)
    operating = availability.div(weight.where(weight > 0))
    numerator = (operating.fillna(0.0) * weight).groupby(period).transform("sum")
    denominator = weight.groupby(period).transform("sum")
    mean = numerator.div(denominator.where(denominator > 0)).fillna(0.0)
    mean.index = availability.index
    best = availability.max()
    scale = mean.div(best.where(best > 0), axis=1).fillna(0.0).clip(upper=1.0)
    return mean * weight, scale


def out_of_service_units(record: pd.DataFrame, days: pd.DatetimeIndex | None = None) -> list[str]:
    """Units out all day (other than reserve shutdown) on nearly every day.

    ``days`` defaults to the record period. A unit out on at least
    ``OUT_OF_SERVICE_SHARE`` of the days is out of service; the margin covers
    days missing from the source data (e.g. 2 June 2025 for Tuticorin (P) TPP).
    """
    if days is None:
        days = pd.date_range(record.date.min(), record.date.max(), freq="D")
    days = pd.DatetimeIndex(days).normalize().unique()
    out = record[
        (record.kind != "reserve_shutdown") & (record.outage_days >= 0.999) & record.date.isin(days)
    ]
    full = out.groupby("component").date.nunique()
    return sorted(full.index[full >= OUT_OF_SERVICE_SHARE * len(days)])


def hourly_outage(rows: pd.DataFrame, snapshots: pd.DatetimeIndex) -> dict[str, pd.DataFrame]:
    """Share of each hour each unit is out, per outage kind.

    ``rows`` holds daily outage shares (``component``, ``date``, ``kind``,
    ``outage_days``). A partial day is placed at the start of the day when the
    unit was out the day before, at the end when it is out the day after,
    otherwise in the middle. Returns one snapshots x units frame per kind.
    """
    snapshots = pd.DatetimeIndex(snapshots)
    if rows.empty:
        return {}
    rows = rows[["component", "date", "kind", "outage_days"]].copy()
    rows["date"] = pd.to_datetime(rows.date).dt.normalize()
    out_days = set(zip(rows.component, rows.date))
    one_day = pd.Timedelta(days=1)
    before = np.array([(c, d - one_day) in out_days for c, d in zip(rows.component, rows.date)])
    after = np.array([(c, d + one_day) in out_days for c, d in zip(rows.component, rows.date)])
    share = rows.outage_days.to_numpy(dtype=float)
    length = HOURS_PER_DAY * share
    start = np.where(
        share >= 1.0, 0.0,
        np.where(before, 0.0, np.where(after, HOURS_PER_DAY - length, (HOURS_PER_DAY - length) / 2.0)),
    )
    hours = np.arange(HOURS_PER_DAY, dtype=float)
    overlap = np.clip(
        np.minimum((start + length)[:, None], hours[None, :] + 1.0) - np.maximum(start[:, None], hours[None, :]),
        0.0, 1.0,
    )
    long = pd.DataFrame(overlap, columns=range(HOURS_PER_DAY))
    long["component"] = rows.component.to_numpy()
    long["date"] = rows.date.to_numpy()
    long["kind"] = rows.kind.to_numpy()
    long = long.melt(id_vars=["component", "date", "kind"], var_name="hour", value_name="share")
    long = long[long.share > 0]
    long["snapshot"] = long.date + pd.to_timedelta(long.hour.astype(int), unit="h")
    result = {}
    for kind, part in long.groupby("kind"):
        frame = part.pivot_table(index="snapshot", columns="component", values="share", aggfunc="sum")
        result[kind] = frame.reindex(snapshots).fillna(0.0).clip(upper=1.0)
    return result


def _spells(dates: pd.Series, shares: pd.Series) -> list[float]:
    """Durations in hours of runs of consecutive outage days."""
    order = np.argsort(dates.to_numpy())
    days = pd.DatetimeIndex(dates.to_numpy()[order]).normalize()
    hours = shares.to_numpy(dtype=float)[order] * HOURS_PER_DAY
    # Compare as Timedeltas: the datetime resolution may be s, us or ns.
    breaks = np.r_[True, (days[1:] - days[:-1]) != pd.Timedelta(days=1)]
    groups = np.cumsum(breaks)
    return [float(hours[groups == g].sum()) for g in np.unique(groups)]


def size_class(capacity_mw: float) -> str:
    return next(label for bound, label in SIZE_CLASSES if capacity_mw < bound)


def forced_outage_parameters(
    record: pd.DataFrame, capacity_mw: pd.Series, exclude: list[str] | tuple[str, ...] = ()
) -> pd.DataFrame:
    """Forced-outage rate, frequency and duration per unit size class.

    Exposure is the unit's hours in the record period minus its planned,
    commercial and pre-commissioning hours. Units in ``exclude`` (out of
    service all period) are left out. Returns one row per class with the
    observed outage durations in hours (``durations_h``) for resampling.
    """
    days = pd.date_range(record.date.min(), record.date.max(), freq="D")
    period_h = len(days) * HOURS_PER_DAY
    units = capacity_mw.index.difference(list(exclude))
    rows = []
    for label in [label for _, label in SIZE_CLASSES]:
        members = [u for u in units if size_class(float(capacity_mw[u])) == label]
        if not members:
            continue
        part = record[record.component.isin(members)]
        blocked_h = part[part.kind.isin(["planned", *ALWAYS_IMPOSED])].outage_days.sum() * HOURS_PER_DAY
        forced = part[part.kind == "forced"]
        durations = []
        for _, unit_rows in forced.groupby("component"):
            durations.extend(_spells(unit_rows.date, unit_rows.outage_days))
        exposure_h = len(members) * period_h - blocked_h
        forced_h = float(sum(durations))
        rows.append(
            {
                "size_class": label,
                "units": len(members),
                "exposure_h": exposure_h,
                "forced_h": forced_h,
                "outages": len(durations),
                "forced_outage_rate": forced_h / exposure_h if exposure_h > 0 else 0.0,
                "mean_duration_h": forced_h / len(durations) if durations else 0.0,
                "mean_time_between_h": (exposure_h - forced_h) / len(durations) if durations else np.inf,
                "durations_h": durations,
            }
        )
    return pd.DataFrame(rows).set_index("size_class")


def _unit_rng(seed: int, component: str) -> np.random.Generator:
    """Per-unit random stream: the same unit gets the same draws in every scenario."""
    return np.random.default_rng([int(seed), zlib.crc32(component.encode("utf-8"))])


def sample_forced_outages(
    parameters: pd.DataFrame,
    capacity_mw: pd.Series,
    snapshots: pd.DatetimeIndex,
    seed: int,
) -> pd.DataFrame:
    """Random forced outages: share of each hour each unit is out.

    Each unit alternates between available spells, exponentially distributed
    with the class's mean time between outages, and outages whose durations
    are resampled from the class's observed outage durations. The first state
    is drawn from the class's forced-outage rate.
    """
    snapshots = pd.DatetimeIndex(snapshots)
    horizon = float(len(snapshots))
    out = pd.DataFrame(0.0, index=snapshots, columns=capacity_mw.index)
    hours = np.arange(len(snapshots), dtype=float)
    for unit, capacity in capacity_mw.items():
        label = size_class(float(capacity))
        if label not in parameters.index:
            continue
        p = parameters.loc[label]
        durations = np.asarray(p.durations_h, dtype=float)
        if not len(durations) or not np.isfinite(p.mean_time_between_h):
            continue
        rng = _unit_rng(seed, str(unit))
        share = np.zeros(len(snapshots))
        t = 0.0
        if rng.random() < p.forced_outage_rate:
            # Out at the start: the remaining part of an outage in progress.
            t = float(rng.choice(durations)) * (1.0 - rng.random())
            share += np.clip(np.minimum(t, hours + 1.0) - hours, 0.0, 1.0)
        while t < horizon:
            t += float(rng.exponential(p.mean_time_between_h))
            if t >= horizon:
                break
            end = t + float(rng.choice(durations))
            share += np.clip(np.minimum(end, hours + 1.0) - np.maximum(t, hours), 0.0, 1.0)
            t = end
        out[unit] = np.clip(share, 0.0, 1.0)
    return out


@dataclass
class OutageResult:
    """What ``apply_outages`` imposed, for the run folder."""

    units: pd.DataFrame
    forced_parameters: pd.DataFrame | None = None
    maintenance_schedule: pd.DataFrame | None = None
    notes: list[str] = field(default_factory=list)


def _daily_network(network: pypsa.Network) -> pypsa.Network:
    """Copy of an hourly network at daily resolution, without unit commitment."""
    daily = network.copy()
    day = pd.DatetimeIndex(daily.snapshots).normalize()
    series = {}
    for component in daily.components:
        if component.static.empty:
            continue
        for attribute, frame in component.dynamic.items():
            if isinstance(frame, pd.DataFrame) and not frame.empty and len(frame) == len(daily.snapshots):
                series[(component.name, attribute)] = frame.groupby(day).mean()
    days = pd.DatetimeIndex(sorted(day.unique()))
    daily.set_snapshots(days)
    daily.snapshot_weightings.loc[:, :] = float(HOURS_PER_DAY)
    for (name, attribute), frame in series.items():
        frame.index.name = "snapshot"
        daily.c[name].dynamic[attribute] = frame.reindex(days)
    generators = daily.generators
    generators["committable"] = False
    generators["p_min_pu"] = 0.0
    for column in ("ramp_limit_up", "ramp_limit_down", "ramp_limit_start_up", "ramp_limit_shut_down"):
        generators[column] = np.nan
    if not daily.generators_t.p_min_pu.empty:
        daily.generators_t.p_min_pu = daily.generators_t.p_min_pu.clip(upper=0.0)
    if len(daily.global_constraints):
        daily.remove("GlobalConstraint", daily.global_constraints.index)
    return daily


def schedule_maintenance(
    network: pypsa.Network,
    duration_days: pd.Series,
    *,
    solver_name: str = "highs",
    threads: int | None = None,
) -> pd.DataFrame:
    """Choose maintenance dates with PyPSA's maintenance scheduling.

    ``network`` is the hourly network with the units' availability already
    set (without planned maintenance). Each unit in ``duration_days`` takes one
    maintenance event of that many whole days in a year-long MILP at daily
    resolution without unit commitment. Among dates of similar cost, the
    objective prefers days of low residual load (demand minus wind and solar).
    Returns one row per unit and maintenance day in the outage-record format.
    """
    duration_days = duration_days[duration_days >= 1].round()
    units = duration_days.index.intersection(network.generators.index)
    if units.empty:
        return pd.DataFrame(columns=["component", "date", "kind", "reason", "outage_days"])
    daily = _daily_network(network)
    daily.generators.loc[units, "maintainable"] = True
    daily.generators.loc[units, "maintenance_duration"] = duration_days[units] * HOURS_PER_DAY
    daily.generators.loc[units, "maintenance_events"] = 1
    daily.generators.loc[units, "maintenance_pu"] = 1.0

    load = daily.loads_t.p_set.sum(axis=1) if not daily.loads_t.p_set.empty else pd.Series(0.0, index=daily.snapshots)
    variable = daily.generators.index[daily.generators.carrier.isin(["solar", "wind", "offshore_wind"])]
    renewable = (
        daily.get_switchable_as_dense("Generator", "p_max_pu")[variable] * daily.generators.p_nom[variable]
    ).sum(axis=1)
    residual = (load - renewable).clip(lower=0.0)
    residual_pu = residual / residual.max() if residual.max() > 0 else residual
    nominal = daily.generators.p_nom[units]

    def tie_break(n: pypsa.Network, snapshots: pd.Index) -> None:
        maintenance = n.model["Generator-maintenance"]
        names = pd.Index(maintenance.coords["name"].values)
        cost = np.outer(residual_pu.reindex(snapshots).to_numpy(), nominal[names].to_numpy())
        weight = xr.DataArray(
            cost * MAINTENANCE_RESIDUAL_LOAD_WEIGHT * HOURS_PER_DAY,
            coords={"snapshot": pd.Index(snapshots), "name": names},
            dims=("snapshot", "name"),
        )
        n.model.add_objective(n.model.objective.expression + (weight * maintenance).sum(), overwrite=True)

    options = {"mip_rel_gap": MAINTENANCE_MIP_GAP, "time_limit": MAINTENANCE_TIME_LIMIT_S, "output_flag": False}
    if threads:
        options["threads"] = int(threads)
    status, condition = daily.optimize(solver_name=solver_name, solver_options=options, extra_functionality=tie_break)
    if status != "ok":
        raise OutageError(f"Maintenance scheduling failed: {status} ({condition})")
    schedule = daily.generators_t["maintenance"][units].round().clip(0.0, 1.0)
    schedule.index = pd.DatetimeIndex(schedule.index, name="date")
    schedule.columns = pd.Index(schedule.columns, name="component")
    rows = schedule.reset_index().melt(id_vars="date", var_name="component", value_name="outage_days")
    rows = rows[rows.outage_days > 0]
    rows["kind"] = "planned"
    rows["reason"] = "Maintenance scheduled by PyPSA (maintainable generators)"
    return rows[["component", "date", "kind", "reason", "outage_days"]].reset_index(drop=True)


def apply_outages(
    network: pypsa.Network,
    section: Mapping[str, Any] | None,
    *,
    solver_name: str = "highs",
    threads: int | None = None,
) -> OutageResult | None:
    """Set the hourly ``p_max_pu`` of the coal units from the ``outages`` settings.

    Returns ``None`` when the section is missing or disabled; the network is
    then unchanged.
    """
    if not section or not section.get("enabled"):
        return None
    settings = validate_section(section)
    auxiliary = read_auxiliary(settings["auxiliary"])
    record = read_record(settings["record"])
    units = auxiliary.index.intersection(network.generators.index)
    if units.empty:
        raise OutageError("outages: none of the units in the auxiliary table is in the network")
    snapshots = pd.DatetimeIndex(network.snapshots)
    record = record[record.component.isin(units)]
    period = record[(record.date >= snapshots[0].normalize()) & (record.date <= snapshots[-1].normalize())]
    out_of_service = out_of_service_units(period, snapshots.normalize()) if not period.empty else []
    notes = []

    imposed = [*ALWAYS_IMPOSED]
    if settings["planned"] == "actual":
        imposed.append("planned")
    if settings["forced"] == "actual":
        imposed.append("forced")
    rows = period[period.kind.isin(imposed) & ~period.component.isin(out_of_service)]
    shares = hourly_outage(rows, snapshots)
    if out_of_service:
        shares["out_of_service"] = pd.DataFrame(1.0, index=snapshots, columns=out_of_service)

    if settings["base_availability"] == "net_of_auxiliary":
        base_full = 1.0 - auxiliary[units] / 100.0
    elif settings["base_availability"] == "gross":
        base_full = pd.Series(1.0, index=units)
    else:
        base_full = network.generators.loc[units, "p_max_pu"].astype(float)
    capability = pd.Series(1.0, index=units)
    if settings["partial_derating"] == "demonstrated":
        capability = read_capability(settings["capability"]).reindex(units).fillna(1.0)
    source = pd.Series("outage record", index=units)

    # Central stations: declared capacity (ex-bus MW per station). In the
    # backcast (actual maintenance and forced outages) it replaces the record
    # hour by hour; otherwise one factor per station, declared capacity over
    # the capacity the record leaves available, replaces the capability cap.
    declared = stations = None
    declared_hourly = False
    if settings["central_availability"] == "declared":
        declared, stations = read_declared(settings["declared_capacity"], settings["declared_units"])
        stations = stations[stations.component.isin(units)]
        declared_hourly = settings["planned"] == "actual" and settings["forced"] == "actual"
        # Declared capacity is ex-bus; on a gross basis it is divided by (1 - aux).
        if settings["base_availability"] == "gross":
            to_base = 1.0 / (1.0 - auxiliary / 100.0)
        else:
            to_base = pd.Series(1.0, index=auxiliary.index)
        if not declared_hourly and not stations.empty:
            actual = hourly_outage(period[period.kind != "reserve_shutdown"], snapshots)
            actual_out = _combine(actual, list(actual), snapshots, units)
            for station, members in stations.groupby("station"):
                members = members.set_index("component")
                net = 1.0 - auxiliary[members.index] / 100.0
                available = ((1.0 - actual_out[members.index]) * net * members.unit_rating_mw).sum(axis=1)
                station_mw = declared[station].reindex(snapshots)
                hours = station_mw.notna() & (available > 0)
                factor = station_mw[hours].mean() / available[hours].mean() if hours.any() else 1.0
                capability[members.index] = factor
        source[stations.component] = "declared capacity, " + ("hourly" if declared_hourly else "station factor")
    base = (base_full * capability).clip(upper=1.0)

    # Size classes use the physical unit rating, not the Tamil Nadu share.
    physical = network.generators.loc[units, "p_nom"].astype(float)
    if "capacity_mw" in record.columns and not record.empty:
        physical = record.groupby("component").capacity_mw.first().reindex(units).fillna(physical)
    forced_parameters = None
    if settings["forced"] == "sampled":
        forced_parameters = forced_outage_parameters(record, physical, exclude=out_of_service)
        sampled_units = physical.index.difference(out_of_service)
        drawn = sample_forced_outages(forced_parameters, physical[sampled_units], snapshots, settings["seed"])
        # Draws only count where the unit is not already out for another reason;
        # the rates were estimated on that exposure.
        blocked = _combine(shares, [*imposed, "out_of_service"], snapshots, sampled_units)
        shares["forced"] = drawn.mul(1.0 - blocked)

    schedule = None
    if settings["planned"] == "optimise":
        if settings.get("maintenance_schedule"):
            schedule = read_record(settings["maintenance_schedule"])
            schedule = schedule[schedule.component.isin(units)]
        else:
            # The schedule sees only what is known in advance: no forced outages.
            unavailable = _combine(shares, [*ALWAYS_IMPOSED, "out_of_service"], snapshots, units)
            scheduling = network.copy()
            _set_availability(scheduling, units, base, (1.0 - unavailable).mul(base, axis=1))
            durations = record[record.kind == "planned"].groupby("component").outage_days.sum()
            durations = np.ceil(durations.reindex(units).fillna(0.0).drop(out_of_service, errors="ignore"))
            schedule = schedule_maintenance(scheduling, durations, solver_name=solver_name, threads=threads)
            notes.append(f"Maintenance scheduled by PyPSA for {int((durations >= 1).sum())} units.")
        in_period = (schedule.date >= snapshots[0].normalize()) & (schedule.date <= snapshots[-1].normalize())
        planned_shares = hourly_outage(schedule[in_period].assign(kind="planned"), snapshots).get("planned")
        if planned_shares is not None:
            shares["planned"] = planned_shares

    total = _combine(shares, list(shares), snapshots, units)
    availability = (1.0 - total).mul(base, axis=1)

    if declared_hourly and not stations.empty:
        committable_units = network.generators.loc[units, "committable"].astype(bool)
        for station, members in stations.groupby("station"):
            components = list(members.component)
            station_mw = declared[station].reindex(snapshots)
            if station_mw.isna().any():
                raise OutageError(f"Declared capacity of {station} misses {int(station_mw.isna().sum())} hours")
            rating = float(members.unit_rating_mw.iloc[0])
            p_min = network.generators.loc[components, "p_min_pu"].astype(float).max()
            if not committable_units[components].any():
                p_min = 0.0
            mw = allocate_declared(station_mw, rating, 1.0 - total[components], p_min)
            availability[components] = (mw / rating).mul(to_base[components], axis=1).clip(upper=1.0)

    p_min_scale = None
    if settings["resolution"] != "hourly":
        commissioned = 1.0 - _combine(shares, ["commissioning"], snapshots, units)
        availability, p_min_scale = smooth_availability(availability, commissioned, settings["resolution"])
        notes.append(f"Availability averaged per {'month' if settings['resolution'] == 'monthly' else 'year'}.")
    _set_availability(network, units, base, availability, p_min_scale)

    # Units out at the first hour start the horizon offline instead of being
    # shut down (and paying the shut-down cost) in it.
    committable = network.generators.loc[units, "committable"].astype(bool)
    initial_off = units[committable.to_numpy() & (availability.iloc[0] <= 1e-9).to_numpy()]
    if len(initial_off):
        network.generators.loc[initial_off, "up_time_before"] = 0
        network.generators.loc[initial_off, "down_time_before"] = 168
        notes.append(f"{len(initial_off)} unit(s) out in the first hour start the horizon offline.")

    summary = pd.DataFrame(index=units)
    summary.index.name = "component"
    summary["p_nom_mw"] = network.generators.loc[units, "p_nom"]
    summary["availability_source"] = source
    summary["auxiliary_consumption_pct"] = auxiliary[units]
    summary["capability_pu"] = capability
    summary["base_availability"] = base
    for kind in ("planned", "forced", "commercial", "commissioning", "out_of_service"):
        frame = shares.get(kind)
        summary[f"{kind}_hours"] = frame.reindex(columns=units, fill_value=0.0).sum() if frame is not None else 0.0
    summary["mean_availability"] = availability.mean()
    return OutageResult(units=summary, forced_parameters=forced_parameters, maintenance_schedule=schedule, notes=notes)


def _combine(
    shares: Mapping[str, pd.DataFrame], kinds: list[str], snapshots: pd.DatetimeIndex, units: pd.Index
) -> pd.DataFrame:
    """Share of each hour each unit is out for the given kinds, at most 1."""
    total = pd.DataFrame(0.0, index=snapshots, columns=units)
    for kind in kinds:
        if kind in shares:
            total = total.add(shares[kind].reindex(index=snapshots, columns=units, fill_value=0.0), fill_value=0.0)
    return total.clip(upper=1.0)


def _set_availability(
    network: pypsa.Network,
    units: pd.Index,
    base: pd.Series,
    availability: pd.DataFrame,
    p_min_scale: pd.DataFrame | None = None,
) -> None:
    """Static p_max_pu = base; hourly p_max_pu = availability.

    With ``p_min_scale`` the units' minimum load is scaled by it hour by hour
    (smoothed availability treats a partly available unit as a smaller one).
    """
    network.generators.loc[units, "p_max_pu"] = base[units].to_numpy()
    _set_series(network, "p_max_pu", availability.reindex(index=network.snapshots, columns=units, fill_value=0.0))
    if p_min_scale is not None:
        p_min = network.generators.loc[units, "p_min_pu"].astype(float)
        scaled = p_min[p_min > 0].index
        if len(scaled):
            frame = p_min_scale.reindex(index=network.snapshots, columns=scaled).mul(p_min[scaled], axis=1)
            _set_series(network, "p_min_pu", frame)


def _set_series(network: pypsa.Network, attribute: str, frame: pd.DataFrame) -> None:
    """Replace the given units' columns of a generator time series."""
    existing = network.generators_t[attribute]
    combined = pd.concat([existing.drop(columns=frame.columns, errors="ignore"), frame], axis=1)
    combined.columns.name = existing.columns.name
    combined.index.name = existing.index.name
    network.generators_t[attribute] = combined


def write_outputs(result: OutageResult, output_dir: Path) -> None:
    """Save what was imposed next to the run's results."""
    output_dir.mkdir(parents=True, exist_ok=True)
    result.units.to_csv(output_dir / "coal_unit_availability.csv")
    if result.forced_parameters is not None:
        result.forced_parameters.drop(columns="durations_h").to_csv(output_dir / "forced_outage_parameters.csv")
    if result.maintenance_schedule is not None:
        schedule = result.maintenance_schedule.copy()
        schedule["date"] = pd.to_datetime(schedule.date).dt.strftime("%Y-%m-%d")
        schedule.to_csv(output_dir / "maintenance_schedule.csv", index=False)
