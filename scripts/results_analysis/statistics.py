"""PyPSA ``n.statistics`` for single networks and rolling checkpoint series.

``n.statistics`` works on one network. A rolling run is stored as one network
per committed week, so time-varying statistics are evaluated per network with
``groupby_time=False``, joined along the time axis and then weighted with the
run's snapshot weightings. Static statistics, such as installed capacity, are
taken from the first network, as the rest of the analysis does.

Every statistic is restricted to the electricity buses (``bus_carrier="AC"``).
The model also has a water-energy layer: reservoir stores, natural inflow
"generators", spill and transfer links. Those flows are hydro *inputs*; the
electricity they produce is already counted at the turbine links' grid side.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd
import pypsa

from .config import CARRIER_ORDER, canonical_carrier
from .loaders import AnalysisRun

ELECTRICITY_BUS_CARRIER = "AC"

# Components counted as electricity generation. Lines, transmission links and
# negative loads also "supply" a bus in PyPSA's energy balance; they are
# transfers, not production, and are reported by the transmission metrics.
GENERATING_COMPONENTS = ("Generator", "StorageUnit", "Link")
GENERATING_LINK_CARRIERS = frozenset({"hydro_turbine"})

# Links that move electricity between areas: the AC ties and HVDC links. Lines
# are always transmission.
TRANSMISSION_LINK_CARRIERS = frozenset({"AC", "DC"})


def network_statistic(
    network: pypsa.Network, name: str, **kwargs
) -> pd.DataFrame | pd.Series:
    """Call ``network.statistics.<name>`` at the electricity buses.

    Groups keep the model's carrier names rather than the carriers'
    ``nice_name``, so they map onto the reporting taxonomy.
    """
    kwargs.setdefault("bus_carrier", ELECTRICITY_BUS_CARRIER)
    kwargs.setdefault("nice_names", False)
    return getattr(network.statistics, name)(**kwargs)


def statistic_timeseries(run: AnalysisRun, name: str, **kwargs) -> pd.DataFrame:
    """Return an ``n.statistics`` metric per snapshot over all retained networks.

    The result has one row per run snapshot and one column per group (for
    example ``(component, carrier)``). Groups absent from a network are zero
    in its snapshots.
    """
    parts: list[pd.DataFrame] = []
    for network in run.networks:
        values = network_statistic(network, name, groupby_time=False, **kwargs)
        if values.empty:
            continue
        part = values.T
        part.index = pd.DatetimeIndex(part.index)
        parts.append(part)
    if not parts:
        return pd.DataFrame(index=run.snapshots)
    combined = pd.concat(parts).sort_index()
    combined = combined.loc[~combined.index.duplicated(keep="first")]
    return combined.reindex(run.snapshots).fillna(0.0)


def statistic_total(run: AnalysisRun, name: str, **kwargs) -> pd.Series:
    """Return a time-varying metric summed over the run with snapshot weights."""
    hourly = statistic_timeseries(run, name, **kwargs)
    return hourly.mul(run.weights, axis=0).sum()


def is_transmission(components: pd.Index, carriers: pd.Index) -> pd.Index:
    """Flag Line and AC/DC Link groups, given their component and carrier."""
    return components.isin(["Line"]) | (
        components.isin(["Link"])
        & carriers.astype(str).isin(TRANSMISSION_LINK_CARRIERS)
    )


def _generating_rows(index: pd.MultiIndex) -> pd.Index:
    """Select generating ``(component, ..., carrier)`` groups from *index*."""
    if "component" not in index.names:
        # PyPSA drops the component level when one component was requested;
        # callers that do so already chose a generating component.
        return index
    components = index.get_level_values("component")
    carriers = index.get_level_values("carrier").astype(str)
    keep = components.isin(GENERATING_COMPONENTS) & (
        ~components.isin(["Link"]) | carriers.isin(GENERATING_LINK_CARRIERS)
    )
    return index[keep]


def to_report_carriers(
    values: pd.Series | pd.DataFrame,
    levels: Sequence[str] = (),
    axis: int = 0,
) -> pd.Series | pd.DataFrame:
    """Keep generating groups and collapse them to the reporting carriers.

    *levels* names extra group levels, such as ``"bus"``, to keep alongside
    the reporting carrier.
    """
    frame = values if axis == 0 else values.T
    frame = frame.loc[_generating_rows(frame.index)]
    keys = [frame.index.get_level_values(level) for level in levels]
    keys.append(
        frame.index.get_level_values("carrier").map(canonical_carrier).rename("carrier")
    )
    grouped = frame.groupby(keys).sum()
    return grouped if axis == 0 else grouped.T


def order_carriers(frame: pd.DataFrame) -> pd.DataFrame:
    """Order carrier columns as in the reports; unknown carriers go last."""
    ordered = [carrier for carrier in CARRIER_ORDER if carrier in frame]
    extras = sorted(set(frame.columns) - set(ordered))
    return frame.reindex(columns=ordered + extras, fill_value=0.0)
