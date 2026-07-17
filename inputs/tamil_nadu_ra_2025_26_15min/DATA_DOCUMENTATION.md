# Tamil Nadu FY 2025-26 15-minute inputs

This is the 15-minute-resolution alternative to `../tamil_nadu_ra_2025_26`.
It contains 35,040 snapshots from 2025-04-01 00:00:00 through
2026-03-31 23:45:00. Build it reproducibly with:

```powershell
python scripts/build_15min_model.py
```

Demand is assembled from the Tamil Nadu rows of the supplied 2025 and 2026
quarter-hour CSV files, then restricted to the Indian fiscal year: April-
December 2025 followed by January-March 2026. The supplied equal-allocation
values are hourly MWh divided among four quarters, so they are multiplied by
four to recover average MW; the 0.25-hour snapshot weights then recover energy.
The resulting series exactly reproduces the original hourly load in each
quarter. The maximum demand is
19,987.3300 MW at 2025-07-11 16:00:00.

All buses, carriers, generating and storage capacities, costs, availability
assumptions, and annual energy limits are inherited from the hourly model.
Hourly generator availability values are held constant over each set of four
quarter-hours. Snapshot objective, store, and generator weights are 0.25 hours.
Minimum up/down and prior-state durations are multiplied by four because PyPSA
stores them as snapshot counts. Normal up/down ramp limits are multiplied by
0.25 because they apply between consecutive snapshots. Start-up and shut-down
ramp limits are unchanged because they describe the transition capacity, not
an elapsed hourly ramp rate.

Load the model with:

```python
import pypsa

n = pypsa.Network("inputs/tamil_nadu_ra_2025_26_15min")
```
