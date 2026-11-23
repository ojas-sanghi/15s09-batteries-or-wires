"""
How feeder_shape.csv was made. Run once; needs the full SMART-DS load profiles (~1.4 GB).
 
Idea:
  - Loads.dss lists every customer with its annual peak kW (and kvar) and the name of its
    load profile. A profile is a year of 15-minute values scaled so its maximum is 1.
  - Customer load at time t = peak kW x profile value at t.
  - Add this up over all customers -> total feeder load at every 15-minute step.
  - Average each hour's four values -> 8760 hourly values.
  - Divide by the sum of all customers' peaks. The result is one shape: if every customer
    used it, the customers would add up to exactly the real feeder load each hour.
  - Do the same for kvar (reactive power). kvar profiles have "_kvar_" where kW ones have "_kw_".
 
Output: feeder_shape.csv, 8760 rows, columns = kW multiplier, kvar multiplier.
"""
 
import re
 
import numpy as np
 
 
# ----------------------------------------------------------------------------
# Settings: where the files are. Change these to match your folders.
# ----------------------------------------------------------------------------
 
LOADS_FILE = "data/P2U/scenarios/base_timeseries/opendss/p2uhs13_1247/p2uhs13_1247--p2udt5108/Loads.dss"
PROFILES_FOLDER = "data/P2U/profiles"
OUTPUT_FILE = "data/feeder_shape.csv"
 
STEPS_PER_YEAR = 35040      # 365 days x 24 hours x 4 fifteen-minute steps
HOURS_PER_YEAR = 8760
 
 
def read_profile(profile_name):
    """Read one profile file: a single column of 35,040 numbers."""
    return np.loadtxt(f"{PROFILES_FOLDER}/{profile_name}.csv")
 
 
def main():
    total_kw = np.zeros(STEPS_PER_YEAR)     # feeder total kW at each 15-minute step
    total_kvar = np.zeros(STEPS_PER_YEAR)
    sum_of_peak_kw = 0.0
    sum_of_peak_kvar = 0.0
    number_of_customers = 0
 
    with open(LOADS_FILE) as f:
        for row in f:
            # Each customer is one row like:
            #   New Load.load_p2ulv3_1 ... kW=4.18 kvar=1.12 Phases=1 yearly=res_kw_11063_pu
            if not row.startswith("New Load."):
                continue
 
            peak_kw = float(re.search(r"kW=(\S+)", row).group(1))
            peak_kvar = float(re.search(r"kvar=(\S+)", row).group(1))
            kw_profile_name = re.search(r"yearly=(\S+)", row).group(1)
            kvar_profile_name = kw_profile_name.replace("_kw_", "_kvar_")
 
            total_kw += peak_kw * read_profile(kw_profile_name)
            total_kvar += peak_kvar * read_profile(kvar_profile_name)
            sum_of_peak_kw += peak_kw
            sum_of_peak_kvar += peak_kvar
            number_of_customers += 1
 
    # 15-minute values -> hourly averages (4 values per hour)
    hourly_kw = total_kw.reshape(HOURS_PER_YEAR, 4).mean(axis=1)
    hourly_kvar = total_kvar.reshape(HOURS_PER_YEAR, 4).mean(axis=1)
 
    # one shape for everyone
    kw_multiplier = hourly_kw / sum_of_peak_kw
    kvar_multiplier = hourly_kvar / sum_of_peak_kvar
 
    shape = np.column_stack([kw_multiplier, kvar_multiplier])
    np.savetxt(OUTPUT_FILE, shape, delimiter=",", fmt="%.6f")
 
    print(f"customers: {number_of_customers}")
    print(f"sum of customer peaks: {sum_of_peak_kw / 1000:.2f} MW")
    print(f"actual feeder peak:    {hourly_kw.max() / 1000:.2f} MW")
    print(f"saved {OUTPUT_FILE}")
 
 
if __name__ == "__main__":
    main()
 