"""Add scenario technologies (batteries, offshore wind) to the base network.

The ``technologies`` section of a run or scenario config (see
``config/defaults.yaml``) lists the sites of each technology. Each site's
``capacity_mw`` is either an installed capacity in MW (fixed) or ``optimise``,
which makes the capacity a decision of a capacity-expansion run, optionally
bounded by ``min_mw`` / ``max_mw``. Technology parameters (efficiencies,
duration, costs, profiles) come from the data file named by
``technologies.data`` (``config/technologies.yaml``), with per-scenario changes
in ``technologies.parameters``.

Optimised capacities need one continuous solve over the year (``run.mode:
annual``): rolling, monthly and selected-week runs solve periods separately
and would size each period differently. The Snakemake workflow therefore
solves an annual capacity-expansion LP first, saves the optimised capacities
(``write_optimised_capacities``) and passes them as
``technologies.fixed_capacities`` to the operational run, where they replace
``optimise``.

Added components carry a ``scenario_site`` column with their site name, so the
optimised capacities can be read back from a solved network.
"""

from __future__ import annotations

import copy
import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import pypsa
import yaml

ROOT = Path(__file__).resolve().parents[1]
OPTIMISE = "optimise"
SITE_KEYS = {"bus", "capacity_mw", "min_mw", "max_mw", "profile"}
TECHNOLOGY_COMPONENT = {"battery": "StorageUnit", "offshore_wind": "Generator"}
TECHNOLOGIES = tuple(TECHNOLOGY_COMPONENT)
# Parameters each technology needs whenever it has a site, and in addition
# when any of its sites is optimised.
REQUIRED_PARAMETERS = {
    "battery": ("max_hours", "efficiency_store", "efficiency_dispatch"),
    "offshore_wind": ("profiles",),
}
REQUIRED_FOR_OPTIMISATION = ("capital_cost",)
FIXED_CAPACITY_COLUMNS = ["technology", "site", "bus", "capacity_mw"]


class TechnologyError(ValueError):
    """Raised when the technologies settings or data are incomplete or invalid."""


@dataclass(frozen=True)
class Site:
    technology: str
    name: str
    bus: str
    capacity_mw: float | None  # None when optimised
    min_mw: float
    max_mw: float
    profile: str

    @property
    def optimise(self) -> bool:
        return self.capacity_mw is None

    @property
    def component_name(self) -> str:
        return f"{self.technology} {self.name}"


def _resolve(path: str | Path) -> Path:
    path = Path(path).expanduser()
    return path if path.is_absolute() else ROOT / path


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TechnologyError(f"{label} must be a number, got {value!r}")
    if not math.isfinite(float(value)) or float(value) < 0:
        raise TechnologyError(f"{label} must be a finite, non-negative number")
    return float(value)


def parse_sites(section: Mapping[str, Any]) -> list[Site]:
    """Validate the site lists of a ``technologies`` section and return them."""
    sites: list[Site] = []
    for technology in TECHNOLOGIES:
        entries = section.get(technology) or {}
        if not isinstance(entries, Mapping):
            raise TechnologyError(
                f"technologies.{technology} must map site names to settings"
            )
        for name, settings in entries.items():
            label = f"technologies.{technology}.{name}"
            if not isinstance(settings, Mapping):
                raise TechnologyError(f"{label} must be a mapping of settings")
            unknown = set(settings) - SITE_KEYS
            if unknown:
                raise TechnologyError(
                    f"{label}: unknown setting(s) {sorted(unknown)}; known: {sorted(SITE_KEYS)}"
                )
            if "capacity_mw" not in settings:
                raise TechnologyError(f"{label}: set capacity_mw to MW or '{OPTIMISE}'")
            capacity = settings["capacity_mw"]
            optimise = isinstance(capacity, str) and capacity.strip().casefold() == OPTIMISE
            if optimise:
                capacity_mw = None
                min_mw = _number(settings.get("min_mw", 0.0), f"{label}.min_mw")
                max_raw = settings.get("max_mw")
                max_mw = math.inf if max_raw is None else _number(max_raw, f"{label}.max_mw")
                if min_mw > max_mw:
                    raise TechnologyError(f"{label}: min_mw is larger than max_mw")
            else:
                capacity_mw = _number(capacity, f"{label}.capacity_mw")
                if "min_mw" in settings or "max_mw" in settings:
                    raise TechnologyError(
                        f"{label}: min_mw / max_mw apply only with capacity_mw: {OPTIMISE}"
                    )
                min_mw = max_mw = capacity_mw
            if "profile" in settings and technology != "offshore_wind":
                raise TechnologyError(f"{label}: profile applies to offshore_wind only")
            sites.append(
                Site(
                    technology=technology,
                    name=str(name),
                    bus=str(settings.get("bus", name)),
                    capacity_mw=capacity_mw,
                    min_mw=min_mw,
                    max_mw=max_mw,
                    profile=str(settings.get("profile", name)),
                )
            )
    return sites


def parse_totals(section: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    """Validate ``technologies.totals``: total optimised MW per technology."""
    totals = section.get("totals") or {}
    if not isinstance(totals, Mapping):
        raise TechnologyError("technologies.totals must map technologies to limits")
    parsed: dict[str, dict[str, float]] = {}
    for technology, limits in totals.items():
        label = f"technologies.totals.{technology}"
        if technology not in TECHNOLOGIES:
            raise TechnologyError(f"{label}: unknown technology; known: {list(TECHNOLOGIES)}")
        if not isinstance(limits, Mapping) or set(limits) - {"min_mw", "max_mw"}:
            raise TechnologyError(f"{label} must set min_mw and/or max_mw")
        parsed[technology] = {
            key: _number(value, f"{label}.{key}")
            for key, value in limits.items()
            if value is not None
        }
        bounds = parsed[technology]
        if bounds.get("min_mw", 0.0) > bounds.get("max_mw", math.inf):
            raise TechnologyError(f"{label}: min_mw is larger than max_mw")
    return parsed


def read_fixed_capacities(path: str | Path | None) -> dict[tuple[str, str], float]:
    """Read optimised capacities written by :func:`write_optimised_capacities`."""
    if path is None:
        return {}
    resolved = _resolve(path)
    if not resolved.exists():
        raise TechnologyError(f"technologies.fixed_capacities does not exist: {resolved}")
    table = pd.read_csv(resolved)
    missing = set(FIXED_CAPACITY_COLUMNS) - set(table.columns)
    if missing:
        raise TechnologyError(f"{resolved}: missing column(s) {sorted(missing)}")
    return {
        (str(row.technology), str(row.site)): float(row.capacity_mw)
        for row in table.itertuples()
    }


def resolve_sites(section: Mapping[str, Any]) -> list[Site]:
    """Return the sites with any fixed (previously optimised) capacities applied."""
    fixed = read_fixed_capacities(section.get("fixed_capacities"))
    resolved = []
    for site in parse_sites(section):
        key = (site.technology, site.name)
        if site.optimise and key in fixed:
            capacity = fixed[key]
            site = Site(site.technology, site.name, site.bus, capacity, capacity, capacity, site.profile)
        resolved.append(site)
    return resolved


def needs_capacity_expansion(section: Mapping[str, Any]) -> bool:
    """Whether any site is still ``optimise`` after applying fixed capacities."""
    return any(site.optimise for site in resolve_sites(section))


def has_optimised_sites(section: Mapping[str, Any]) -> bool:
    """Whether the scenario asks the model to optimise any capacity."""
    return any(site.optimise for site in parse_sites(section))


def validate_section(section: Mapping[str, Any]) -> None:
    """Check the structure of a ``technologies`` section (no network needed)."""
    known = {"data", "parameters", "totals", "fixed_capacities", *TECHNOLOGIES}
    unknown = set(section) - known
    if unknown:
        raise TechnologyError(
            f"technologies: unknown key(s) {sorted(unknown)}; known: {sorted(known)}"
        )
    if not isinstance(section.get("parameters") or {}, Mapping):
        raise TechnologyError("technologies.parameters must map technologies to parameters")
    parse_sites(section)
    parse_totals(section)
    read_fixed_capacities(section.get("fixed_capacities"))


def _deep_merge(base: dict, override: Mapping) -> dict:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_parameters(section: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return technology parameters: the data file plus scenario changes."""
    data_path = section.get("data")
    data: dict = {}
    if data_path:
        resolved = _resolve(data_path)
        if not resolved.exists():
            raise TechnologyError(f"technologies.data does not exist: {resolved}")
        data = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    return _deep_merge(data, section.get("parameters") or {})


def _check_parameters(sites: list[Site], parameters: Mapping[str, Mapping[str, Any]]) -> None:
    missing = []
    for technology in TECHNOLOGIES:
        used = [site for site in sites if site.technology == technology]
        if not used:
            continue
        values = parameters.get(technology) or {}
        required = list(REQUIRED_PARAMETERS[technology])
        if any(site.optimise for site in used):
            required += REQUIRED_FOR_OPTIMISATION
        missing += [f"{technology}.{key}" for key in required if values.get(key) is None]
    if missing:
        raise TechnologyError(
            "Technology data are missing: "
            + ", ".join(missing)
            + ". Set them in config/technologies.yaml or in the scenario's "
            "technologies.parameters."
        )


def _read_profiles(path: str | Path, snapshots: pd.DatetimeIndex, columns: list[str]) -> pd.DataFrame:
    resolved = _resolve(path)
    if not resolved.exists():
        raise TechnologyError(f"Offshore wind profile file does not exist: {resolved}")
    profiles = pd.read_csv(resolved, index_col=0, parse_dates=True)
    missing_columns = sorted(set(columns) - set(profiles.columns))
    if missing_columns:
        raise TechnologyError(f"{resolved}: no profile column(s) {missing_columns}")
    profiles.index = pd.DatetimeIndex(profiles.index)
    missing_hours = snapshots.difference(profiles.index)
    if len(missing_hours):
        raise TechnologyError(
            f"{resolved}: profiles miss {len(missing_hours)} model hour(s), "
            f"first {missing_hours[0]}"
        )
    selected = profiles.loc[snapshots, columns].astype(float)
    if selected.isna().any().any() or (selected < 0).any().any() or (selected > 1).any().any():
        raise TechnologyError(f"{resolved}: profiles must be within 0..1 with no gaps")
    return selected


def _ensure_carrier(network: pypsa.Network, carrier: str) -> None:
    if carrier not in network.carriers.index:
        network.add("Carrier", carrier, co2_emissions=0.0)


def apply_technologies(
    network: pypsa.Network, section: Mapping[str, Any] | None
) -> pd.DataFrame:
    """Add the configured technologies to *network* and return what was added."""
    if not section:
        return pd.DataFrame()
    validate_section(section)
    sites = resolve_sites(section)
    if not sites:
        return pd.DataFrame()
    parameters = load_parameters(section)
    _check_parameters(sites, parameters)

    unknown_buses = sorted({site.bus for site in sites} - set(network.buses.index))
    if unknown_buses:
        raise TechnologyError(
            f"Unknown bus(es) {unknown_buses}; sites connect to a balancing area such as "
            f"{list(network.buses.index[network.buses.carrier.eq('AC')][:3])}"
        )
    for technology in TECHNOLOGIES:
        allowed = (parameters.get(technology) or {}).get("allowed_buses")
        if allowed is None:
            continue
        outside = sorted(
            {site.bus for site in sites if site.technology == technology} - set(map(str, allowed))
        )
        if outside:
            raise TechnologyError(
                f"{technology} can connect only to {list(allowed)} "
                f"({technology}.allowed_buses); not to {outside}"
            )
    duplicate = [site.component_name for site in sites if site.component_name in (
        network.storage_units.index if site.technology == "battery" else network.generators.index
    )]
    if duplicate:
        raise TechnologyError(f"Components already exist: {duplicate}")

    rows = []
    for technology in TECHNOLOGIES:
        used = [site for site in sites if site.technology == technology]
        if not used:
            continue
        values = parameters[technology]
        carrier = str(values.get("carrier", technology))
        _ensure_carrier(network, carrier)
        names = [site.component_name for site in used]
        common = {
            "bus": [site.bus for site in used],
            "carrier": carrier,
            "p_nom": [site.min_mw if site.optimise else site.capacity_mw for site in used],
            "p_nom_extendable": [site.optimise for site in used],
            "p_nom_min": [site.min_mw for site in used],
            "p_nom_max": [site.max_mw for site in used],
            "capital_cost": float(values.get("capital_cost") or 0.0),
            "marginal_cost": float(values.get("marginal_cost") or 0.0),
            "scenario_site": [site.name for site in used],
        }
        if technology == "battery":
            network.add(
                "StorageUnit",
                names,
                max_hours=float(values["max_hours"]),
                efficiency_store=float(values["efficiency_store"]),
                efficiency_dispatch=float(values["efficiency_dispatch"]),
                standing_loss=float(values.get("standing_loss") or 0.0),
                cyclic_state_of_charge=True,
                **common,
            )
        else:
            profiles = _read_profiles(
                values["profiles"], pd.DatetimeIndex(network.snapshots),
                sorted({site.profile for site in used}),
            )
            p_max_pu = pd.DataFrame(
                {site.component_name: profiles[site.profile].to_numpy() for site in used},
                index=network.snapshots,
            )
            network.add("Generator", names, p_max_pu=p_max_pu, committable=False, **common)
        for site in used:
            rows.append(
                {
                    "technology": technology,
                    "site": site.name,
                    "component": site.component_name,
                    "bus": site.bus,
                    "capacity_mw": site.capacity_mw,
                    "optimise": site.optimise,
                    "min_mw": site.min_mw,
                    "max_mw": site.max_mw,
                }
            )

    for technology, bounds in parse_totals(section).items():
        carrier = str(parameters.get(technology, {}).get("carrier", technology))
        if not any(site.optimise and site.technology == technology for site in sites):
            continue
        for key, sense in (("min_mw", ">="), ("max_mw", "<=")):
            if key in bounds:
                network.add(
                    "GlobalConstraint",
                    f"{technology}_total_{key}",
                    type="tech_capacity_expansion_limit",
                    carrier_attribute=carrier,
                    sense=sense,
                    constant=bounds[key],
                )
    return pd.DataFrame(rows)


def optimised_capacities(network: pypsa.Network) -> pd.DataFrame:
    """Return the capacities chosen for optimised sites of a solved network."""
    rows = []
    for technology, component in TECHNOLOGY_COMPONENT.items():
        static = network.components[component].static
        if "scenario_site" not in static:
            continue
        selected = static.loc[
            static.scenario_site.notna()
            & static.scenario_site.astype(str).ne("")
            & static.p_nom_extendable.astype(bool)
            & static.index.str.startswith(f"{technology} ")
        ]
        for name, row in selected.iterrows():
            rows.append(
                {
                    "technology": technology,
                    "site": row.scenario_site,
                    "bus": row.bus,
                    "capacity_mw": float(row.p_nom_opt),
                }
            )
    return pd.DataFrame(rows, columns=FIXED_CAPACITY_COLUMNS)


def write_optimised_capacities(network: pypsa.Network, path: str | Path) -> pd.DataFrame:
    """Save the optimised capacities in the ``technologies.fixed_capacities`` format."""
    table = optimised_capacities(network)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(path, index=False)
    return table


def network_fingerprint(network: pypsa.Network) -> str:
    """Return a content hash of a network's components and time series.

    Used to decide whether checkpoints of an earlier run were solved with the
    same network and can be reused.
    """
    digest = hashlib.sha256()
    digest.update(pd.util.hash_pandas_object(pd.Series(network.snapshots), index=False).values.tobytes())
    for component in network.components:
        static = component.static
        if len(static):
            digest.update(component.name.encode())
            digest.update(static.sort_index(axis=1).to_csv().encode())
        for attribute, frame in sorted(component.dynamic.items()):
            if frame.empty:
                continue
            frame = frame.sort_index(axis=1)
            digest.update(f"{component.name}.{attribute}".encode())
            digest.update(",".join(map(str, frame.columns)).encode())
            digest.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())
    return digest.hexdigest()
