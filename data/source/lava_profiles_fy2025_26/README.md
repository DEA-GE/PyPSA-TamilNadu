# LAVA/atlite wind and solar profiles — FY2025–26 source window

Two extracts hold the hourly capacity factors of the nine balancing areas from
the LAVA workflow, 2025-03-31 18:00 to 2026-04-01 00:00 UTC. That is the
window the FY2025-26 model hours need, plus margin at each end:

- `lava_solar_9BA_extract_utc.csv`, from
  `D:\LAVA_TamilNadu\data\<Area>\0_profiles\<Area>_solar_9BA_profile_<year>.csv`;
- `lava_wind_AggrPCWiderCUF_extract_utc.csv`, from
  `D:\LAVA_TamilNadu\data\<Area>\0_profiles\<Area>_onshorewind_AggrPCWiderCUF_profile_<year>_<code>aggPC.csv`.

Each takes the 2025 and 2026 files, copied at full precision. `manifest.csv`
records each original file's size, SHA-256, and modification time.

LAVA runs atlite on the ERA5 cutout `TamilNadu-2025-era5.nc`:

- solar uses the settings in `technologies_solar_9BA_<year>.txt`: panel `CSi`,
  orientation `latitude_optimal`, no tracking, `clip_p_max_pu: 0.01`;
- wind uses `technologies_onshorewind_AggrPCWiderCUF_<year>_<code>aggPC.txt`:
  turbine `<code>aggPC`, cutout wind speed added.

The cutout's time axis is UTC, and `energy_profiles.py` keeps those
timestamps. The two technologies are labelled differently:

- solar: the value at `T` is the mean over the hour ending at `T`, because
  ERA5 radiation is accumulated;
- wind: the value at `T` is instantaneous, because ERA5 100 m wind is.

`scripts/prepare_lava_profiles.py` converts both to the model's
hour-beginning IST hours, without wrapping the year:

- solar: the mean of the two atlite hours each IST hour overlaps;
- wind: the value at the middle of each IST hour, 5 hours earlier in UTC.

It writes `data/model_inputs/renewable_profiles.csv` and the FY wind table
`data/model_inputs/Onshorewind_9BAs_AggrPCWiderCUF_FY2025-26.csv`. To rebuild
the extracts from the LAVA folder, run
`python scripts/prepare_lava_profiles.py --extract-from D:\LAVA_TamilNadu\data`.
