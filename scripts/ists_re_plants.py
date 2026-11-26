"""Run the interstate-connected wind and solar plants on their real output.

Thirteen wind and solar plants on the interstate pooling stations at
Tuticorin, Karur and Pugalur (about 2.6 GW of wind and 0.4 GW of solar) have
hourly output in SRPC's Regional Energy Accounts
(``scripts/prepare_ists_re_plant_output.py``): metered where their deviation
account (or their QCA's) reports, the REMC schedule otherwise. In the built
network they are part of the area wind and solar fleets and follow the area
profiles.

With ``ists_re_plants.enabled`` each plant becomes its own generator on the
lower bus of its pooling station's area, with ``p_max_pu`` = its output /
its capacity (it can still be curtailed), and the area's wind or solar fleet
(rooftop excluded) is reduced by the same capacity, pro rata to its
generators' ratings. Every other generator keeps its profile. ``basis``
chooses the output: ``metered`` (metered where available, schedule otherwise)
or ``scheduled`` (REMC schedule only). Hours without data use the area
fleet's availability.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd
import pypsa

ROOT = Path(__file__).resolve().parents[1]
SECTION_KEYS = {"enabled", "basis", "series", "plants"}
DEFAULTS = {
    "enabled": False,
    "basis": "metered",
    "series": "data/model_inputs/tn_ists_re_plant_series_FY2025_26.csv",
    "plants": "data/model_inputs/tn_ists_re_plants_FY2025_26.csv",
}
BASES = {"metered": "output_mw", "scheduled": "scheduled_mw"}
PREFIX = "ists_re_plant__"


class IstsRePlantError(ValueError):
    """Invalid settings or inputs for the interstate wind and solar plants."""


def _resolve(path: str | Path) -> Path:
    path = Path(path).expanduser()
    return path if path.is_absolute() else ROOT / path


def validate_section(section: Mapping[str, Any] | None) -> dict[str, Any]:
    section = {**DEFAULTS, **(section or {})}
    unknown = set(section) - SECTION_KEYS
    if unknown:
        raise IstsRePlantError(f"Unknown ists_re_plants keys {sorted(unknown)}; known: {sorted(SECTION_KEYS)}")
    if not isinstance(section["enabled"], bool):
        raise IstsRePlantError("ists_re_plants.enabled must be true or false")
    if section["basis"] not in BASES:
        raise IstsRePlantError(f"ists_re_plants.basis must be one of {sorted(BASES)}")
    return section


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def fleet(network: pypsa.Network, area: str, technology: str) -> pd.Index:
    """The area's wind or solar generators that a plant can be carved out of."""
    generators = network.generators
    chosen = (generators.bus.eq(area) & generators.carrier.eq(technology)
              & ~generators.index.str.contains("rooftop") & ~generators.index.str.startswith(PREFIX))
    return generators.index[chosen & generators.p_nom.gt(0)]


def apply_ists_re_plants(network: pypsa.Network, settings: Mapping[str, Any] | None) -> pd.DataFrame:
    """Add the plants as generators on their output and shrink the area fleets; report per plant (empty when off)."""
    settings = validate_section(settings)
    if not settings["enabled"]:
        return pd.DataFrame()
    plants = pd.read_csv(_resolve(settings["plants"]), index_col="seller")
    series = pd.read_csv(_resolve(settings["series"]), parse_dates=["snapshot"])
    output = series.pivot_table(index="snapshot", columns="seller", values=BASES[settings["basis"]])
    output = output.reindex(network.snapshots)
    p_max_pu = network.get_switchable_as_dense("Generator", "p_max_pu")
    rows = []
    for (area, technology), group in plants.groupby(["area", "technology"]):
        members = fleet(network, area, technology)
        fleet_mw = float(network.generators.loc[members, "p_nom"].sum())
        capacity = float(group.capacity_mw.sum())
        if capacity > fleet_mw:
            raise IstsRePlantError(
                f"{area} {technology}: plants of {capacity:,.0f} MW exceed the area fleet of {fleet_mw:,.0f} MW")
        weights = network.generators.loc[members, "p_nom"] / fleet_mw
        fallback = p_max_pu[members].mul(weights, axis=1).sum(axis=1)
        template = network.generators.loc[members].sort_values("p_nom").iloc[-1]
        network.generators.loc[members, "p_nom"] = network.generators.loc[members, "p_nom"] * (1 - capacity / fleet_mw)
        for seller, plant in group.iterrows():
            if seller not in output:
                raise IstsRePlantError(f"No output series for {seller}")
            profile = (output[seller] / plant.capacity_mw).clip(0.0, 1.0)
            missing = int(profile.isna().sum())
            profile = profile.fillna(fallback)
            name = PREFIX + _slug(seller)
            network.add("Generator", name, bus=area, carrier=technology, p_nom=plant.capacity_mw,
                        marginal_cost=template.marginal_cost, p_max_pu=profile.to_numpy())
            rows.append({"plant": name, "seller": seller, "area": area, "technology": technology,
                         "capacity_mw": plant.capacity_mw, "potential_gwh": float(
                             (profile * plant.capacity_mw * network.snapshot_weightings.generators).sum() / 1e3),
                         "hours_from_fleet_profile": missing, "fleet_mw_before": fleet_mw,
                         "fleet_mw_after": fleet_mw - capacity})
    return pd.DataFrame(rows)
