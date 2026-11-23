# Step 1, part 2: AC screen.
#
# Pick a few representative days. Then for each growth level g, scale every load by g, run an
# AC power flow in OpenDSS for every hour of those days, and record how loaded each 12.47 kV
# line gets compared with its rating.
#
#   output/rep_days.csv        the representative days and how many days each stands for
#   output/screen.csv          one row per line per g
#   output/screen_summary.csv  one row per g
#
# Run after step1_1_export.py:  python src/step1_2_screen.py

import numpy as np
import opendssdirect as dss
import pandas as pd
from sklearn.cluster import KMeans

from common import GROWTH_LEVELS, load_feeder

# ---- representative days ----
# The peak day (the day with the highest hour) is kept as its own day with weight 1,
# because k-means would average it away. The other 364 days are grouped into 5 clusters,
# and from each cluster we take the real day closest to its center. The weight of that
# day is the number of days in its cluster.
kw = np.loadtxt("data/feeder_shape.csv", delimiter=",")[:, 0]
days = kw.reshape(365, 24)  # one row per day, one column per hour
peak_day = int(np.argmax(kw) // 24)
other_days = [d for d in range(365) if d != peak_day]

kmeans = KMeans(n_clusters=5, n_init=20, random_state=0)
kmeans.fit(days[other_days])

rep_days = []
weights = []
for c in range(5):
    members = []
    for i in range(len(other_days)):
        if kmeans.labels_[i] == c:
            members.append(other_days[i])
    distances = [np.sum((days[d] - kmeans.cluster_centers_[c]) ** 2) for d in members]
    rep_days.append(members[np.argmin(distances)])
    weights.append(len(members))
rep_days.append(peak_day)
weights.append(1)

print("representative days:", rep_days)
print("weights:", weights)
pd.DataFrame({"day": rep_days, "weight": weights}).to_csv("output/rep_days.csv", index=False)

# ---- the screen ----
load_feeder()
lines = pd.read_csv("output/network_lines.csv")

screen = []
summary = []
for g in GROWTH_LEVELS:
    dss.Text.Command(f"set loadmult={g}")  # scales every load by g
    dss.Text.Command("set mode=yearly stepsize=1h number=1")

    peak_loading = np.zeros(len(lines))  # highest current / rating seen on each line
    hours_over = np.zeros(len(lines), dtype=int)  # hours above the rating
    lowest_v = 10.0

    for day in rep_days:
        for hour in range(24):
            dss.Solution.Hour(24 * day + hour)
            dss.Solution.Seconds(0)
            dss.Solution.Solve()

            for i in range(len(lines)):
                dss.Lines.Name(lines["line"][i])
                # [magnitude, angle, magnitude, angle, ...]; the first `phases` pairs
                # are the sending end of the line
                currents = dss.CktElement.CurrentsMagAng()
                amps = max(currents[0 : 2 * lines["phases"][i] : 2])
                loading = amps / lines["rating_amps"][i]
                peak_loading[i] = max(peak_loading[i], loading)
                if loading > 1:
                    hours_over[i] += 1

            voltages = np.array(dss.Circuit.AllBusMagPu())
            lowest_v = min(lowest_v, voltages[voltages > 0.5].min())  # skip unused neutrals

    result = pd.DataFrame(
        {
            "g": g,
            "line": lines["line"],
            "rating_MVA": lines["rating_MVA"],
            "peak_loading_pct": 100 * peak_loading,
            "spare_MVA": lines["rating_MVA"] * (1 - peak_loading),
            "hours_above_rating": hours_over,
        }
    )
    screen.append(result.sort_values("peak_loading_pct", ascending=False))

    row = {
        "g": g,
        "overloaded_lines": int((hours_over > 0).sum()),
        "worst_loading_pct": round(100 * peak_loading.max(), 1),
        "min_voltage_pu": round(lowest_v, 4),
    }
    summary.append(row)
    print(row)

pd.concat(screen).to_csv("output/screen.csv", index=False)
pd.DataFrame(summary).to_csv("output/screen_summary.csv", index=False)
