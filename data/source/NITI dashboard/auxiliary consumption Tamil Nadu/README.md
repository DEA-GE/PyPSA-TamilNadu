# NITI Dashboard auxiliary consumption, Tamil Nadu coal plants, 2008–2018

`Aux-Consumption_Metatable_1795078497354.xlsx` is the India Climate & Energy
Dashboard (https://iced.niti.gov.in) export of auxiliary power consumption of
Tamil Nadu coal plants, in % of gross generation, by fiscal year, plant and
plant vintage, 2008-09 to 2017-18. It was supplied by the project team on
2026-11-20. The latest year, 2017-18, is the last edition of CEA's *Review of
Performance of Thermal Power Stations*, its source.

A second export, `Aux-Consumption_Metatable_1795169610066.xlsx` (not stored),
holds only the 2010–2019-vintage plants; all its 22 rows are in this file with
the same values.

Plants are reported by name and vintage, so Mettur TPS (1980s units 1–4,
2010s unit 5) has two series. North Chennai TPS has only the 1990s units
(Stage I); Stage II and III, NNTPS, SEPC, OPG, TCP and Tulsyan are missing.
`scripts/prepare_coal_outages.py` uses the latest value per plant and vintage
and fills the missing units from TNERC and CERC norms
(`data/model_inputs/coal_auxiliary_consumption.csv`).
