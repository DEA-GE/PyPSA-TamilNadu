"""Shared economic and loss assumptions for electrical transmission."""

from __future__ import annotations

import pandas as pd
import pypsa
import xarray as xr


# This is a numerical routing tie-breaker, not a regulated tariff. Applying the
# system-level TANTRANSCO tariff to every traversed internal line would charge a
# multi-line path repeatedly.
TRANSMISSION_FLOW_COST_PER_MWH = 1.0

# TNERC's FY2025-26 short-term open-access transmission charge is
# Rs 241.81/MW/hour, numerically equivalent to Rs 241.81/MWh for energy flow.
# Source: https://tnerc.tn.gov.in/Orders/files/TO-Intra-Stat010720251711.pdf
INTERSTATE_FLOW_COST_PER_MWH = 241.81

# Approximate passive-branch I^2R losses with two linear tangent segments.
# The underlying resistance comes from the imported AC-line data.
TRANSMISSION_LOSSES = {"mode": "tangents", "segments": 2}

# CEA's 20th Electric Power Survey assumes 3% inter-state transmission losses
# on energy imported from the national grid. Directional interconnector Links
# therefore deliver 97% of their sending-end power.
# Source: CEA, 20th Electric Power Survey, Volume I, section 1.4.6.
INTERCONNECTOR_EFFICIENCY = 0.97


def interconnector_efficiency(length_km: pd.Series) -> pd.Series:
    """Return the CEA planning efficiency for interstate grid Links."""
    return pd.Series(INTERCONNECTOR_EFFICIENCY, index=length_km.index, dtype=float)


def add_transmission_flow_cost(
    network: pypsa.Network, snapshots: pd.DatetimeIndex
) -> None:
    """Charge a small cost on absolute passive-branch flow."""
    if "Line-s" not in network.model.variables:
        return

    flow = network.model["Line-s"]
    absolute_flow = network.model.add_variables(
        lower=0.0,
        coords=flow.coords,
        name="Line-absolute-flow",
    )
    network.model.add_constraints(
        absolute_flow >= flow,
        name="Line-absolute-flow-positive",
    )
    network.model.add_constraints(
        absolute_flow >= -flow,
        name="Line-absolute-flow-negative",
    )
    weights = xr.DataArray(
        network.snapshot_weightings.generators.reindex(snapshots),
        dims=["snapshot"],
        coords={"snapshot": snapshots},
    )
    flow_cost = TRANSMISSION_FLOW_COST_PER_MWH * (absolute_flow * weights).sum()
    network.model.add_objective(
        network.model.objective.expression + flow_cost,
        overwrite=True,
    )
