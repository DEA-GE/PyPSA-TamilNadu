# Model CSV folder

`model/` is the supported, ready-to-load nine-balancing-area PyPSA network for
8,760 hourly snapshots from 1 April 2025 through 31 March 2026. Rebuild it
from the repository root with:

```powershell
python scripts/build_9ba_model.py
```

Load the network with `pypsa.Network("model")`. The build reads prepared files
under `data/build_template/`, `data/model_inputs/`, and `data/reservoirs/`, plus
the plant inventory and observed-generation workbooks in `data/source/`.
Changes to those inputs require a rebuild; changes to generated files in
`model/` will be overwritten.

## What is in this folder

| Files | Meaning |
| --- | --- |
| `buses.csv`, `links.csv`, `generators.csv`, `loads.csv`, `storage_units.csv`, `stores.csv` | Network components and their capacities and operating assumptions |
| `snapshots.csv`, `loads-p_set.csv`, `generators-p_max_pu.csv`, `storage_units-inflow.csv` | Hourly snapshots, demand, availability, and simple-reservoir inflow |
| `global_constraints.csv`, `oil_gas_daily_energy_budget.csv`, `nuclear_daily_energy_budget.csv` | Annual equality constraints and observed daily energy targets |
| `component_metadata.csv`, `district_capacity_allocation.csv`, `demand_allocation.csv`, `capacity_by_balancing_area.csv` | Component provenance and spatial allocation audits |
| `hydro_*.csv`, `transmission_capacity_metadata.csv`, `capacity_reconciliation_2026_07.csv` | Hydro assumptions and validation, corridor ratings, and capacity reconciliation |

The network includes annual oil-and-gas, nuclear, and biomass generation
equality constraints. For a shorter solve, use the runners described in the
[repository README](../README.md#how-to-run); they adjust the
constraints for the chosen period. Directly optimizing a subset of snapshots
without adjusting the annual constraints is not a valid short-run example.

The [input data guide](../docs/input-data.md) maps each source and prepared
input to its model output and explains units, timestamp alignment, provenance,
and limitations. The [repository README](../README.md) documents run modes,
result locations, model assumptions, and study limitations. Generated results
are described in [results/README.md](../results/README.md).
