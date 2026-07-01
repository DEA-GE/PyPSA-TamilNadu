# Tamil Nadu FY 2025-26 unit-commitment inputs

## Status

This directory is a PyPSA CSV-folder dataset for a single-node, hourly unit
commitment model of Tamil Nadu for FY 2025-26. It contains 8,760 hourly
snapshots from 1 April 2025 00:00 through 31 March 2026 23:00.

The demand series comes directly from the supplied NITI Aayog ICED workbooks.
Generation capacities and annual generation targets come from the CEA resource
adequacy report. Hourly generation availability and several operational-cost
and unit-commitment parameters remain explicit dummy assumptions.

The repository's run notebook does not optimize all 8,760 hours. It extracts
the 168-hour Monday-Sunday week containing the annual demand peak, solves that
subset, and exports the solved network to `tamil_nadu_2025_26.nc`.

Load the model with:

```python
import pypsa

n = pypsa.Network("inputs/tamil_nadu_ra_2025_26")
```

## Repository workflow and outputs

Run the notebooks from the repository root in this order:

1. `run_peakday_2025_26.ipynb` loads this CSV folder, finds the annual peak at
   16:00 on 11 July 2025, copies the 168 snapshots from 7 July 00:00 through
   13 July 23:00, solves the mixed-integer unit-commitment problem with HiGHS,
   and exports `tamil_nadu_2025_26.nc`.
2. `results_analysis.ipynb` loads the exported network and plots generation by
   carrier, pumped-storage power and state of charge, and coal commitment.

The committed NetCDF is a derived result rather than a second input dataset. It
contains an optimal 168-hour solution with no unserved energy. The CSV files in
the repository-level `results/` directory are legacy two-bus example outputs
and are not generated or consumed by this Tamil Nadu workflow.

Subsetting changes how some constraints should be interpreted:

- Hydro and STOA/MTOA `e_sum_max` values remain full-year limits; the notebook
  does not prorate them for the selected week.
- Pumped-storage cyclic state of charge closes across the selected week, not
  across FY 2025-26.
- Commitment initial conditions apply at the start of 7 July without a
  preceding rolling-horizon solve.

The solved week is therefore a dispatch and unit-commitment demonstration, not
a completed annual resource-adequacy or reliability assessment.

## Sources

### Resource adequacy report

Central Electricity Authority (CEA), Ministry of Power, Government of India,
*Report on Resource Adequacy Plan (Generation) for Tamil Nadu (2025-26 to
2035-36)*, April 2026.

The report PDF is not committed to this repository. The two demand workbooks
listed below are included at the repository root.

Relevant report references:

| Information | Reference |
|---|---|
| FY 2025-26 projected energy and peak demand | Table 7, printed page 11 |
| FY 2025-26 contracted capacity | Table 15, printed page 16 |
| FY 2025-26 projected generation by source | Figure 13, printed page 20 |
| Renewable CUFs | Annexure, printed page 25 |
| Technical and storage assumptions | Annexure, printed page 26 |
| Single-node network assumption | Annexure, printed page 26 |

### Demand workbooks

- [`Tamilnadu_Telangana_Yearly Demand Profile_2025.xlsx`](../../Tamilnadu_Telangana_Yearly%20Demand%20Profile_2025.xlsx)
- [`Tamilnadu_Telangana_Yearly Demand Profile_2026.xlsx`](../../Tamilnadu_Telangana_Yearly%20Demand%20Profile_2026.xlsx)

The worksheet is `Yearly Demand Profile`; the selected fields are `State`,
`Date`, and `Hourly Demand Met (in MW)`.

- April-December 2025: 6,600 Tamil Nadu rows from the 2025 workbook.
- January-March 2026: 2,160 Tamil Nadu rows from the 2026 workbook.
- Telangana rows and workbook footer/disclaimer rows are excluded.
- The assembled fiscal-year series has 8,760 unique timestamps, no duplicates,
  no missing hours, and no extra hours.
- Values are used unchanged: no scaling, interpolation, or gap filling.

The workbooks identify NITI Aayog ICED as their source and include a disclaimer
that some platform data may be derived or assumed. In this documentation,
`workbook-sourced` means copied from the provided files; it does not make a
stronger claim about measurement or validation quality.

## Demand validation

| Metric | Workbook FY 2025-26 | CEA projection | Difference |
|---|---:|---:|---:|
| Annual energy | 131,383.400 GWh | 149,063 GWh | -17,679.600 GWh (-11.86%) |
| Peak demand | 19,987.33 MW | 21,481 MW | -1,493.67 MW (-6.95%) |
| Mean demand | 14,998.105 MW | 17,016.324 MW implied | -2,018.219 MW |
| Minimum demand | 7,483.57 MW | Not reported | n/a |

The workbook peak occurs at 16:00 on 11 July 2025. The model uses the workbook
series rather than scaling it to the higher CEA forecast.

## Capacity represented

| Component | Capacity | Representation | Provenance |
|---|---:|---|---|
| Coal | 12,808 MW | 12 equal commitment blocks | Reported capacity; dummy aggregation |
| Gas | 408 MW | 2 equal commitment blocks | Reported capacity; dummy aggregation |
| Nuclear | 1,448 MW | One aggregate fixed-output generator | Reported capacity; dummy aggregation |
| Biomass | 951 MW | 2 equal commitment blocks | Reported capacity; dummy aggregation |
| Hydro | 1,886 MW | Aggregate energy-limited generator | Reported capacity |
| Wind | 9,631 MW | Aggregate variable generator | Reported capacity |
| Utility solar | 11,106 MW | Aggregate variable generator | Reported capacity |
| Solar DRE/rooftop | 2,353 MW | Aggregate variable generator | Reported capacity |
| Hydro DRE | 58 MW | Aggregate variable generator | Reported capacity |
| PSP | 400 MW | Aggregate storage unit | Reported power; energy duration dummy |
| STOA/MTOA | 6,727 MW | Dispatchable market import | Reported peak tie-up capacity |
| Unserved energy | 100,000 MW | Virtual feasibility generator | Dummy; not part of reported capacity |

The report-based capacity sum, including PSP and STOA/MTOA but excluding the
virtual unserved-energy generator, is 47,776 MW. No BESS is included because
Table 15 reports zero storage capacity for FY 2025-26.

## Input files

### `network.csv`, `crs.json`, and `meta.json`

PyPSA export metadata. The model is a single, fixed investment period using
EPSG:4326. `meta.json` is empty.

### `buses.csv`

The `Tamil_Nadu` bus implements the report's single-node assumption. The
230 kV nominal voltage and coordinates 78.6569, 11.1271 are dummy display
values; voltage does not affect this one-node dispatch model. Internal lines,
links, and transformers are intentionally absent because the report assumes no
internal transmission bottleneck.

### `snapshots.csv`

Contains all 8,760 FY 2025-26 hours. Objective, generator, and storage weights
are one for every hourly snapshot.

### `loads.csv` and `loads-p_set.csv`

`Tamil_Nadu_demand` is connected to the single bus. Every `p_set` value is a
workbook-sourced hourly demand in MW assembled as described above.

### `generators.csv`

#### Reported or report-derived settings

| Technology | `p_min_pu` | `p_max_pu` | Normal ramp limit | Basis |
|---|---:|---:|---:|---|
| Coal | 0.55 | 0.85 | 0.60 p.u./h | Report: 55% minimum, 85% availability, 1%/min ramp |
| Gas | 0.40 | 0.90 | 1.00 p.u./h | Report: 40% minimum, 90% availability, 5%/min ramp; hourly value capped at 1 |
| Biomass | 0.50 | 0.60 | 1.00 p.u./h | Report: 50% minimum, 60% availability, 2%/min ramp; hourly value capped at 1 |
| Nuclear | 0.61201 | 0.61201 | Not set | Derived from 7,763 GWh projected annual generation and constant operation |

Additional annual limits:

- Hydro `e_sum_max`: 5,528,000 MWh, from Figure 13.
- STOA/MTOA `e_sum_max`: 17,752,000 MWh, from Figure 13.

#### Dummy unit-commitment settings

| Technology | Minimum up/down | Start-up cost | Shut-down cost | Start/shut ramp |
|---|---:|---:|---:|---:|
| Coal | 8 h / 8 h | INR 5,000,000 | INR 1,000,000 | 0.55 p.u. |
| Gas | 2 h / 2 h | INR 500,000 | INR 100,000 | 0.40 p.u. |
| Biomass | 4 h / 4 h | INR 200,000 | INR 50,000 | 0.50 p.u. |

All representative thermal blocks start offline (`p_init=0`) and are assumed to
have been down for at least their minimum down time. Physical unit capacities,
initial conditions, and UC parameters were not supplied.

#### Dummy operating costs

| Technology | Marginal cost (INR/MWh) |
|---|---:|
| Coal blocks | 2,950-3,225 |
| Gas blocks | 6,000-6,050 |
| Biomass blocks | 4,500-4,525 |
| Nuclear | 800 |
| Hydro | 300 |
| Wind, solar, and DRE | 0 |
| STOA/MTOA import | 6,500 |
| Unserved energy | 100,000 |

The CEA report does not provide sufficient unit fuel prices, variable O&M,
no-load costs, or start costs to calculate these operational costs.

### `generators-p_max_pu.csv`

Hourly generation availability shapes are **dummy**. Their annual sums are
normalised to the report's FY 2025-26 projected generation values:

| Column | Capacity | Available annual energy | Implied CF | Provenance |
|---|---:|---:|---:|---|
| `wind_fleet` | 9,631 MW | 13,544 GWh | 16.054% | Annual energy reported; hourly shape dummy |
| `solar_utility` | 11,106 MW | 18,844 GWh | 19.369% | Annual energy reported; hourly shape dummy |
| `solar_DRE_rooftop` | 2,353 MW | 2,982 GWh | 14.467% | Annual energy reported; hourly shape dummy |
| `hydro_DRE` | 58 MW | 86 GWh | 16.926% | Annual energy reported; hourly shape dummy |
| `hydro_reservoir` | 1,886 MW | Availability profile only | n/a | Hourly shape dummy; annual energy limited separately |

The solar shape follows daylight and a dummy seasonal modifier; wind and hydro
use deterministic dummy seasonal and intraday shapes. These profiles are not
observations and must be replaced when actual generation data are supplied.

### `storage_units.csv`

The report lists 400 MW PSP capacity but does not specify its existing energy
capacity in Table 15. The model assumes:

- 6 hours / 2,400 MWh energy capacity: dummy.
- 80% round-trip efficiency: reported; split symmetrically into 89.443% charge
  and discharge efficiencies.
- 95% availability applied as +/-0.95 charge/discharge limits: report-derived.
- Cyclic annual state of charge: modelling assumption.
- INR 10/MWh marginal discharge cost: dummy.

## Material data still missing

1. Actual hourly wind, utility solar, rooftop solar, hydro, and hydro DRE
   generation or availability profiles.
2. Physical generating-unit list and actual unit capacities.
3. Unit heat rates/efficiencies, fuel prices, variable O&M, no-load costs,
   start/shut costs, minimum up/down times, and initial commitment states.
4. Auxiliary consumption; report technology-level values are not yet applied.
5. Hydro inflows, reservoir energy and level constraints, and seasonal water
   budgets.
6. PSP energy capacity, initial state of charge, reservoir constraints,
   self-discharge, and cycling/degradation cost.
7. Hourly STOA/MTOA availability, prices, and contract restrictions.
8. Forced outages and the report's stochastic variations for demand and
   renewable generation.
9. Operating reserve constraints, the 4% planning reserve margin, RCO
   constraints, LOLP, and EENS/NENS calculations.
10. Generator efficiency defaults to 1 and standby/capital/fixed costs default
    to zero where absent; these are not validated assumptions.

## Solver and consistency notes

The supplied notebook solves only the peak-demand week because a monolithic
8,760-hour mixed-integer solve is substantially larger. Use a full-year or
rolling-horizon formulation only after defining suitable boundary conditions,
horizon length, overlap, and treatment of annual energy budgets.

With PyPSA 1.2.4, the peak-week solve completes optimally with HiGHS 1.15.0 but
emits non-blocking consistency warnings:

- bus, generator, load, and storage carrier names are used without rows in a
  `carriers.csv` component table;
- `p_init=0` is ignored for committable generators whose
  `down_time_before` already specifies their pre-horizon state; and
- PyPSA warns that the default for `include_objective_constant` will change in
  a future release.

These warnings do not invalidate the committed solution, but the input schema
and initial-condition convention should be cleaned up before treating the
model as a production study.
