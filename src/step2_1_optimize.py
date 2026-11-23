"""
Proposal Step 2, part 1: the optimization model of Section 3 (batteries or wires?).

For one load-growth level g, choose
  - where to put batteries and how big (y_n, E_n for n in B(g)),
  - which lines to upgrade (z_ij for (i,j) in U),
  - how every battery charges and discharges each hour of the representative days,
so that the total yearly cost is as small as possible and every line, voltage and battery
limit holds. The model is exactly the one in the proposal:

  min  sum_n (c_E E_n + F_n y_n) + sum_U C_U z  +  sum_d w_d sum_t (price_dt P_01,dt + c_deg sum_n p_dis)

  (1) active power balance at every bus        P_in - P_out = load + charge - discharge
  (2) reactive power balance at every bus      Q_in - Q_out = tan(phi) x load
  (3) voltage drop along every line            v_j = v_i - 2 (r P + x Q),  v_0 = V0^2,  0.95^2 <= v <= 1.05^2
  (4) thermal limit, 16-sided polygon          cos(th_k) P + sin(th_k) Q <= cos(pi/16) (S + dS z)
  (5) battery state of charge, cyclic per day  s_t = s_{t-1} + eta_c p_ch - p_dis / eta_d,  s_0 = s_24
  (6) battery energy and power limits          s <= E,  p_ch <= E/4,  p_dis <= E/4
  (7) capacity only where a site is built      E <= E_max y

Inputs:  output/network_lines.csv, output/network_buses.csv   (step1_2_export.py)
         output/upgrades_g{g}.csv, output/battery_sites_g{g}.csv (step1_3_candidates.py)
         data/feeder_shape.csv
Outputs: output/step2_g{g}_{plan}_summary.csv     costs and totals
         output/step2_g{g}_{plan}_batteries.csv   batteries built (bus, MWh, MW)
         output/step2_g{g}_{plan}_upgrades.csv    lines upgraded

Run from the repo root:
    python src/step2_1_optimize.py 1.3            (co-optimize batteries and wires at g = 1.3)
    python src/step2_1_optimize.py 1.3 wires      (upgrades only, no batteries allowed)
    python src/step2_1_optimize.py 1.3 batteries  (batteries only, no upgrades allowed)
Needs Gurobi (gurobipy) with a full license: the model is far bigger than the free
size-limited license allows.
"""

import math
import sys

import gurobipy as gp
import numpy as np
import pandas as pd
from gurobipy import GRB

from step1_1_screen import pick_representative_days


# ----------------------------------------------------------------------------
# Settings (all costs in 2026 dollars)
# ----------------------------------------------------------------------------

DISCOUNT_RATE = (
    0.07  # real discount rate, used to turn up-front costs into yearly costs
)


def capital_recovery_factor(rate, years):
    """Yearly payment that pays back $1 over `years` years at interest `rate`."""
    return rate * (1 + rate) ** years / ((1 + rate) ** years - 1)


# --- batteries (NREL ATB 2024, utility-scale 4-hour, Moderate, 2026) ---
BATTERY_LIFE_YEARS = 15
DURATION_H = 4  # h: power rating = energy / 4
CAPITAL_COST_PER_KW = 1500.90  # $/kW, overnight capital cost
FIXED_OM_PER_KW_YEAR = 37.52  # $/kW-yr, fixed operation and maintenance
# one MWh of a 4-hour battery = 0.25 MW = 250 kW of power rating
C_E = (CAPITAL_COST_PER_KW * 1000 / DURATION_H) * capital_recovery_factor(
    DISCOUNT_RATE, BATTERY_LIFE_YEARS
) + FIXED_OM_PER_KW_YEAR * 1000 / DURATION_H  # $/MWh-yr, about 50,600
ROUND_TRIP_EFFICIENCY = 0.85
ETA_CHARGE = math.sqrt(ROUND_TRIP_EFFICIENCY)  # losses split evenly between charging
ETA_DISCHARGE = math.sqrt(ROUND_TRIP_EFFICIENCY)  # and discharging
C_DEG = 0.0  # $/MWh discharged (ATB lists no variable O&M)
E_MAX = 40.0  # MWh, largest battery at one site (10 MW at 4 h)
# fixed cost of opening a site: one pad-mounted switch + one 1,500 kVA pad transformer
# (PG&E Unit Cost Guide, March 2026)
SITE_COST = 93_417 + 138_188  # $
F_SITE = SITE_COST * capital_recovery_factor(DISCOUNT_RATE, BATTERY_LIFE_YEARS)  # $/yr

# --- line upgrades (PG&E Unit Cost Guide, March 2026, Bay Area) ---
WIRE_LIFE_YEARS = 40
OVERHEAD_COST_PER_FT = 241  # $/ft, reconductor an overhead line
UNDERGROUND_COST_PER_FT = 366  # $/ft, reconductor an underground line
SWITCH_COST = 93_417  # $, replace a switch
RECLOSER_COST = 165_000  # $, replace a recloser
FUSE_COST = 20_003  # $, replace a fuse
FEET_PER_KM = 3280.84
UPGRADE_FACTOR = 2.0  # an upgraded line gets UPGRADE_FACTOR x its old rating

# --- electricity price at the substation ---
PRICE = 40.0  # $/MWh, the same every hour for now

# --- network ---
BASE_KV = 12.47  # line-to-line voltage
BASE_MVA = 1.0  # per-unit base, so 1 MW = 1 p.u.
BASE_OHM = BASE_KV**2 / BASE_MVA  # 155.5 ohm
V0 = 1.03  # substation voltage set-point (p.u., from Master.dss)
V_MIN, V_MAX = 0.95, 1.05
K = 16  # sides of the thermal polygon

# --- solver ---
TIME_LIMIT_S = 1800
MIP_GAP = 1e-4


# ----------------------------------------------------------------------------
# Part 1: read the inputs
# ----------------------------------------------------------------------------


def upgrade_cost(line):
    """Up-front cost ($) of upgrading one line, from what kind of element it is."""
    name = line["line"]
    if name.startswith("l("):
        # a real stretch of wire: pay per foot to reconductor it
        if "_oh_" in line["linecode"].lower():
            cost_per_ft = OVERHEAD_COST_PER_FT
        else:
            cost_per_ft = UNDERGROUND_COST_PER_FT
        return cost_per_ft * line["length_km"] * FEET_PER_KM
    if "recloser" in name:
        return RECLOSER_COST
    if "fuse" in name:
        return FUSE_COST
    return SWITCH_COST  # padswitch, disswitch, elbswitch, ...


def read_inputs(g, plan):
    lines = pd.read_csv("output/network_lines.csv")
    buses = pd.read_csv("output/network_buses.csv", keep_default_na=False)
    upgrades = pd.read_csv(f"output/upgrades_g{g}.csv")
    sites = pd.read_csv(f"output/battery_sites_g{g}.csv")

    # the plan decides which kinds of investment are allowed
    if plan == "wires":
        sites = sites.iloc[0:0]  # no battery sites
    if plan == "batteries":
        upgrades = upgrades.iloc[0:0]  # no upgrades

    # hourly kW multiplier for the representative days
    shape = np.loadtxt("data/feeder_shape.csv", delimiter=",")
    kw_multiplier = shape[:, 0]
    rep_days, weights = pick_representative_days()

    return lines, buses, upgrades, sites, kw_multiplier, rep_days, weights


# ----------------------------------------------------------------------------
# Part 2: build the model
# ----------------------------------------------------------------------------


def build_model(g, lines, buses, upgrades, sites, kw_multiplier, rep_days, weights):
    m = gp.Model("batteries_or_wires")

    # --- index sets ---
    substation = buses["bus"][0]  # first row of the bus table
    bus_list = list(buses["bus"])
    line_list = list(lines["line"])
    U = list(upgrades["line"])  # candidate upgrades
    B = list(sites["bus"])  # candidate battery buses
    D = range(len(rep_days))  # representative days
    T = range(24)  # hours

    # --- look-up tables ---
    from_bus = dict(zip(lines["line"], lines["from_bus"]))
    to_bus = dict(zip(lines["line"], lines["to_bus"]))
    r_pu = dict(zip(lines["line"], lines["r_ohm"] / BASE_OHM))
    x_pu = dict(zip(lines["line"], lines["x_ohm"] / BASE_OHM))
    rating = dict(zip(lines["line"], lines["rating_MVA"] / BASE_MVA))
    peak_P = dict(zip(buses["bus"], buses["peak_P_MW"]))
    peak_Q = dict(zip(buses["bus"], buses["peak_Q_MVAr"]))

    # the line into each bus (from its parent), and the lines out of it (to its children)
    line_in = {}
    lines_out = {bus: [] for bus in bus_list}
    for line in line_list:
        line_in[to_bus[line]] = line
        lines_out[from_bus[line]].append(line)

    # load at bus j, day d, hour t:  P_D = g x peak_P_j x multiplier for that hour
    def load_P(j, d, t):
        hour_of_year = 24 * rep_days[d] + t
        return g * peak_P[j] * kw_multiplier[hour_of_year]

    # reactive load = tan(phi_j) x active load, with tan(phi_j) = peak_Q / peak_P at the bus
    def load_Q(j, d, t):
        if peak_P[j] == 0:
            return 0.0
        return (peak_Q[j] / peak_P[j]) * load_P(j, d, t)

    # --- decision variables ---
    # planning
    E = m.addVars(B, lb=0, ub=E_MAX, name="E")  # battery energy (MWh)
    y = m.addVars(B, vtype=GRB.BINARY, name="y")  # battery site built
    z = m.addVars(U, vtype=GRB.BINARY, name="z")  # line upgraded
    # operation (every day d and hour t)
    p_ch = m.addVars(B, D, T, lb=0, name="p_ch")  # charging (MW)
    p_dis = m.addVars(B, D, T, lb=0, name="p_dis")  # discharging (MW)
    s = m.addVars(B, D, T, lb=0, name="s")  # state of charge (MWh)
    # power flows can go either way, so they need negative lower bounds
    P = m.addVars(line_list, D, T, lb=-GRB.INFINITY, name="P")  # active flow (MW)
    Q = m.addVars(line_list, D, T, lb=-GRB.INFINITY, name="Q")  # reactive flow (MVAr)
    v = m.addVars(bus_list, D, T, lb=V_MIN**2, ub=V_MAX**2, name="v")  # squared voltage

    # --- objective ---
    battery_cost = gp.quicksum(C_E * E[n] + F_SITE * y[n] for n in B)
    upgrade_costs = dict(
        zip(upgrades["line"], [upgrade_cost(row) for _, row in upgrades.iterrows()])
    )
    wire_cost = gp.quicksum(
        upgrade_costs[l]
        * capital_recovery_factor(DISCOUNT_RATE, WIRE_LIFE_YEARS)
        * z[l]
        for l in U
    )
    lines_leaving_substation = lines_out[substation]
    energy_cost = gp.quicksum(
        weights[d] * PRICE * P[l, d, t]
        for d in D
        for t in T
        for l in lines_leaving_substation
    )
    wear_cost = gp.quicksum(
        weights[d] * C_DEG * p_dis[n, d, t] for n in B for d in D for t in T
    )
    m.setObjective(battery_cost + wire_cost + energy_cost + wear_cost, GRB.MINIMIZE)

    # --- constraints ---
    for d in D:
        for t in T:
            # (1) and (2): power balance at every bus except the substation
            for j in bus_list:
                if j == substation:
                    continue
                battery = 0
                if j in E:
                    battery = p_ch[j, d, t] - p_dis[j, d, t]
                m.addConstr(
                    P[line_in[j], d, t] - gp.quicksum(P[k, d, t] for k in lines_out[j])
                    == load_P(j, d, t) + battery
                )
                m.addConstr(
                    Q[line_in[j], d, t] - gp.quicksum(Q[k, d, t] for k in lines_out[j])
                    == load_Q(j, d, t)
                )

            # (3): fixed substation voltage, and voltage drop along every line
            v[substation, d, t].lb = V0**2
            v[substation, d, t].ub = V0**2
            for l in line_list:
                i, j = from_bus[l], to_bus[l]
                m.addConstr(
                    v[j, d, t]
                    == v[i, d, t] - 2 * (r_pu[l] * P[l, d, t] + x_pu[l] * Q[l, d, t])
                )

            # (4): thermal limit as a 16-sided polygon inside the circle P^2 + Q^2 <= S^2
            for l in line_list:
                limit = rating[l]
                if l in z:
                    extra = (UPGRADE_FACTOR - 1) * rating[
                        l
                    ]  # Delta S, added if upgraded
                    limit = rating[l] + extra * z[l]
                for k in range(K):
                    theta = 2 * math.pi * k / K
                    m.addConstr(
                        math.cos(theta) * P[l, d, t] + math.sin(theta) * Q[l, d, t]
                        <= math.cos(math.pi / K) * limit
                    )

            # (5) and (6): batteries
            for n in B:
                previous_hour = (
                    t - 1
                ) % 24  # hour 0 follows hour 23: each day is a cycle
                m.addConstr(
                    s[n, d, t]
                    == s[n, d, previous_hour]
                    + ETA_CHARGE * p_ch[n, d, t]
                    - p_dis[n, d, t] / ETA_DISCHARGE
                )
                m.addConstr(s[n, d, t] <= E[n])
                m.addConstr(p_ch[n, d, t] <= E[n] / DURATION_H)
                m.addConstr(p_dis[n, d, t] <= E[n] / DURATION_H)

    # (7): battery capacity only where a site is built
    for n in B:
        m.addConstr(E[n] <= E_MAX * y[n])

    variables = {"E": E, "y": y, "z": z, "p_dis": p_dis, "P": P, "v": v}
    costs = {
        "battery": battery_cost,
        "wires": wire_cost,
        "energy": energy_cost,
        "wear": wear_cost,
    }
    return m, variables, costs, upgrade_costs


# ----------------------------------------------------------------------------
# Part 3: solve and write out the plan
# ----------------------------------------------------------------------------


def report(g, plan, m, variables, costs, upgrade_costs, upgrades):
    if m.SolCount == 0:
        print(f"no feasible plan found (Gurobi status {m.Status})")
        if m.Status == GRB.INFEASIBLE:
            print(
                "the model is infeasible: the allowed batteries/upgrades cannot fix every overload"
            )
        return

    E, y, z = variables["E"], variables["y"], variables["z"]

    batteries = []
    for n in E:
        if E[n].X > 1e-4:
            batteries.append(
                {"bus": n, "energy_MWh": E[n].X, "power_MW": E[n].X / DURATION_H}
            )
    batteries = pd.DataFrame(batteries, columns=["bus", "energy_MWh", "power_MW"])

    built = []
    for l in z:
        if z[l].X > 0.5:
            built.append(l)
    built_upgrades = upgrades[upgrades["line"].isin(built)].copy()
    built_upgrades["upfront_cost_usd"] = [
        upgrade_costs[l] for l in built_upgrades["line"]
    ]

    summary = {
        "g": g,
        "plan": plan,
        "total_cost_per_yr": m.ObjVal,
        "battery_cost_per_yr": costs["battery"].getValue(),
        "upgrade_cost_per_yr": costs["wires"].getValue(),
        "energy_cost_per_yr": costs["energy"].getValue(),
        "wear_cost_per_yr": costs["wear"].getValue(),
        "battery_sites": len(batteries),
        "battery_MWh": batteries["energy_MWh"].sum(),
        "lines_upgraded": len(built_upgrades),
        "mip_gap": m.MIPGap,
        "solve_seconds": m.Runtime,
    }

    prefix = f"output/step2_g{g}_{plan}"
    pd.DataFrame([summary]).to_csv(f"{prefix}_summary.csv", index=False)
    batteries.to_csv(f"{prefix}_batteries.csv", index=False)
    built_upgrades.to_csv(f"{prefix}_upgrades.csv", index=False)

    print()
    for key, value in summary.items():
        print(f"{key:>22}: {value}")
    print(f"saved {prefix}_summary.csv, _batteries.csv, _upgrades.csv")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


def main():
    # growth level and plan from the command line (defaults: g = 1.3, both)
    g = float(sys.argv[1]) if len(sys.argv) > 1 else 1.3
    plan = sys.argv[2] if len(sys.argv) > 2 else "both"
    if plan not in ["both", "wires", "batteries"]:
        sys.exit("plan must be both, wires or batteries")

    lines, buses, upgrades, sites, kw_multiplier, rep_days, weights = read_inputs(
        g, plan
    )
    print(
        f"g = {g}, plan = {plan}: {len(upgrades)} candidate upgrades, {len(sites)} candidate battery sites"
    )

    m, variables, costs, upgrade_costs = build_model(
        g, lines, buses, upgrades, sites, kw_multiplier, rep_days, weights
    )
    m.Params.TimeLimit = TIME_LIMIT_S
    m.Params.MIPGap = MIP_GAP
    m.optimize()

    report(g, plan, m, variables, costs, upgrade_costs, upgrades)


if __name__ == "__main__":
    main()
