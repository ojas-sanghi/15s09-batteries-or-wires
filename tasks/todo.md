# Simplify the pipeline (2026-11-23)

## Pipeline duplication (#2)
- [x] `src/common.py`: GROWTH_LEVELS and load_feeder()
- [x] Export runs first (step1_1_export) and writes the one line list (+ rating_amps) and bus x/y
- [x] Screen (step1_2_screen) reads network_lines.csv instead of building its own line list
- [x] Screen writes rep_days.csv; optimizer reads it (no import of the screen)
- [x] Plot uses network_lines.csv + bus x/y; no .dss parsing

## Repo hygiene (#3)
- [x] One long-format CSV per table (g / plan columns) instead of one file per g
- [x] README with the real run order; fix stale docstrings
- [x] Delete stale data/step1_summary.csv and old per-g outputs and logs
- [x] pyproject description; step0_2 reads data/feeder/Loads.dss

## Scope changes asked for
- [x] Synthetic two-level price (user set $50 peak 4-9 pm, $35 off-peak); drop step0_3, prices.csv, certifi
- [x] GROWTH_LEVELS = [1.0, 1.3]
- [x] Plain code style everywhere; optimization math kept exactly as in the proposal (per-unit r, x;
      Q variables; 16-sided polygon; tan(phi) reactive load). `all_sites` plan dropped (not in proposal).

## Verify
- [x] Step 1 outputs identical before/after the style rewrite (8 files)
- [x] Step 2 at g = 1.0 and 1.3, all three plans (energy cost matches a hand calculation)
- [x] Q validation (full vs fixed-Q model): for information only, not applied

## Review
- Step 1 outputs unchanged by the refactor and the style rewrite.
- Step 2 at g = 1.3: wires $4.58M/yr (15 upgrades), batteries only $5.20M/yr (time limit, 0.29% gap),
  both = wires. g = 1.0: nothing built (the screen's one overload is a phase imbalance the balanced model can't see).
- Fixing Q is exact (same objective and plan in 3 cases) but not faster to solve (g = 1.3 both: 1269 s vs 695 s),
  because Gurobi's presolve already does it. Not applied.
