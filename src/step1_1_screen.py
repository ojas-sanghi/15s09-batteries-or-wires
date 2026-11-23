"""
Proposal Step 1: AC headroom screen.

For each load-growth level g, we:
  1. scale every customer's load by g,
  2. run an AC power flow in OpenDSS for every hour of a few representative days
     (k-means days plus the single highest-load day of the year, see pick_representative_days),
  3. record how loaded each 12.47 kV line gets compared with its rating.

Simplification beyond the proposal: every customer follows the same hourly load shape
(feeder_shape.csv). See README.md for how that file was made.

Before running:  python download.py   (gets the feeder files into the folder "feeder/")
"""

import re

import numpy as np
import opendssdirect as dss
import pandas as pd
from sklearn.cluster import KMeans


# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------

GROWTH_LEVELS = [1.0, 1.1, 1.2, 1.3, 1.4, 1.5]  # load-growth multipliers g to test
NUMBER_OF_REP_DAYS = 5  # representative days picked by k-means (the peak day is added on top)
FEEDER_FOLDER = "data/feeder"
PRIMARY_VOLTAGE_KV = 7.2  # 12.47 kV line-to-line = 7.2 kV line-to-neutral


# ----------------------------------------------------------------------------
# Part 1: pick representative days
# ----------------------------------------------------------------------------


def pick_representative_days():
    """
    Take out the single highest-load day of the year (the day with the highest hourly kW
    multiplier) and keep it as its own representative day with weight 1, because k-means
    would average it away and the peak is what decides whether a line is overloaded.

    Group the other 364 days into clusters of days that look alike (k-means),
    and pick the real day closest to the middle of each cluster.
    The weight of a day = how many days its cluster contains. The weights add up to 365.
    """
    # feeder_shape.csv has 8760 rows (one per hour) and 2 columns:
    #   column 1 = kW multiplier, column 2 = kvar multiplier
    shape = np.loadtxt("data/feeder_shape.csv", delimiter=",")
    kw_multiplier = shape[:, 0]

    # Rearrange the 8760 hourly values into 365 rows (days) of 24 values (hours).
    days_by_hours = kw_multiplier.reshape(365, 24)

    # The peak day is the day that contains the highest hour of the year.
    peak_hour = np.argmax(kw_multiplier)
    peak_day = int(peak_hour // 24)

    # Every other day goes into k-means.
    other_days = []
    for day in range(365):
        if day != peak_day:
            other_days.append(day)

    kmeans = KMeans(n_clusters=NUMBER_OF_REP_DAYS, n_init=20, random_state=0)
    kmeans.fit(days_by_hours[other_days])

    rep_days = []
    weights = []
    for cluster in range(NUMBER_OF_REP_DAYS):
        # k-means numbers the days 0..363 in the order of other_days,
        # so turn those positions back into days of the year
        positions_in_cluster = np.where(kmeans.labels_ == cluster)[0]
        days_in_cluster = []
        for position in positions_in_cluster:
            days_in_cluster.append(other_days[position])
        center = kmeans.cluster_centers_[cluster]

        # find the real day in this cluster that is closest to the cluster center
        best_day = None
        best_distance = np.inf
        for day in days_in_cluster:
            distance = np.sum((days_by_hours[day] - center) ** 2)
            if distance < best_distance:
                best_distance = distance
                best_day = day

        rep_days.append(int(best_day))
        weights.append(len(days_in_cluster))

    # add the peak day last, standing for only itself
    rep_days.append(peak_day)
    weights.append(1)

    return rep_days, weights


# ----------------------------------------------------------------------------
# Part 2: load the feeder into OpenDSS
# ----------------------------------------------------------------------------


def load_feeder():
    """
    Load the SMART-DS feeder into OpenDSS, giving every customer the same load shape.
    """
    # SMART-DS gives each customer its own load shape ("yearly=..." in Loads.dss).
    # We remove those references and write a copy of the file without them.
    with open(f"{FEEDER_FOLDER}/Loads.dss") as f:
        loads_text = f.read()
    loads_text = re.sub(r"\s+yearly=\S+", "", loads_text)
    with open(f"{FEEDER_FOLDER}/Loads_oneshape.dss", "w") as f:
        f.write(loads_text)

    # Master.dss is the file that builds the circuit. We send its commands to OpenDSS
    # one line at a time, but skip:
    #   - the lines that solve, plot or export (we do our own solving below)
    #   - the line that loads the per-customer load shapes (we use one shape instead)
    with open(f"{FEEDER_FOLDER}/Master.dss") as f:
        master_lines = f.read().splitlines()

    for line in master_lines:
        lower = line.lower()
        if (
            lower.startswith("solve")
            or lower.startswith("plot")
            or lower.startswith("export")
        ):
            continue
        if "loadshapes.dss" in lower:
            continue

        # use our copy of the loads file
        line = line.replace("Loads.dss", "Loads_oneshape.dss")
        # the other files live in the feeder folder
        line = line.replace("Redirect ", f"Redirect {FEEDER_FOLDER}/")
        line = line.replace("Buscoords ", f"Buscoords {FEEDER_FOLDER}/")

        dss.Text.Command(line)

    # Create one load shape from feeder_shape.csv and give it to every load.
    dss.Text.Command(
        "New Loadshape.feeder npts=8760 interval=1 "
        "mult=(file=data/feeder_shape.csv, col=1) "
        "qmult=(file=data/feeder_shape.csv, col=2)"
    )
    dss.Text.Command("batchedit load..* yearly=feeder")


# ----------------------------------------------------------------------------
# Part 3: list the 12.47 kV lines and their ratings
# ----------------------------------------------------------------------------


def get_primary_lines():
    """
    Return a table of the 12.47 kV ("primary") lines: name, number of phases,
    current rating (amps) and rating in MVA. Low-voltage customer lines are skipped.
    """
    rows = []
    for name in dss.Lines.AllNames():
        dss.Lines.Name(name)  # make this line the active one
        dss.Circuit.SetActiveBus(dss.Lines.Bus1())  # look at the bus it starts from

        bus_voltage_kv = dss.Bus.kVBase()
        is_primary = abs(bus_voltage_kv - PRIMARY_VOLTAGE_KV) < 0.5
        if not is_primary:
            continue

        rows.append(
            {
                "line": name,
                "phases": dss.Lines.Phases(),
                "rating_amps": dss.Lines.NormAmps(),
            }
        )

    lines = pd.DataFrame(rows)
    # MVA rating = phases x (7.2 kV per phase) x (amps) / 1000
    lines["rating_MVA"] = (
        lines["phases"] * PRIMARY_VOLTAGE_KV * lines["rating_amps"] / 1000
    )
    return lines


def worst_phase_current(line_name, phases):
    """Current (amps) in the most loaded phase of a line, from the last power flow."""
    dss.Lines.Name(line_name)
    # CurrentsMagAng returns [magnitude, angle, magnitude, angle, ...] for every phase
    # at both ends of the line. The first `phases` pairs are the sending end.
    mags_and_angles = dss.CktElement.CurrentsMagAng()
    magnitudes = []
    for p in range(phases):
        magnitudes.append(mags_and_angles[2 * p])
    return max(magnitudes)


def lowest_voltage():
    """Lowest voltage (per unit) anywhere on the feeder, from the last power flow."""
    voltages = np.array(dss.Circuit.AllBusMagPu())
    voltages = voltages[voltages > 0.5]  # ignore unused neutral points that read zero
    return voltages.min()


# ----------------------------------------------------------------------------
# Part 4: the screen
# ----------------------------------------------------------------------------


def run_screen(g, rep_days, lines):
    """
    Run an hourly power flow over the representative days with all loads scaled by g.
    Returns a table with one row per line, and the lowest voltage seen.
    """
    # loadmult scales every load; yearly mode makes loads follow their yearly load shape
    dss.Text.Command(f"set loadmult={g}")
    dss.Text.Command("set mode=yearly stepsize=1h number=1")

    number_of_hours = len(rep_days) * 24
    loading = np.zeros(
        (len(lines), number_of_hours)
    )  # current / rating, per line per hour
    lowest_v = np.inf

    hour_index = 0
    for day in rep_days:
        for hour in range(24):
            # tell OpenDSS which hour of the year to solve
            dss.Solution.Hour(24 * day + hour)
            dss.Solution.Seconds(0)
            dss.Solution.Solve()

            for i in range(len(lines)):
                name = lines["line"][i]
                current = worst_phase_current(name, lines["phases"][i])
                loading[i, hour_index] = current / lines["rating_amps"][i]

            lowest_v = min(lowest_v, lowest_voltage())
            hour_index += 1

    peak_loading = loading.max(axis=1)
    report = pd.DataFrame(
        {
            "line": lines["line"],
            "rating_MVA": lines["rating_MVA"],
            "peak_loading_pct": 100 * peak_loading,
            "spare_MVA": lines["rating_MVA"] * (1 - peak_loading),
            "hours_above_rating": (loading > 1).sum(axis=1),
        }
    )
    report = report.sort_values("peak_loading_pct", ascending=False)
    return report, lowest_v


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


def main():
    rep_days, weights = pick_representative_days()
    print("Representative days:", rep_days)
    print("Weights (days each one stands for):", weights)

    load_feeder()
    lines = get_primary_lines()
    print(f"{len(lines)} primary lines")

    summary = []
    for g in GROWTH_LEVELS:
        report, lowest_v = run_screen(g, rep_days, lines)
        report.to_csv(f"output/step1_g{g}.csv", index=False)

        overloaded = report[report["hours_above_rating"] > 0]
        result = {
            "g": g,
            "overloaded_lines": len(overloaded),
            "worst_loading_pct": round(report["peak_loading_pct"].max(), 1),
            "min_voltage_pu": round(lowest_v, 4),
        }
        summary.append(result)
        print(result)

    pd.DataFrame(summary).to_csv("output/step1_summary.csv", index=False)


if __name__ == "__main__":
    main()
