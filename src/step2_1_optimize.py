# Step 2: the optimization model from Section 3 of the proposal (batteries or wires?).
#
# For each growth level g and each plan, choose where to put batteries and how big (y_n, E_n),
# which lines to upgrade (z_ij), and how the batteries charge and discharge each hour of the
# representative days, so that the yearly cost is as small as possible:
#
#   min  sum_n (c_E E_n + F_n y_n) + sum_U C_U z + sum_d w_d sum_t (price_t P_01,dt + c_deg sum_n p_dis)
#
#   (1) P_in - P_out = load + charge - discharge                 every bus except the substation
#   (2) Q_in - Q_out = tan(phi) x load                            every bus except the substation
#   (3) v_j = v_i - 2 (r P + x Q),  v_0 = V0^2,  0.95^2 <= v <= 1.05^2
#   (4) cos(th_k) P + sin(th_k) Q <= cos(pi/16) (S + dS z)        16-sided polygon, every line
#   (5) s_t = s_(t-1) + eta_c p_ch - p_dis / eta_d,  s_0 = s_24
#   (6) s <= E,  p_ch <= E/4,  p_dis <= E/4
#   (7) E <= E_max y
#
# Plans: "wires" = upgrades only, "batteries" = batteries only, "both" = choose freely.
#
# Writes (one row per run, or per battery / upgrade, with g and plan columns):
#   output/step2_summary.csv, output/step2_batteries.csv, output/step2_upgrades.csv
#
# Needs Gurobi with a full license. Run after step 1:  python src/step2_1_optimize.py

import math

import gurobipy as gp
import numpy as np
import pandas as pd
from gurobipy import GRB

from common import GROWTH_LEVELS

PLANS = ["wires", "batteries", "both"]

# ---- costs (2026 dollars) ----
DISCOUNT_RATE = 0.07


def yearly(cost, years):
    # equal yearly payment that pays back `cost` over `years` years (capital recovery factor)
    r = DISCOUNT_RATE
    return cost * r * (1 + r) ** years / ((1 + r) ** years - 1)


# batteries (NREL ATB 2024, utility-scale 4-hour, Moderate, 2026)
BATTERY_YEARS = 15
DURATION_H = 4  # power rating = energy / 4
COST_PER_KW = 1500.90  # $/kW up front
OM_PER_KW_YEAR = 37.52  # $/kW per year, fixed operation and maintenance
KW_PER_MWH = 1000 / DURATION_H  # one MWh of a 4-hour battery = 250 kW
C_E = yearly(COST_PER_KW * KW_PER_MWH, BATTERY_YEARS) + OM_PER_KW_YEAR * KW_PER_MWH  # $/MWh-yr
ETA_CHARGE = math.sqrt(0.85)  # 85% round trip, losses split evenly
ETA_DISCHARGE = math.sqrt(0.85)
C_DEG = 0.0  # $/MWh discharged (ATB lists no variable O&M)
E_MAX = 40.0  # MWh, largest battery at one site (10 MW)
# opening a site: one pad-mounted switch + one 1,500 kVA pad transformer (PG&E Unit Cost Guide, March 2026)
F_SITE = yearly(93_417 + 138_188, BATTERY_YEARS)  # $/yr

# line upgrades (PG&E Unit Cost Guide, March 2026, Bay Area)
WIRE_YEARS = 40
OVERHEAD_COST_PER_FT = 241  # reconductor an overhead line
UNDERGROUND_COST_PER_FT = 366  # reconductor an underground line
SWITCH_COST = 93_417
RECLOSER_COST = 165_000
FUSE_COST = 20_003
FEET_PER_KM = 3280.84
UPGRADE_FACTOR = 2.0  # an upgraded line gets twice its old rating

# electricity price at the substation ($/MWh): peak 4 pm to 9 pm, off-peak the rest of the day,
# the same every day (round numbers close to the 2025 CAISO day-ahead averages at DLAP_PGAE-APND)
PEAK_PRICE = 50
OFF_PEAK_PRICE = 35


def price(hour):
    if 16 <= hour <= 20:
        return PEAK_PRICE
    return OFF_PEAK_PRICE


# network (per unit on 12.47 kV and 1 MVA, so 1 MW = 1 p.u.)
BASE_OHM = 12.47**2 / 1.0  # 155.5 ohm
V0 = 1.03  # substation voltage (p.u., from Master.dss)
V_MIN = 0.95
V_MAX = 1.05
K = 16  # sides of the thermal polygon

TIME_LIMIT_S = 1800
MIP_GAP = 1e-4

# ---- read the inputs ----
lines = pd.read_csv("output/network_lines.csv")
buses = pd.read_csv("output/network_buses.csv", keep_default_na=False)
all_upgrades = pd.read_csv("output/upgrades.csv")
all_sites = pd.read_csv("output/battery_sites.csv")
rep = pd.read_csv("output/rep_days.csv")
rep_days = list(rep["day"])
weights = list(rep["weight"])  # how many days of the year each representative day stands for
kw_multiplier = np.loadtxt("data/feeder_shape.csv", delimiter=",")[:, 0]

substation = buses["bus"][0]
bus_list = list(buses["bus"])
line_list = list(lines["line"])
D = range(len(rep_days))
T = range(24)

from_bus = dict(zip(lines["line"], lines["from_bus"]))
to_bus = dict(zip(lines["line"], lines["to_bus"]))
r_pu = dict(zip(lines["line"], lines["r_ohm"] / BASE_OHM))
x_pu = dict(zip(lines["line"], lines["x_ohm"] / BASE_OHM))
rating = dict(zip(lines["line"], lines["rating_MVA"]))  # MVA = p.u.
length_km = dict(zip(lines["line"], lines["length_km"]))
linecode = dict(zip(lines["line"], lines["linecode"]))
peak_P = dict(zip(buses["bus"], buses["peak_P_MW"]))
peak_Q = dict(zip(buses["bus"], buses["peak_Q_MVAr"]))

# the line into each bus, and the lines out of it
line_in = {}
lines_out = {}
for bus in bus_list:
    lines_out[bus] = []
for l in line_list:
    line_in[to_bus[l]] = l
    lines_out[from_bus[l]].append(l)


def upgrade_cost(l):
    # up-front cost ($) of upgrading line l, from what kind of element it is
    if l.startswith("l("):
        # a real stretch of wire: pay per foot to reconductor it
        if "_oh_" in linecode[l].lower():
            return OVERHEAD_COST_PER_FT * length_km[l] * FEET_PER_KM
        return UNDERGROUND_COST_PER_FT * length_km[l] * FEET_PER_KM
    if "recloser" in l:
        return RECLOSER_COST
    if "fuse" in l:
        return FUSE_COST
    return SWITCH_COST  # padswitch, disswitch, elbswitch, ...


def load_P(g, j, d, t):
    # active load at bus j on day d, hour t = g x peak x the hour's multiplier (MW)
    return g * peak_P[j] * kw_multiplier[24 * rep_days[d] + t]


def load_Q(g, j, d, t):
    # reactive load = tan(phi_j) x active load, with tan(phi_j) = peak_Q / peak_P at the bus
    if peak_P[j] == 0:
        return 0.0
    return peak_Q[j] / peak_P[j] * load_P(g, j, d, t)


def build_model(g, plan):
    # candidate upgrades U and battery buses B for this g and plan
    U = list(all_upgrades[all_upgrades["g"] == g]["line"])
    B = list(all_sites[all_sites["g"] == g]["bus"])
    if plan == "wires":
        B = []
    if plan == "batteries":
        U = []

    m = gp.Model()

    # planning variables
    E = m.addVars(B, lb=0, ub=E_MAX, name="E")  # battery energy (MWh)
    y = m.addVars(B, vtype=GRB.BINARY, name="y")  # battery site built
    z = m.addVars(U, vtype=GRB.BINARY, name="z")  # line upgraded
    # operating variables, for every day d and hour t
    p_ch = m.addVars(B, D, T, lb=0, name="p_ch")  # charging (MW)
    p_dis = m.addVars(B, D, T, lb=0, name="p_dis")  # discharging (MW)
    s = m.addVars(B, D, T, lb=0, name="s")  # state of charge (MWh)
    P = m.addVars(line_list, D, T, lb=-GRB.INFINITY, name="P")  # active flow (MW), either way
    Q = m.addVars(line_list, D, T, lb=-GRB.INFINITY, name="Q")  # reactive flow (MVAr)
    v = m.addVars(bus_list, D, T, lb=V_MIN**2, ub=V_MAX**2, name="v")  # squared voltage

    # objective
    battery_cost = gp.quicksum(C_E * E[n] + F_SITE * y[n] for n in B)
    wire_cost = gp.quicksum(yearly(upgrade_cost(l), WIRE_YEARS) * z[l] for l in U)
    energy_cost = gp.quicksum(
        weights[d] * price(t) * P[l, d, t] for d in D for t in T for l in lines_out[substation]
    )
    wear_cost = gp.quicksum(weights[d] * C_DEG * p_dis[n, d, t] for n in B for d in D for t in T)
    m.setObjective(battery_cost + wire_cost + energy_cost + wear_cost, GRB.MINIMIZE)

    for d in D:
        for t in T:
            # (1) and (2): power balance at every bus except the substation
            for j in bus_list:
                if j == substation:
                    continue
                battery = 0
                if j in E:  # a candidate battery bus
                    battery = p_ch[j, d, t] - p_dis[j, d, t]
                P_out = gp.quicksum(P[k, d, t] for k in lines_out[j])
                Q_out = gp.quicksum(Q[k, d, t] for k in lines_out[j])
                m.addConstr(P[line_in[j], d, t] - P_out == load_P(g, j, d, t) + battery)
                m.addConstr(Q[line_in[j], d, t] - Q_out == load_Q(g, j, d, t))

            # (3): substation voltage fixed, voltage drop along every line
            v[substation, d, t].lb = V0**2
            v[substation, d, t].ub = V0**2
            for l in line_list:
                i = from_bus[l]
                j = to_bus[l]
                m.addConstr(
                    v[j, d, t] == v[i, d, t] - 2 * (r_pu[l] * P[l, d, t] + x_pu[l] * Q[l, d, t])
                )

            # (4): thermal limit, a 16-sided polygon inside the circle P^2 + Q^2 <= S^2
            for l in line_list:
                limit = rating[l]
                if l in z:  # a candidate upgrade
                    limit = rating[l] + (UPGRADE_FACTOR - 1) * rating[l] * z[l]
                for k in range(K):
                    theta = 2 * math.pi * k / K
                    m.addConstr(
                        math.cos(theta) * P[l, d, t] + math.sin(theta) * Q[l, d, t]
                        <= math.cos(math.pi / K) * limit
                    )

            # (5) and (6): batteries; hour 0 follows hour 23, so each day is a cycle
            for n in B:
                m.addConstr(
                    s[n, d, t]
                    == s[n, d, (t - 1) % 24] + ETA_CHARGE * p_ch[n, d, t] - p_dis[n, d, t] / ETA_DISCHARGE
                )
                m.addConstr(s[n, d, t] <= E[n])
                m.addConstr(p_ch[n, d, t] <= E[n] / DURATION_H)
                m.addConstr(p_dis[n, d, t] <= E[n] / DURATION_H)

    # (7): battery capacity only where a site is built
    for n in B:
        m.addConstr(E[n] <= E_MAX * y[n])

    costs = [battery_cost, wire_cost, energy_cost, wear_cost]
    return m, E, z, costs


if __name__ == "__main__":
    summary = []
    batteries = []
    upgrades = []

    for g in GROWTH_LEVELS:
        for plan in PLANS:
            print(f"\n===== g = {g}, plan = {plan} =====")
            m, E, z, costs = build_model(g, plan)
            m.Params.TimeLimit = TIME_LIMIT_S
            m.Params.MIPGap = MIP_GAP
            m.optimize()

            if m.SolCount == 0:
                print("no feasible plan found, Gurobi status", m.Status)
                continue

            for n in E:
                if E[n].X > 1e-4:
                    batteries.append(
                        {"g": g, "plan": plan, "bus": n, "energy_MWh": E[n].X, "power_MW": E[n].X / DURATION_H}
                    )
            for l in z:
                if z[l].X > 0.5:
                    upgrades.append(
                        {
                            "g": g,
                            "plan": plan,
                            "line": l,
                            "length_km": length_km[l],
                            "linecode": linecode[l],
                            "rating_MVA": rating[l],
                            "upfront_cost_usd": upgrade_cost(l),
                        }
                    )

            row = {
                "g": g,
                "plan": plan,
                "total_cost_per_yr": m.ObjVal,
                "battery_cost_per_yr": costs[0].getValue(),
                "upgrade_cost_per_yr": costs[1].getValue(),
                "energy_cost_per_yr": costs[2].getValue(),
                "wear_cost_per_yr": costs[3].getValue(),
                "battery_sites": sum(1 for n in E if E[n].X > 1e-4),
                "battery_MWh": sum(E[n].X for n in E),
                "lines_upgraded": sum(1 for l in z if z[l].X > 0.5),
                "mip_gap": m.MIPGap,
                "solve_seconds": m.Runtime,
            }
            summary.append(row)
            print(row)

            # save after every run, so finished runs are kept if a later one is stopped
            pd.DataFrame(summary).to_csv("output/step2_summary.csv", index=False)
            pd.DataFrame(batteries, columns=["g", "plan", "bus", "energy_MWh", "power_MW"]).to_csv(
                "output/step2_batteries.csv", index=False
            )
            pd.DataFrame(
                upgrades,
                columns=["g", "plan", "line", "length_km", "linecode", "rating_MVA", "upfront_cost_usd"],
            ).to_csv("output/step2_upgrades.csv", index=False)
