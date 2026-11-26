# Model CSV folder

`model/` is the supported, ready-to-load nine-balancing-area PyPSA network for
8,760 hourly snapshots from 1 April 2025 through 31 March 2026. Load it with
`pypsa.Network("model")`. Rebuild it from the repository root with:

```powershell
python scripts/build_9ba_model.py
```

The build reads prepared files under `data/build_template/`, `data/model_inputs/`
and `data/reservoirs/`, plus the plant inventory and observed-generation
workbooks in `data/source/`. Changes to those inputs require a rebuild; changes
to generated files in `model/` will be overwritten. To replace only the
transmission network from the grid workbook, run
`python scripts/import_grid_from_excel.py`.

The [model description](../docs/model-description.md) explains what the
network represents and why, the [input data guide](../docs/input-data.md) where
each input comes from, and the [README](../README.md) how to run it.

## What is in this folder

| Files | Meaning |
| --- | --- |
| `network.csv`, `crs.json`, `carriers.csv`, `snapshots.csv` | Network name, coordinate system (longitude/latitude), carriers and the 8,760 hourly snapshots |
| `buses.csv` | The nine Tamil Nadu balancing areas, five neighbouring states and regions, and 25 water buses of the hydro model |
| `lines.csv`, `links.csv` | The 18 corridors between areas (AC lines), the 24 one-way interstate links, and the hydro turbines, pump, water transfers and spill |
| `loads.csv`, `loads-p_set.csv` | The nine area demands and the fixed interstate exchange at the neighbour buses (imports negative, exports positive) |
| `generators.csv`, `generators-p_max_pu.csv`, `generators-p_min_pu.csv`, `generators-marginal_cost.csv` | Generators with their hourly availability, the fixed schedules of the export reservations, and the monthly energy charges of the central coal units |
| `storage_units.csv`, `storage_units-inflow.csv`, `stores.csv` | The Suruliyar reservoir and its inflow; the reservoirs, pondages and downstream sinks of the hydro model |
| `component_metadata.csv`, `district_capacity_allocation.csv`, `capacity_by_balancing_area.csv`, `additional_capacity_2026_07.csv`, `capacity_reconciliation_2026_07.csv` | Component provenance, spatial allocation, installed capacity by area and the July 2026 capacity reconciliation |
| `demand_allocation.csv` | Area shares, energy, peak and the affine coefficients of the synthetic area demand |
| `coal_operating_cost_audit.csv`, `plant_operating_cost_audit.csv` | Applied energy charges, unit ratings and start-up costs |
| `oil_gas_daily_energy_budget.csv`, `nuclear_daily_energy_budget.csv`, `oil_gas_energy_budget_summary.csv`, `biomass_energy_target_summary.csv`, `small_hydro_energy_target_summary.csv` | Observed energy targets and their annual summaries, applied by the run scripts |
| `solar_capacity_trajectory.csv`, `solar_availability_calibration.csv`, `solar_seasonal_correction.csv` | Month-end solar capacity applied to solar availability, the annual-energy calibration, and the applied soiling, wind-cooling and seasonal correction |
| `hydro_*.csv`, `upper_kodayar_inflow_estimate.csv` | Hydro assumptions, parameters, observed storage, inflows, calibration targets and capacity validation |
| `transmission_capacity_metadata.csv` | Corridor and tie ratings, circuits and provenance |
| `interstate_flow_audit.csv`, `interstate_flow_reconciliation.csv` | Hourly and annual audit of the interstate exchange by measure and gateway |
| `rea_plant_export_reservation_audit.csv`, `tn_export_capacity_allocations.csv` | The plant-level export reservations; the superseded four-station capacity shares (reference only) |

## Notes

- **No energy targets in the network.** The folder has no
  `global_constraints.csv`, so any subset of snapshots can be optimised
  directly, but without calibration to observed generation. The runners
  described in the [README](../README.md#how-to-run) apply the observed targets
  over the exact period solved (`scripts/period_energy_targets.py`):
  oil-and-gas and nuclear per day; bio-power and small hydro as
  month-prorated equalities.
- **Interstate exchange.** The neighbour loads fix the exchange at each
  neighbour bus every hour: the verified SRPC REA transactions plus a
  correction that matches Tamil Nadu's metered net import (the default
  build). The links' hourly flows are not fixed, so the grid chooses the
  routes. See [Imports and exports](../docs/model-description.md#interstate-exchange-a-fixed-schedule).
- **Off-grid capacity is excluded**: the 75.91 MW off-grid solar and the
  27.57 MW off-grid waste-to-energy categories are not in `generators.csv`,
  the availability columns, the component metadata or the district audit.
  They must not be added back unless the study scope is changed deliberately.
- **Run-time options.** Coal outages, the two-level grid, the import limit,
  exchange trading, the interstate plant series, rooftop removal and the
  scenario technologies are applied by `scripts/run_model.py` to a copy of
  this network; the files here are unchanged.
