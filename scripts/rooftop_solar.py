"""Include or leave out grid-connected rooftop solar (the ``rooftop_solar`` run setting).

The build models Tamil Nadu's grid-connected rooftop solar as supply: one
generator per area, all from the inventory record "Tamil Nadu N.A. Rooftop
(Grid Connected) 1635.8" (1,635.8 MW in the July 2026 fleet; the solar
capacity trajectory scales it through FY2025-26 with the rest of the fleet).
The model's demand is the state load despatch centre's "Demand Met", which
counts only generation the centre sees plus the state's drawal from the
interstate grid. Rooftop systems report to no one in real time: whatever they
supply, used on site or exported to the distribution network, shows up only
as lower load at the substations, so the demand series is already net of it.
Modelled as supply, rooftop output is then counted twice.

``include: false`` removes the rooftop generators before the solve, so their
output stays netted in the demand. ``include: true`` (the default) keeps the
network as built.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd
import pypsa

SECTION_KEYS = {"include"}
ROOFTOP_MARKER = "rooftop"


def validate_section(section: Mapping[str, Any]) -> dict[str, Any]:
    """Return the checked ``rooftop_solar`` settings."""
    unknown = set(section) - SECTION_KEYS
    if unknown:
        raise ValueError(f"rooftop_solar: unknown key(s) {sorted(unknown)}; known: {sorted(SECTION_KEYS)}")
    include = section.get("include", True)
    if not isinstance(include, bool):
        raise ValueError("rooftop_solar.include must be true or false")
    return {"include": include}


def rooftop_generators(network: pypsa.Network) -> pd.Index:
    """Solar generators that represent grid-connected rooftop systems."""
    generators = network.generators
    solar = generators.carrier.eq("solar")
    return generators.index[solar & generators.index.str.contains(ROOFTOP_MARKER, case=False)]


def apply_rooftop_solar(network: pypsa.Network, section: Mapping[str, Any] | None) -> pd.Series:
    """Remove the rooftop generators when ``include`` is false.

    Returns the removed generators' ``p_nom`` (empty when rooftop is kept).
    """
    settings = validate_section(section or {})
    if settings["include"]:
        return pd.Series(dtype=float)
    units = rooftop_generators(network)
    if units.empty:
        raise ValueError(
            "rooftop_solar.include is false, but the network has no solar generator "
            f"whose name contains '{ROOFTOP_MARKER}'"
        )
    removed = network.generators.loc[units, "p_nom"].astype(float)
    network.remove("Generator", units)
    return removed
