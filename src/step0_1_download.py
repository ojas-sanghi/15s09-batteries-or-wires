"""Download one SMART-DS feeder's network files (public, no login, about 3 MB)."""

import urllib.request
from pathlib import Path

URL = "https://oedi-data-lake.s3.amazonaws.com/SMART-DS/v1.0/2018/SFO/P2U/scenarios/base_timeseries/opendss/p2uhs13_1247/p2uhs13_1247--p2udt5108/"
Path("data/feeder").mkdir(parents=True, exist_ok=True)
for f in [
    "Master.dss",
    "LineCodes.dss",
    "Lines.dss",
    "Transformers.dss",
    "Loads.dss",
    "Buscoords.dss",
]:
    urllib.request.urlretrieve(URL + f, f"data/feeder/{f}")
print("done")
