"""Tamil Nadu's import capability as Grid-India declares it.

Grid-India declares one simultaneous limit on Tamil Nadu's drawal from the
interstate grid each month: the total transfer capability (TTC), less a
reliability margin (RM) for the available transfer capability (ATC). The value
is the same in all 96 blocks of a month; the limiting elements are N-1 of the
400/230 kV transformers at Salem, Kalivandapattu, Pugalur, NLC TS-II and
NNTPS (data/source/grid_india_ttc_fy2025_26).

The interstate ties of the built network are rated per circuit and sum to
about 51.5 GW into Tamil Nadu, so on their own they never limit the import.
With ``interstate.drawal_limit`` set, every solve adds, for each hour,

    net import delivered over the ties
    + output of the interstate-connected stations inside Tamil Nadu
    <= limit - interstate-connected renewables inside Tamil Nadu

which is Grid-India's drawal: what flows from the interstate grid into the
state grid. The stations are the central stations with their other states'
shares (Vallur, NTPL, NNTPS, NLC TS-II and the NLC expansions, Kudankulam,
MAPS) and the two IPPs on interstate pooling stations (Coastal Energen,
IL&FS). The interstate-connected wind and solar that sells to other states is
not separate in the network, so its metered output is taken as given
(``interstate_re_mw`` of the metered interchange). This decomposition is the
one that reproduces the metered drawal (prepare_metered_interchange.py).

``ttc`` (the default) limits the actual flow, which in real time may use the
reliability margin: the metered drawal of FY2025-26 peaks at 14,240 MW, above
the ATC in 10 hours and above the TTC in 2. ``atc`` limits it to the
schedulable value. Each month takes its latest declared revision; months
without a declaration take the previous month's.

With fixed interstate schedules the constraint limits only the dispatch of
the interstate-connected stations; with traded imports it also limits the
trades.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
import pypsa
import xarray as xr

ROOT = Path(__file__).resolve().parents[1]
META_KEY = "interstate_drawal_limit"
CONSTRAINT = "tamil_nadu_drawal_limit"
LIMITS = ("ttc", "atc", "none")
SECTION_KEYS = {"drawal_limit", "transfer_capability", "metered_interchange"}
DEFAULTS = {
    "drawal_limit": "ttc",
    "transfer_capability": "data/source/grid_india_ttc_fy2025_26/transfer_capability_fy2025_26.csv",
    "metered_interchange": "data/model_inputs/tn_metered_interchange_FY2025_26.csv",
}
AREA = "Tamil Nadu"
TN_KINDS = ("TN balancing area", "TN balancing area (400 kV)")
NEIGHBOUR_KINDS = ("neighbouring state", "neighbouring region")
# Interstate-connected stations inside Tamil Nadu: Tamil Nadu's units and the
# other states' export reservations of the central stations, and the IPPs on
# interstate pooling stations.
STATIONS = (
    r"^(?:vallur_tpp|tuticorin_joint_venture|neyveli_new_tpp|neyveli_tps_ii__|neyveli_ext_tps|"
    r"kudankulam_atomic_power_project__|madras_atomic_power_station__|muthiara_tpp__|itpcl_)"
    r"|^rea_export_reservation__(?:ntecl_vallur|nlc_tamil_nadu_power|neyveli_|kudankulam_|madras_atomic)"
)


class DrawalLimitError(ValueError):
    """Invalid drawal-limit settings or inputs."""


def _resolve(path: str | Path) -> Path:
    path = Path(path).expanduser()
    return path if path.is_absolute() else ROOT / path


def validate_section(section: Mapping[str, Any] | None) -> dict[str, Any]:
    section = {**DEFAULTS, **(section or {})}
    unknown = set(section) - SECTION_KEYS
    if unknown:
        raise DrawalLimitError(f"Unknown interstate keys {sorted(unknown)}; known: {sorted(SECTION_KEYS)}")
    if section["drawal_limit"] not in LIMITS:
        raise DrawalLimitError(f"interstate.drawal_limit must be one of {list(LIMITS)}")
    return section


@lru_cache(maxsize=4)
def monthly_limits(path: str, basis: str) -> pd.Series:
    """Declared limit per month (MW): latest revision; one value per month."""
    table = pd.read_csv(_resolve(path))
    table = table[table.area.eq(AREA) & table.latest.astype(bool)]
    column = f"{basis}_mw"
    values = table.groupby("month")[column].agg(["min", "max"])
    varying = values.index[values["min"] != values["max"]]
    if len(varying):
        raise DrawalLimitError(f"{AREA} {basis.upper()} varies within month(s) {list(varying)}")
    limits = values["min"].astype(float)
    limits.index = pd.PeriodIndex(limits.index, freq="M")
    return limits


@lru_cache(maxsize=4)
def _interstate_re(path: str) -> pd.Series:
    metered = pd.read_csv(_resolve(path), parse_dates=["snapshot"]).set_index("snapshot")
    return metered["interstate_re_mw"].astype(float)


def limit_mw(snapshots: pd.DatetimeIndex, settings: Mapping[str, Any]) -> pd.Series:
    """Hourly declared limit; a month without a declaration takes the previous (or next) month's."""
    snapshots = pd.DatetimeIndex(snapshots)
    limits = monthly_limits(str(settings["transfer_capability"]), settings["drawal_limit"])
    months = pd.period_range(min(limits.index.min(), snapshots.min().to_period("M")),
                             max(limits.index.max(), snapshots.max().to_period("M")), freq="M")
    limits = limits.reindex(months).ffill().bfill()
    return pd.Series(limits.reindex(snapshots.to_period("M")).to_numpy(), index=snapshots, name="limit_mw")


def interstate_re_mw(snapshots: pd.DatetimeIndex, settings: Mapping[str, Any]) -> pd.Series:
    series = _interstate_re(str(settings["metered_interchange"])).reindex(pd.DatetimeIndex(snapshots))
    if series.isna().any():
        raise DrawalLimitError(f"The metered interchange has no interstate RE for {int(series.isna().sum())} snapshot(s)")
    return series.rename("interstate_re_mw")


def ties(network: pypsa.Network) -> tuple[pd.Index, pd.Index]:
    """Interstate links into and out of Tamil Nadu."""
    kind = network.buses.kind.astype(str)
    tn = kind.index[kind.isin(TN_KINDS)]
    neighbours = kind.index[kind.isin(NEIGHBOUR_KINDS)]
    links = network.links
    into = links.index[links.bus0.isin(neighbours) & links.bus1.isin(tn)]
    out = links.index[links.bus0.isin(tn) & links.bus1.isin(neighbours)]
    return into, out


def stations(network: pypsa.Network) -> pd.Index:
    return network.generators.index[network.generators.index.str.contains(STATIONS, regex=True)]


def fixed_drawal_mw(network: pypsa.Network) -> pd.Series:
    """Lowest drawal the fixed inputs allow: the neighbours' fixed net supply
    delivered without circulation, plus the export reservations' pinned output.
    Committable units can be off, so their minimum load is not counted."""
    into, out = ties(network)
    efficiency = network.links.efficiency
    neighbours = set(network.links.loc[into, "bus0"]) | set(network.links.loc[out, "bus1"])
    loads = network.loads.index[network.loads.bus.isin(neighbours)]
    supply = -network.loads_t.p_set.reindex(columns=loads).fillna(network.loads.loc[loads, "p_set"])
    supply = supply.T.groupby(network.loads.loc[loads, "bus"]).sum().T
    eta_in = efficiency[into].groupby(network.links.loc[into, "bus0"]).min()
    eta_out = efficiency[out].groupby(network.links.loc[out, "bus1"]).min()
    delivered = (supply.clip(lower=0) * eta_in.reindex(supply.columns)
                 + supply.clip(upper=0) / eta_out.reindex(supply.columns)).sum(axis=1)
    pinned = stations(network)
    pinned = pinned[~network.generators.loc[pinned, "committable"].astype(bool)]
    p_min_pu = network.generators_t.p_min_pu.reindex(columns=pinned).fillna(network.generators.loc[pinned, "p_min_pu"])
    return delivered + p_min_pu.mul(network.generators.loc[pinned, "p_nom"]).sum(axis=1)


def apply_drawal_limit(network: pypsa.Network, settings: Mapping[str, Any] | None) -> pd.DataFrame:
    """Store the limit in ``network.meta`` for the solves; returns its hourly report.

    The report is empty when the limit is off: ``drawal_limit: none``, or no
    settings at all (direct callers; the run config's defaults switch it on).
    """
    if settings is None:
        return pd.DataFrame()
    settings = validate_section(settings)
    if settings["drawal_limit"] == "none":
        return pd.DataFrame()
    into, out = ties(network)
    if not len(into) or not len(out):
        raise DrawalLimitError("The network has no interstate links into and out of Tamil Nadu")
    if len(stations(network)) == 0:
        raise DrawalLimitError("No interstate-connected stations found in the network")
    snapshots = network.snapshots
    report = pd.DataFrame({
        "limit_mw": limit_mw(snapshots, settings),
        "interstate_re_mw": interstate_re_mw(snapshots, settings),
        "fixed_drawal_mw": fixed_drawal_mw(network),
    })
    report["headroom_mw"] = report.limit_mw - report.interstate_re_mw - report.fixed_drawal_mw
    short = report.index[report.headroom_mw < 0]
    if len(short):
        raise DrawalLimitError(
            f"The fixed interstate schedule alone exceeds the {settings['drawal_limit'].upper()} in "
            f"{len(short)} hour(s), first {short[0]}; the drawal limit would be infeasible"
        )
    network.meta = {**(network.meta or {}), META_KEY: {key: str(value) for key, value in settings.items()}}
    return report


def tag(network: pypsa.Network) -> str | None:
    spec = (network.meta or {}).get(META_KEY)
    return f"drawal-{spec['drawal_limit']}" if spec else None


def add_drawal_limit(network: pypsa.Network, snapshots: pd.DatetimeIndex) -> None:
    """extra_functionality hook: Tamil Nadu's drawal <= declared limit in every snapshot."""
    spec = (network.meta or {}).get(META_KEY)
    if not spec:
        return
    snapshots = pd.DatetimeIndex(snapshots)
    into, out = ties(network)
    if not network.links_t.efficiency.reindex(columns=into).dropna(axis=1, how="all").empty:
        raise DrawalLimitError("Time-varying tie efficiencies are not supported by the drawal limit")
    model = network.model
    link_p = model["Link-p"]
    generator_p = model["Generator-p"]
    link_dim = next(d for d in link_p.dims if d != "snapshot")
    generator_dim = next(d for d in generator_p.dims if d != "snapshot")
    efficiency = xr.DataArray(network.links.loc[into, "efficiency"].to_numpy(), dims=[link_dim],
                              coords={link_dim: into})
    drawal = (
        (link_p.sel({link_dim: into}) * efficiency).sum(link_dim)
        - link_p.sel({link_dim: out}).sum(link_dim)
        + generator_p.sel({generator_dim: stations(network)}).sum(generator_dim)
    ).sel(snapshot=snapshots)
    rhs = limit_mw(snapshots, spec) - interstate_re_mw(snapshots, spec)
    model.add_constraints(
        drawal <= xr.DataArray(rhs.to_numpy(), dims=["snapshot"], coords={"snapshot": snapshots}),
        name=CONSTRAINT,
    )


def drawal_mw(network: pypsa.Network) -> pd.Series:
    """Drawal of a solved network, as constrained (including the interstate RE)."""
    into, out = ties(network)
    spec = (network.meta or {}).get(META_KEY) or DEFAULTS
    delivered = network.links_t.p0[into].mul(network.links.loc[into, "efficiency"]).sum(axis=1)
    sent = network.links_t.p0[out].sum(axis=1)
    output = network.generators_t.p.reindex(columns=stations(network)).sum(axis=1)
    return (delivered - sent + output + interstate_re_mw(network.snapshots, spec)).rename("drawal_mw")
