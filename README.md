# Step 1 of the proposal, with one load shape for every customer

Code drafted with Claude (AI): disclose in the AI acknowledgement.

```bash
pip install -r requirements.txt
python download.py   # the feeder's 6 network files from SMART-DS (about 3 MB, public, no login)
python step1.py      # about 1.5 minutes
python plot.py 1.3   # map of the feeder, overloaded lines in red -> map_g1.3.png
```

- `feeder_shape.csv`: 8,760 hourly rows, two columns (kW and kvar multipliers). It is the feeder's total
  2018 load from SMART-DS divided by the sum of the customers' peaks (35.6 MW, 8.3 MVAr), so every customer
  times this shape adds up to the real feeder load (peak 16.8 MW). Made once from the SMART-DS profiles by `make_shape.py` (needs the full ~1.4 GB profile download; reproduces the file exactly).
- Simplification beyond the proposal: all customers follow the same shape, so homes and businesses peak together.

Data links:
- Feeder folder: https://data.openei.org/s3_viewer?bucket=oedi-data-lake&prefix=SMART-DS%2Fv1.0%2F2018%2FSFO%2FP2U%2Fscenarios%2Fbase_timeseries%2Fopendss%2Fp2uhs13_1247%2Fp2uhs13_1247--p2udt5108%2F
