"""How feeder_shape.csv was made (one time; needs the full SMART-DS profiles, ~1.4 GB).

Feeder load each hour = sum over customers of (peak kW from Loads.dss) x (that customer's
15-minute profile), averaged to hourly. Dividing by the sum of the peaks gives one shape that,
applied to every customer, reproduces the feeder total. Same for kvar.
Expects the bucket layout: data/P2U/scenarios/.../Loads.dss and data/P2U/profiles/*.csv
"""

import re
from pathlib import Path

import numpy as np

FEEDER = Path(
    "data/P2U/scenarios/base_timeseries/opendss/p2uhs13_1247/p2uhs13_1247--p2udt5108"
)
PROFILES = Path("../data/P2U/profiles")

total = {"kw": np.zeros(35040), "kvar": np.zeros(35040)}
peak = {"kw": 0.0, "kvar": 0.0}
for line in (FEEDER / "Loads.dss").read_text().splitlines():
    m = re.search(r"kW=(\S+).*kvar=(\S+).*yearly=(\S+)", line, flags=re.I)
    if not m:
        continue
    kw, kvar, shape = float(m.group(1)), float(m.group(2)), m.group(3)
    total["kw"] += kw * np.loadtxt(PROFILES / f"{shape}.csv")
    total["kvar"] += kvar * np.loadtxt(
        PROFILES / f"{shape.replace('_kw_', '_kvar_')}.csv"
    )
    peak["kw"] += kw
    peak["kvar"] += kvar

hourly = lambda x: x.reshape(8760, 4).mean(axis=1)  # four 15-minute values per hour
shape = np.column_stack(
    [hourly(total["kw"]) / peak["kw"], hourly(total["kvar"]) / peak["kvar"]]
)
np.savetxt("data/feeder_shape.csv", shape, delimiter=",", fmt="%.6f")
print(
    f"feeder peak {hourly(total['kw']).max() / 1000:.2f} MW; sum of customer peaks {peak['kw'] / 1000:.2f} MW"
)
