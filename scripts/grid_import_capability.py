"""How much import the model's grid can take into Tamil Nadu, compared with Grid-India's TTC.

    python scripts/grid_import_capability.py [--two-level] [--sources all|S1] [--output <csv>]

For one hour (Tamil Nadu's annual demand peak by default) the network is
reduced to a transfer test:

- the fixed interstate schedule (loads at the neighbouring buses) and the
  other states' export reservations at central stations are removed, and an
  unlimited free source is placed at each neighbouring bus (``--sources S1``:
  Andhra Pradesh and Karnataka only, Grid-India's S1 bid area);
- hydro, pumped storage and storage are removed; unit commitment, minimum
  outputs and energy limits are dropped;
- Tamil Nadu's share of the interstate-grid stations inside the state
  (central stations and Kudankulam/MAPS) runs at its availability, as in
  Grid-India's base case; every other Tamil Nadu plant costs 1,000 INR/MWh,
  so the optimiser imports whatever the grid can carry;
- Tamil Nadu's demand is scaled up step by step (``--scales``).

For each scale it reports the border import (net flow over the interstate
ties), the drawal (border import plus the interstate-grid stations inside Tamil
Nadu, the quantity Grid-India's TTC covers), the Tamil Nadu generation
needed, and the branches at their limit. The import capability is the import
reached once Tamil Nadu generation is needed although cheaper imports are
available.
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from two_level_grid import TN_KIND, UPPER_KIND, apply_two_level_grid  # noqa: E402

NEIGHBOURS = ["Andhra Pradesh", "Karnataka", "Kerala", "Puducherry", "Western Region"]
S1 = ["Andhra Pradesh", "Karnataka"]
# Tamil Nadu's share of interstate-grid stations inside the state.
ISTS_IN_TN = (r"vallur|tuticorin_joint_venture|neyveli_new_tpp|neyveli_tps_ii__|neyveli_ext_tps|"
              r"kudankulam_atomic_power_project__|madras_atomic_power_station__")
TN_COST = 1000.0
TTC_MW = 14600.0  # Grid-India Tamil Nadu import TTC, Rev 1 declarations from July 2025
S1_TTC_MW = 11300.0  # S1-(S2&S3) TTC, December 2025


def transfer_network(two_level: bool, sources: list[str], snapshot: pd.Timestamp | None) -> tuple[pypsa.Network, list[str]]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        n = pypsa.Network(ROOT / "model")
    apply_two_level_grid(n, {"two_level": two_level})
    tn = n.buses.index[n.buses.kind.isin([TN_KIND, UPPER_KIND])].tolist()
    tn_loads = n.loads.index[n.loads.bus.isin(tn)]
    if snapshot is None:
        snapshot = n.loads_t.p_set[tn_loads].sum(axis=1).idxmax()
    n.set_snapshots([snapshot])

    electrical = set(tn) | set(NEIGHBOURS)
    for component in ("StorageUnit", "Store"):
        n.remove(component, n.static(component).index)
    hydro_links = n.links.index[~(n.links.bus0.isin(electrical) & n.links.bus1.isin(electrical))]
    n.remove("Link", hydro_links)
    n.remove("Load", n.loads.index[~n.loads.bus.isin(tn)])
    drop = n.generators.index[~n.generators.bus.isin(electrical) | n.generators.index.str.startswith("rea_export_reservation")]
    n.remove("Generator", drop)
    for column in ("p_min_pu", "e_sum_min"):
        if column in n.generators:
            n.generators[column] = 0.0
    n.generators_t.p_min_pu = n.generators_t.p_min_pu.iloc[:, 0:0]
    n.generators["e_sum_max"] = np.inf
    n.generators["committable"] = False
    n.remove("GlobalConstraint", n.global_constraints.index)

    ists = n.generators.index[n.generators.index.str.contains(ISTS_IN_TN, regex=True)]
    availability = n.generators_t.p_max_pu.reindex(columns=ists).iloc[0].fillna(n.generators.loc[ists, "p_max_pu"])
    n.generators.loc[ists, "p_min_pu"] = availability
    n.generators.loc[ists, "p_max_pu"] = availability
    n.generators_t.p_max_pu = n.generators_t.p_max_pu.drop(columns=ists, errors="ignore")
    other = n.generators.index.difference(ists)
    unserved = n.generators.carrier.eq("unserved_energy")
    n.generators.loc[other[~unserved.loc[other]], "marginal_cost"] = TN_COST
    n.generators_t.marginal_cost = n.generators_t.marginal_cost.iloc[:, 0:0]
    if "import_source" not in n.carriers.index:
        n.add("Carrier", "import_source")
    for bus in sources:
        n.add("Generator", f"import_source_{bus}", bus=bus, p_nom=1e6, marginal_cost=0.0, carrier="import_source")
    return n, tn


def solve_case(two_level: bool, sources: list[str], scale: float, snapshot) -> dict:
    n, tn = transfer_network(two_level, sources, snapshot)
    tn_loads = n.loads.index[n.loads.bus.isin(tn)]
    n.loads_t.p_set[tn_loads] = n.loads_t.p_set[tn_loads] * scale
    status, condition = n.optimize(solver_name="highs", solver_options={"output_flag": False})
    if status != "ok":
        raise RuntimeError(f"Transfer test failed: {status} {condition}")
    t = n.snapshots[0]
    links = n.links
    into = links.index[links.bus1.isin(tn) & ~links.bus0.isin(tn)]
    out = links.index[links.bus0.isin(tn) & ~links.bus1.isin(tn)]
    border = float(-n.links_t.p1.loc[t, into].sum() - n.links_t.p0.loc[t, out].sum())
    gen = n.generators_t.p.loc[t]
    ists = n.generators.index[n.generators.index.str.contains(ISTS_IN_TN, regex=True) & n.generators.bus.isin(tn)]
    tn_gen = n.generators.index[n.generators.bus.isin(tn) & ~n.generators.index.isin(ists)
                                & n.generators.carrier.ne("unserved_energy")]
    lines = n.lines
    loading = n.lines_t.p0.loc[t].abs() / (lines.s_nom * lines.s_max_pu)
    link_loading = n.links_t.p0.loc[t] / (links.p_nom * links.p_max_pu)
    binding = list(loading.index[loading > 0.999]) + list(link_loading.index[link_loading > 0.999])
    return {
        "grid": "two-level" if two_level else "single-level", "sources": "+".join(sources), "snapshot": t,
        "scale": scale, "tn_load_mw": float(n.loads_t.p_set.loc[t, n.loads.index[n.loads.bus.isin(tn)]].sum()),
        "border_import_mw": border, "ists_in_tn_mw": float(gen[ists].sum()), "drawal_mw": border + float(gen[ists].sum()),
        "tn_other_generation_mw": float(gen[tn_gen].sum()),
        "unserved_mw": float(gen[n.generators.index[n.generators.carrier.eq("unserved_energy")]].sum()),
        "binding": "; ".join(sorted(binding)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--two-level", action="store_true")
    parser.add_argument("--sources", choices=["all", "S1"], default="all")
    parser.add_argument("--snapshot", type=pd.Timestamp, default=None, help="hour to test; default: TN demand peak")
    parser.add_argument("--scales", type=float, nargs="+", default=[1.0, 1.5, 2.0, 3.0, 4.0])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    sources = NEIGHBOURS if args.sources == "all" else S1
    rows = [solve_case(args.two_level, sources, scale, args.snapshot) for scale in args.scales]
    table = pd.DataFrame(rows)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(args.output, index=False, float_format="%.0f")
    columns = ["scale", "tn_load_mw", "border_import_mw", "drawal_mw", "tn_other_generation_mw", "unserved_mw"]
    print(table[columns].round({c: 0 for c in columns[1:]}).to_string(index=False))
    print(f"Grid-India reference: TN import TTC {TTC_MW:,.0f} MW (drawal); S1-(S2&S3) TTC {S1_TTC_MW:,.0f} MW")
    for row in table.itertuples():
        print(f"  scale {row.scale}: binding = {row.binding or 'none'}")


if __name__ == "__main__":
    main()
