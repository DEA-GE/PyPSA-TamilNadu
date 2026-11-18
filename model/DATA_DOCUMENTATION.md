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

To replace only the transmission network from the active Excel input, run
`python scripts/import_grid_from_excel.py` from the repository root.

## What is in this folder

| Files | Meaning |
| --- | --- |
| `buses.csv`, `lines.csv`, `links.csv`, `generators.csv`, `loads.csv`, `storage_units.csv`, `stores.csv` | Network components and their capacities and operating assumptions |
| `snapshots.csv`, `loads-p_set.csv`, `generators-p_max_pu.csv`, `storage_units-inflow.csv` | Hourly snapshots, demand including external-bus exchange, generator availability, and simple-reservoir inflow |
| `oil_gas_daily_energy_budget.csv`, `nuclear_daily_energy_budget.csv`, `*_energy_*_summary.csv` | Observed daily energy targets and annual target summaries (applied by the run scripts, not as PyPSA GlobalConstraints) |
| `component_metadata.csv`, `district_capacity_allocation.csv`, `demand_allocation.csv`, `capacity_by_balancing_area.csv` | Component provenance and spatial allocation audits |
| `solar_capacity_trajectory.csv`, `solar_availability_calibration.csv`, `solar_seasonal_correction.csv` | Month-end government solar capacity applied to solar availability, the annual-energy calibration of that availability, and the applied monthly soiling/wind-cooling/seasonal correction |
| `hydro_*.csv`, `transmission_capacity_metadata.csv`, `capacity_reconciliation_2026_07.csv`, `interstate_flow_audit.csv`, `interstate_flow_reconciliation.csv` | Hydro assumptions and validation, corridor ratings, capacity reconciliation, and hourly/annual REA flow audits |

FY2025-26 SRPC REA imports and exports are represented at four external buses.
Only confirmed, resolved imports are fixed loads, using negative `p_set`.
Unresolved imports remain in `interstate_flow_audit.csv` but have zero modeled
flow and are labeled `unresolved_distributed_excluded_from_model`. Resolved
exports and proportionally allocated unresolved exports use positive `p_set`;
the latter remain labeled `unresolved_distributed_not_verified`. Export
allocation uses annual resolved-flow gateway shares. A capacity-aware
redistribution step moves provisional export flow when its initial gateway lacks
headroom and fails the build if the total cannot be placed. The existing grid
links carry
these boundary injections or withdrawals, but their hourly minimum and maximum
flows are not fixed, allowing parallel paths to be selected by the network.
The former dispatchable import-entitlement generators remain removed to avoid
double counting.

The four listed in-state thermal stations with allocations to other states are
derated to their Tamil Nadu shares before spatial allocation. Their out-of-state
and unlisted/unallocated capacity is not represented as a TN-serving generator
or as an explicit export flow. Nuclear retains its full nameplate capacity and
uses observed FY2025-26 daily generation equality targets.

The network files carry no energy targets (no `global_constraints.csv`), so any
subset of snapshots can be optimized directly, but without calibration to
observed generation. The runners described in the
[repository README](../README.md#how-to-run) apply the observed targets over the
exact period solved (`scripts/period_energy_targets.py`): oil-and-gas and
nuclear per day; bio-power and small hydro as month-prorated equalities.

The bio-power targets total 194,010 MWh from the twelve Tamil Nadu Outlook
FY2025-26 monthly values. Each monthly total is spread uniformly over the
month's hours; each solved period receives its share as one equality, leaving
the daily and hourly generation pattern free for the optimizer.

Explicitly off-grid capacity is excluded from the supported model. In
particular, the 75.91 MW off-grid solar category and the 27.57 MW off-grid
waste-to-energy (bio-power) category are omitted from `generators.csv`, their
availability columns, component metadata, and district-allocation audit. They
must not be added back when rebuilding the model unless the study scope is
changed deliberately.

Small hydro is treated identically using a 307,560 MWh annual total from the
twelve Tamil Nadu Outlook monthly values. Its previous placeholder availability
profile is relaxed to nameplate because that profile cannot supply the observed
monthly energy; the month-prorated period equalities determine energy while
the model optimizes its daily and hourly timing.

The [input data guide](../docs/input-data.md) maps each source and prepared
input to its model output and explains units, timestamp alignment, provenance,
and limitations. The [repository README](../README.md) documents run modes,
result locations, model assumptions, and study limitations. Generated results
are described in [results/README.md](../results/README.md).
