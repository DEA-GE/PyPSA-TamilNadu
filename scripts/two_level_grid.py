"""Two voltage levels per Tamil Nadu balancing area.

The built network has one bus per balancing area. Every inter-area corridor is
a single line whose rating is the sum of all its circuits, whatever their
voltage. Power arriving at an area on a 400 kV line can therefore reach the
area's load without passing a transformer. In reality it must pass the 400/230
and 400/110 kV inter-connecting transformers (ICTs), and Grid-India names N-1
of those transformers as the limit of Tamil Nadu's import capability.

With ``grid.two_level`` on, this module rebuilds the Tamil Nadu grid at run
time (model/ stays unchanged):

- every Tamil Nadu area keeps its bus as the lower level (230 kV and below:
  all loads, storage, hydro, wind, solar and the smaller plants) and gets an
  upper-level bus ``<area> 400kV`` (400 and 765 kV);
- each inter-area corridor is split by circuit voltage into a 400 kV line
  (400/765 kV circuits) between the upper buses and a 230 kV line (230/220/110
  kV circuits) between the lower buses. Ratings and reactances come from the
  corridor's own circuits in the grid workbook, so the two lines together
  have the corridor's original rating and parallel reactance; ``s_max_pu`` is
  kept;
- each interstate tie is split the same way: its 400/765 kV and HVDC part
  lands on the upper bus, its 230/220/110 kV part on the lower bus;
- each area's upper and lower buses are joined by one transformer branch with
  the area's 400/230 and 400/110 kV ICT capacity on 31 March 2026
  (data/source/srldc_transmission_elements/tn_icts_2026-03.csv). Its rating
  is the MVA total times the power factor, usable up to the N-1 secure part
  (``transformer_n_minus_1``): ``substation`` (the default) drops the largest
  unit of every substation, as each substation must survive the loss of its own
  largest transformer (the criterion behind Grid-India's limits); ``area``
  drops only the area's largest unit; ``none`` uses all units. Its reactance is
  ``transformer_reactance_pu`` on each unit's own rating, in parallel;
- plants that connect at 400 or 765 kV (data/source/srldc_transmission_elements/
  tn_plant_connection_levels.csv) move to the upper bus.

765/400 kV transformers are not modelled; 765 kV circuits join the 400 kV
level.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pypsa

ROOT = Path(__file__).resolve().parents[1]
UPPER_SUFFIX = " 400kV"
UPPER_KIND = "TN balancing area (400 kV)"
TN_KIND = "TN balancing area"
UPPER_MIN_KV = 320  # 320 kV VSC HVDC, 400 kV, 765 kV and ±800 kV
ICT_RATIOS = ("400/230", "400/110")
BASE_KV = 400.0  # the workbook gives all impedances in ohm on a 400 kV base
SECTION_KEYS = {
    "two_level", "circuits", "transformers", "plant_levels",
    "transformer_reactance_pu", "transformer_power_factor", "transformer_n_minus_1",
}
DEFAULTS = {
    "two_level": False,
    "circuits": "data/model_inputs/TN_9BA_with_interconnectors_pypsa_FY2025-26.xlsx",
    "transformers": "data/source/srldc_transmission_elements/tn_icts_2026-03.csv",
    "plant_levels": "data/source/srldc_transmission_elements/tn_plant_connection_levels.csv",
    "transformer_reactance_pu": 0.125,
    "transformer_power_factor": 0.95,
    "transformer_n_minus_1": "substation",
}
N_MINUS_1 = ("substation", "area", "none")


class TwoLevelGridError(ValueError):
    """Invalid two-level grid settings or inputs."""


def _resolve(path: str | Path) -> Path:
    path = Path(path).expanduser()
    return path if path.is_absolute() else ROOT / path


def validate_section(section: Mapping[str, Any]) -> None:
    unknown = set(section) - SECTION_KEYS
    if unknown:
        raise TwoLevelGridError(f"Unknown grid keys {sorted(unknown)}; known: {sorted(SECTION_KEYS)}")
    if not isinstance(section.get("two_level", False), bool):
        raise TwoLevelGridError("grid.two_level must be true or false")
    _n_minus_1_mode(section.get("transformer_n_minus_1", DEFAULTS["transformer_n_minus_1"]))
    for key in ("transformer_reactance_pu", "transformer_power_factor"):
        value = section.get(key, DEFAULTS[key])
        if not isinstance(value, (int, float)) or not 0 < value <= 1:
            raise TwoLevelGridError(f"grid.{key} must be a number in (0, 1]")


def _n_minus_1_mode(value: Any) -> str:
    """``substation``, ``area`` or ``none``; true/false (earlier configs) mean area/none."""
    if isinstance(value, bool):
        return "area" if value else "none"
    if value not in N_MINUS_1:
        raise TwoLevelGridError(f"grid.transformer_n_minus_1 must be one of {list(N_MINUS_1)}")
    return value


def read_circuits(path: str | Path) -> pd.DataFrame:
    """Rated circuits of the grid workbook, indexed by line id, with a voltage level."""
    circuits = pd.read_excel(_resolve(path), sheet_name="circuits")
    circuits = circuits[circuits["rated"].astype(bool) & circuits.capacity_mw.gt(0)].set_index("line_id")
    kv = pd.to_numeric(circuits.kv, errors="coerce")
    circuits["level"] = np.where(kv >= UPPER_MIN_KV, "upper", "lower")
    return circuits


def _parallel(values: pd.Series) -> float:
    values = values[values > 0]
    return float(1.0 / (1.0 / values).sum()) if len(values) else 0.0


def _ids(text: Any) -> list[str]:
    return str(text).split() if isinstance(text, str) else []


def split_corridors(network: pypsa.Network, circuits: pd.DataFrame, upper: Mapping[str, str]) -> list[dict]:
    """Replace every inter-area line by a 400 kV and a 230 kV line built from its circuits."""
    lines = network.lines[network.lines.bus0.isin(upper) & network.lines.bus1.isin(upper)]
    report = []
    for name, line in lines.iterrows():
        own = circuits.reindex(_ids(line.get("line_ids"))).dropna(subset=["capacity_mw"])
        if own.empty:
            raise TwoLevelGridError(f"Line {name} has no rated circuits in the workbook")
        groups = {level: group for level, group in own.groupby("level")}
        main = "upper" if "upper" in groups else "lower"
        attrs = line.to_dict()
        network.remove("Line", name)
        for level, group in groups.items():
            buses = (upper[line.bus0], upper[line.bus1]) if level == "upper" else (line.bus0, line.bus1)
            label = name if level == main else f"{name} ({'400' if level == 'upper' else '230'} kV)"
            network.add(
                "Line", label, bus0=buses[0], bus1=buses[1],
                x=_parallel(group.x_ohm), r=_parallel(group.r_ohm), b=float(group.b_siemens.fillna(0).sum()),
                s_nom=float(group.capacity_mw.sum()), s_max_pu=attrs["s_max_pu"], length=attrs.get("length", 0.0),
                carrier=attrs.get("carrier", "AC"),
            )
            report.append({"component": "Line", "name": label, "level": level, "bus0": buses[0], "bus1": buses[1],
                           "rating_mw": float(group.capacity_mw.sum()), "circuits": len(group)})
    return report


def split_ties(network: pypsa.Network, circuits: pd.DataFrame, upper: Mapping[str, str]) -> list[dict]:
    """Move the 400/765 kV and HVDC part of every interstate link to the upper bus."""
    links = network.links
    ties = links[(links.bus0.isin(upper) ^ links.bus1.isin(upper)) & links.carrier.isin(["AC", "DC"])]
    report = []
    for name, link in ties.iterrows():
        tn_end = "bus0" if link.bus0 in upper else "bus1"
        if link.carrier == "DC":
            ratings = {"upper": float(link.p_nom)}
        else:
            own = circuits.reindex(_ids(link.get("line_ids"))).dropna(subset=["capacity_mw"])
            if own.empty:
                raise TwoLevelGridError(f"Link {name} has no rated circuits in the workbook")
            ratings = own.groupby("level").capacity_mw.sum().to_dict()
        main = "upper" if "upper" in ratings else "lower"
        for level, rating in ratings.items():
            bus = upper[link[tn_end]] if level == "upper" else link[tn_end]
            if level == main:
                network.links.loc[name, [tn_end, "p_nom"]] = [bus, rating]
                label = name
            else:
                label = name.replace(" (AC tie)", " (AC tie, 230 kV)")
                attrs = {key: link[key] for key in ("bus0", "bus1", "carrier", "p_min_pu", "p_max_pu",
                                                     "marginal_cost", "efficiency", "length") if key in link}
                attrs[tn_end] = bus
                network.add("Link", label, **{**attrs, "p_nom": rating})
            report.append({"component": "Link", "name": label, "level": level, "bus0": network.links.at[label, "bus0"],
                           "bus1": network.links.at[label, "bus1"], "rating_mw": rating, "circuits": None})
    return report


def transformer_branches(units: pd.DataFrame, areas: list[str], reactance_pu: float, power_factor: float,
                         n_minus_1: str | bool) -> pd.DataFrame:
    """One transformer branch per area: rating, N-1 share and reactance (ohm on 400 kV)."""
    mode = _n_minus_1_mode(n_minus_1)
    units = units[units.kv.isin(ICT_RATIOS) & units.area.isin(areas)]
    missing = sorted(set(areas) - set(units.area))
    if missing:
        raise TwoLevelGridError(f"No 400/230 or 400/110 kV transformers listed for {missing}")
    grouped = units.groupby("area").mva
    total, largest = grouped.sum(), grouped.max()
    if mode == "substation":
        by_substation = units.groupby(["area", "bus"]).mva
        secure = (by_substation.sum() - by_substation.max()).groupby(level="area").sum()
    elif mode == "area":
        secure = total - largest
    else:
        secure = total
    x = (reactance_pu * BASE_KV**2 / units.mva).groupby(units.area).apply(_parallel)
    return pd.DataFrame({
        "mva": total, "units": grouped.count(), "largest_mva": largest, "secure_mva": secure,
        "s_nom": total * power_factor,
        "s_max_pu": secure / total,
        "x": x, "r": x / 40.0,  # X/R of about 40 for large power transformers
    }).reindex(areas)


def plants_on_upper_level(network: pypsa.Network, levels: pd.DataFrame, areas: list[str]) -> pd.Index:
    generators = network.generators[network.generators.bus.isin(areas)]
    pattern = "|".join(f"(?:{p})" for p in levels.loc[levels.level >= UPPER_MIN_KV, "pattern"])
    return generators.index[generators.index.str.contains(pattern, regex=True)]


def apply_two_level_grid(network: pypsa.Network, settings: Mapping[str, Any] | None) -> pd.DataFrame:
    """Rebuild the Tamil Nadu grid with two voltage levels; returns a component report (empty when off)."""
    settings = {**DEFAULTS, **(settings or {})}
    validate_section(settings)
    if not settings["two_level"]:
        return pd.DataFrame()
    areas = network.buses.index[network.buses.kind.eq(TN_KIND)].tolist()
    if not areas:
        raise TwoLevelGridError("The network has no buses of kind 'TN balancing area'")
    upper = {area: area + UPPER_SUFFIX for area in areas}
    for area in areas:
        bus = network.buses.loc[area]
        network.add("Bus", upper[area], v_nom=bus.v_nom, x=bus.x, y=bus.y, carrier=bus.carrier, kind=UPPER_KIND)

    circuits = read_circuits(settings["circuits"])
    report = split_corridors(network, circuits, upper) + split_ties(network, circuits, upper)

    units = pd.read_csv(_resolve(settings["transformers"]))
    branches = transformer_branches(units, areas, settings["transformer_reactance_pu"],
                                    settings["transformer_power_factor"], settings["transformer_n_minus_1"])
    for area, row in branches.iterrows():
        name = f"{area} ICT 400/230+110 kV"
        network.add("Line", name, bus0=upper[area], bus1=area, x=row.x, r=row.r, s_nom=row.s_nom,
                    s_max_pu=row.s_max_pu, carrier="AC")
        report.append({"component": "Line", "name": name, "level": "transformer", "bus0": upper[area], "bus1": area,
                       "rating_mw": row.s_nom * row.s_max_pu, "circuits": int(row.units)})

    levels = pd.read_csv(_resolve(settings["plant_levels"]))
    moved = plants_on_upper_level(network, levels, areas)
    network.generators.loc[moved, "bus"] = network.generators.loc[moved, "bus"].map(upper)
    for name in moved:
        report.append({"component": "Generator", "name": name, "level": "upper",
                       "bus0": network.generators.at[name, "bus"], "bus1": None,
                       "rating_mw": float(network.generators.at[name, "p_nom"]), "circuits": None})
    return pd.DataFrame(report)
