# Hydro water values: findings so far (paused)

Status: paused on 2026-11-19, to be picked up again. Branch `feat/hydro-water-values`.

## How hydro is constrained today

- Weekly CEA energy targets per asset (`GlobalConstraint-CEAHydro-<asset>-<week>`), as
  equality or maximum, over the committed days only.
- The five directly measured reservoirs (Mettur, Periyar, Parambikulam, Sholayar,
  Papanasam) are pinned to the observed level at the end of every day. A balancing
  inflow/outflow costing 1e6 per MWh closes the water balance.
- Turbines have zero marginal cost. There is no water value or end-of-window valuation.

## Done on this branch

The terminal SOC bands with quality-weighted slack penalties were removed from
`scripts/run_9ba_rolling.py`. They were never applied in rolling mode, for two reasons:

- The 5 measured reservoirs are dropped from the bands because they are pinned exactly
  to the observed daily levels.
- The remaining 6 band targets named StorageUnits that no longer exist (`hydro_mettur`,
  `hydro_aliyar`, ...).

In annual mode the bands only duplicated the daily pinning, except in the final hour of
the year. A 4-window relaxed re-run gave identical results.

## Findings

1. **Look-ahead end effect.** In the look-ahead day the water is free. Hydro rises from
   about 300 MW to about 1,150 MW, and forebays and pondages are emptied. Look-ahead prices
   are therefore too low; in window 4 (from 2025-04-22) the look-ahead price was about
   5,800, against about 94,000 in the committed days.

   A test capped look-ahead hydro at the next week's CEA target pro-rata (4 relaxed April
   windows). The committed results barely moved:
   - coal: ≤ 3.3 GWh out of about 1,700 GWh per week;
   - generation cost: ≤ 0.6 %;
   - unserved energy: −0.6 GWh.

   The cap is cheap to add (one `<=` constraint per asset per window). It has not been
   tested in full UC or in the battery scenarios, where the end-of-week state handed to
   the next window may be more sensitive to it.
2. **Implied water values already exist.** They are the duals of the weekly CEA
   constraints. In relaxed window 1 they are about −6,030 per MWh for most assets, which
   is close to the oil/gas cost of 6,025. The exceptions are Sarkarpathy (about −4,450)
   and Suruliyar (about −1,470). The water-bus duals are about 0, because the weekly
   energy target binds rather than the stored water.

   No separate LP is needed to obtain these values, but they must come from relaxed or
   annual runs (see point 3). Replacing the targets with these values is not recommended:
   - for the historical year it is circular;
   - most assets share the same value, so the split between them becomes arbitrary.

   It is more useful to report them as an output.
3. **Full-UC runs have no shadow prices.** Every `.duals.nc` in `full_uc_profiled` is all
   zeros (0 of 110 variables are nonzero, electricity prices included). Any analysis that
   reads prices from full-UC runs is affected. This is the most urgent open item.
4. **Balancing water.** The measured-storage balancing added about 69 GWh of inflow and
   removed about 7 GWh of outflow over the year in the full-UC run. The cause is probably
   the inflow data rather than the method.
5. **Unserved energy with hydro at its caps.** Window 4 sheds 21.5 GWh of load in its
   committed days while hydro is held at its weekly maximum caps. This is a question about
   how the caps are calibrated.

## Possible next steps

1. Find out why full-UC duals are zero: fixed-integer LP re-solve, or prices taken from a
   relaxed run.
2. Report implied water values per asset and week from relaxed or annual runs.
3. Optionally add the look-ahead hydro cap, and test it in full UC and in a battery scenario.
4. Investigate the 69 GWh of balancing inflow.
5. Remove the six legacy `hydro_*` StorageUnit rows from
   `model/hydro_reservoir_parameters.csv` and `hydro_observed_soc.csv` (in
   `build_9ba_model.py`).

Method used for the end-effect test: monkeypatch the rolling runner to save each window's
full 192 hours before the look-ahead is trimmed, then compare a run with and without the
look-ahead cap (`rolling.max_windows=4`, relaxed).
