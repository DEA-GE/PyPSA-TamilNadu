# ERA5 daily weather at the nine balancing-area centroids

`data/model_inputs/era5_daily_weather_tn_areas_FY2020_26.csv` contains daily
ERA5 reanalysis values from 2020-04-01 to 2026-03-31 (Asia/Kolkata days) at
the centroid of each balancing area in `data/source/TamilNadu_9BAs.gpkg`:

- `ghi_mj_m2`: daily surface shortwave radiation sum (MJ/m²);
- `precipitation_mm`: daily precipitation sum (mm);
- `wind_speed_10m_kmh`: daily mean 10 m wind speed (km/h).

`latitude` and `longitude` are the ERA5 grid point returned for each centroid.
The data were retrieved on 2026-11-18 from the Open-Meteo historical weather
API, which serves Copernicus ERA5 (`models=era5`):

```
https://archive-api.open-meteo.com/v1/archive?latitude=13.20,12.66,10.96,11.50,9.94,8.95,10.75,12.47,12.01&longitude=79.93,79.92,77.06,77.85,78.11,77.72,78.95,78.44,79.26&start_date=2019-04-01&end_date=2026-03-31&daily=shortwave_radiation_sum,temperature_2m_max,temperature_2m_mean,precipitation_sum,wind_speed_10m_mean&models=era5&timezone=Asia%2FKolkata
```

The area order in that request is Chennai North, Chennai South, Coimbatore,
Erode, Madurai, Tirunelveli, Trichy, Vellore, Villupuram. The request started
on 2019-04-01; rows before 2020-04-01 were dropped because FY2019-20 is not
used. Temperatures were requested but are not stored: atlite already accounts
for them. The values are
used by `scripts/solar_seasonal_correction.py`.
