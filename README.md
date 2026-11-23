# Batteries or Wires? Step 1 screen and Step 2 siting model, one load shape for every customer

Run from the repo root, in this order:

```bash
uv sync
uv run python src/step0_1_download.py       # feeder files -> data/feeder/ (about 3 MB)
uv run python src/step1_1_export.py         # feeder -> output/network_lines.csv, network_buses.csv
uv run python src/step1_2_screen.py         # AC screen -> output/rep_days.csv, screen.csv, screen_summary.csv
uv run python src/step1_3_candidates.py     # U and B(g) -> output/upgrades.csv, battery_sites.csv
uv run python src/step1_4_plot.py 1.3       # map -> figures/map_g1.3.png
uv run python src/step2_1_optimize.py      # MILP for every g and plan (wires, batteries, both)
```

Step 2 needs Gurobi with a full license. It writes `output/step2_summary.csv`, `step2_batteries.csv`
and `step2_upgrades.csv`, with `g` and `plan` columns.

The growth levels (g = 1.0 and 1.3) and `load_feeder()` are in `src/common.py`.

## Inputs

- `data/feeder/`: one SMART-DS feeder (SFO P2U, p2uhs13_1247--p2udt5108), from `step0_1_download.py`.
- `data/feeder_shape.csv`: 8,760 hourly rows, two columns (kW and kvar multipliers). It is the feeder's
  total 2018 load from SMART-DS divided by the sum of the customers' peaks (35.6 MW, 8.3 MVAr), so every
  customer times this shape adds up to the real feeder load (peak 16.8 MW). Made once by
  `step0_2_make_shape.py` (needs the full ~1.4 GB profile download; reproduces the file exactly).
- Electricity price: a two-level price set in `step2_1_optimize.py`, $50/MWh from 4 pm to 9 pm and
  $35/MWh otherwise (the 2025 CAISO day-ahead averages at DLAP_PGAE-APND are $49.36 and $38.32).

## Simplifications beyond the proposal

- All customers follow the same load shape, so homes and businesses peak together.
- The price is the same every day (peak / off-peak) instead of the hourly day-ahead LMP.

Data links:
- Feeder folder: https://data.openei.org/s3_viewer?bucket=oedi-data-lake&prefix=SMART-DS%2Fv1.0%2F2018%2FSFO%2FP2U%2Fscenarios%2Fbase_timeseries%2Fopendss%2Fp2uhs13_1247%2Fp2uhs13_1247--p2udt5108%2F
