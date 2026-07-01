# PyPSA two-bus example

This example builds a 220 kV, two-bus network for all 24 hours of 1 January
2025, checks it with a nonlinear AC power flow, and solves a linear optimal
power flow with HiGHS.

## Run

```powershell
python -m pip install -r requirements.txt
python two_bus_model.py
```

The script prints a summary and writes hourly results to `results/power_flow.csv`
and `results/optimization.csv`.

## Assumed transmission line

A transmission connection is necessary for bus 1 to supply bus 2. Since only
the bus coordinates were specified, the model interprets their 100-unit spacing
as 100 km and assumes:

- resistance: 0.1 ohm/km (10 ohm total)
- reactance: 0.4 ohm/km (40 ohm total)
- rating: 100 MVA

PyPSA's current `Network.optimize` interface uses its Kirchhoff formulation:
nodal power balance (KCL) and KVL constraints for independent network cycles.
This two-bus network is radial, so it has no independent cycle and therefore no
non-trivial cycle constraint.

## Tamil Nadu 2035-36 UC model

The external PyPSA CSV inputs are under
`inputs/tamil_nadu_ra_2035_36`. Their provenance, dummy assumptions, data
dictionary, and missing inputs are documented in
`inputs/tamil_nadu_ra_2035_36/DATA_DOCUMENTATION.md`. A styled, browser-friendly
version is available as
`inputs/tamil_nadu_ra_2035_36/DATA_DOCUMENTATION.html`.

## Tamil Nadu FY 2025-26 UC model

The full-year base-year inputs are under `inputs/tamil_nadu_ra_2025_26`. They
combine April-December 2025 and January-March 2026 Tamil Nadu demand from the
provided NITI Aayog ICED workbooks. See
`inputs/tamil_nadu_ra_2025_26/DATA_DOCUMENTATION.md` for data provenance,
validation results, report assumptions, dummy inputs, and remaining gaps. A
browser-friendly version is available at
`inputs/tamil_nadu_ra_2025_26/DATA_DOCUMENTATION.html`.
