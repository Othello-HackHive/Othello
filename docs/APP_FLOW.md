# App Flow

How one plan travels from input file to output files, including every failure and timeout path. Names match BACKEND_SCHEMA.md. Status labels: **[CONFIRMED]**, **[ASSUMED]**, **[PENDING]**.

---

## 1. The big picture

```
 organiser plan JSON
        |
        v
 [1] LOAD + NORMALISE        (Part 1)  -> FloorPlan (metres, repaired, scale applied)
        |
        v
 [2] BUILD MAPS              (Part 1)  -> free space, room-link graph, hub zones
        |
        v
 [3] LOWER BOUND             (Part 1)  -> H_LB = max(ceil(n/5), ceil(A/100))
        |
        v
 [4] GROUP ROOMS             (Part 1)  -> PartitionOption list for hub count H      <== Contract 1
        |
        v
 [5] SOLVE                   (Part 3)  -> hub spots, visiting orders, routes, Design <== Contract 2
        |
        v
 [6] CHECK + SCORE           (Part 2)  -> ValidationResult + Metrics
        |        \
        |         invalid -> try next candidate / next hub count / fallback (section 4)
        v
 [7] WRITE OUTPUTS           (Part 2)  -> official JSON, overlay JSON, debug picture
        |
        v
 files in out/  ->  Ritvik's frontend reads <plan_id>.overlay.json
```

Part 3 owns the loop that repeats steps 4 to 6 until the time budget ends; Part 2 owns the runner that calls everything and writes files.

## 2. Step by step

### Step 1: Load and normalise (Part 1)

| | |
|---|---|
| Input | One plan JSON path, `SolverConfig` |
| Does | Read geometry in drawing units; compute `scale = sqrt(inner.area / area)`; repair invalid polygons (log repairs); convert to metres once; mark eligible and excluded rooms; attach the fixed nodes |
| Output | Partial `FloorPlan` |
| Fails when | A required field is missing, `area` is zero, or geometry cannot be repaired -> `InputError` |

Input schema is **[PENDING P1]**; only `load_plan` changes when it is known.

### Step 2: Build maps (Part 1)

| | |
|---|---|
| Does | Free space `F = inner - (walls U windows)`; doors and open passages as openings (entrance excluded from zone links); room-link graph over eligible rooms; hub zone per room (room minus 0.5 m buffers around real walls); the list of excluded interior rooms |
| Output | Complete `FloorPlan` |
| Fails when | An eligible room has no door or passage to any other (it can still be a single-room zone); free space is empty |

### Step 3: Lower bound (Part 1)

`H_LB = max(ceil(n_nodes / 5), ceil(total_eligible_area / 100))`.
Early exit: if any eligible room is over 100 m², return `no_design_found` with the reason (A14).

### Step 4: Group rooms (Part 1)

| | |
|---|---|
| Does | Generate connected room subsets that fit the caps (at most 5 rooms, at most 100 m², non-empty hub zone); find partitions with exactly `H` subsets covering each room once; order by area gap; keep a diverse set (cap 12) |
| Output | `PartitionOption` list and a status per hub count: `feasible`, `proven_infeasible`, `unresolved` |
| Rule | A timeout is **not** infeasibility: record `unresolved` and move on without claiming a proven minimum |

### Step 5: Solve (Part 3)

| | |
|---|---|
| Does | For each partition: hub candidate points (grid, aligned with nodes, near doors, deepest point), diverse set; visiting orders (all permutations, rank, keep diverse set); cable routes (direction-aware A\* on the grid, bounded diverse route library); whole-design route selection (beam search or exact); small improvement search |
| Output | A `Design` (hubs, sequences, polylines) |
| First | A quick valid incumbent (F0) before any expensive search |

### Step 6: Check and score (Part 2)

| | |
|---|---|
| Does | `check_design` runs every hard rule against the original geometry; `score_design` recomputes L, X, O, B, J and the area gap from the polylines |
| Output | `ValidationResult` with violations, `Metrics` |
| Note | Crossings and overlaps are never violations |

### Step 7: Write outputs (Part 2)

| | |
|---|---|
| Does | Only for a verified design: official JSON; overlay JSON (zones, hubs, nodes, orders, rings, violation and crossing markers, metrics, bounds report); debug PNG |
| Files | `out/<plan_id>.json`, `out/<plan_id>.overlay.json`, `out/<plan_id>.png`, `out/<plan_id>.log.txt`, `out/<plan_id>.settings.json` (the settings used) |
| On failure | `out/<plan_id>.failure.json` with reasons; no official JSON is written |

## 3. Time budget

```
t = 0                                                              t = time_budget_s
|-- load, maps, lower bound --|-------- solve and improve --------|-- final check + write (10%) --|
         ~5-10%                            ~80-85%                              ~10%
```

- Setup and partitions are cheap (at most 1,023 subsets).
- Within "solve and improve", time is shared across hub counts: the current `H` gets most of it; if `H_LB` finds a verified design early, the rest goes to improvement.
- The deadline check lives in every long loop; the reserve is never spent on searching.
- The organiser limit is **[PENDING P10]**; the default is 60 s per plan, configurable.

## 4. Failure and timeout paths

| Situation | What happens | Output |
|---|---|---|
| Input missing or unreadable | `InputError`, plan skipped (batch continues) | `failure.json`, no official JSON |
| Eligible room over 100 m² | Immediate `no_design_found` | `failure.json` with reason |
| No partition for `H` proven impossible | Record `proven_infeasible`; try `H + 1` | Continues |
| Partition search times out at `H` | Record `unresolved`; try `H + 1`; never claim proven minimum | Continues |
| First candidate fails the checker | Try the next candidate (other hub spot, order, routes, partition) | Continues |
| No candidate passes at this `H` within budget | Move to `H + 1` (still not guaranteed feasible) | Continues |
| Time runs out with a verified incumbent | Return the incumbent | Official JSON |
| Time runs out with no verified design | `no_design_found` | `failure.json`; plan scores zero |
| Checker raises an internal error | Treat the design as invalid; log; never write official JSON | `failure.json` |
| Output writer fails | Report the error; files are written in one go, so no partial official JSON is left | `failure.json` (`OUTPUT_WRITE_FAILED`) |
| Output folder cannot be used | Clear error, nothing is run; exit code 1 | Message on screen |

## 5. Fallback ladder (summary of TRD 6.4)

F0 simple valid constructor -> F1 full optimiser improves the incumbent -> F2 try `H + 1` -> F3 return best verified incumbent or a structured failure.

## 6. Command-line runner (Part 2)

```bash
# one plan
python -m othello.run path/to/plan.pkl --out out/ --time-budget 60

# a folder of plans (batch); writes a summary table
python -m othello.run path/to/plans/ --out out/ --time-budget 30 --summary out/summary.csv

# optional switches (all map to SolverConfig)
--transit-policy {forbid,avoid_if_possible,allow}
--weights 1 5 1 0.5
--config FILE        # a .json or .toml settings file
--set KEY=VALUE      # any setting, e.g. weights.crossing=6 (repeatable)
--no-picture         # skip the debug PNG
--quiet              # no progress lines on screen
```

- **Input:** for now a pickled ResPlan record (`.pkl` or `.pickle`); a folder means every such file in it, in name order. The organiser JSON will plug in at one function (`load_plan_file`).
- **Settings** come from, lowest to highest priority: built-in defaults, the `--config` file (or `OTHELLO_CONFIG`), `OTHELLO_*` environment variables (`OTHELLO_TIME_BUDGET_S=30`, nested with two underscores: `OTHELLO_WEIGHTS__CROSSING=6`), the friendly flags, then `--set`. A typo or impossible value is an error (exit code 2), never silently ignored.
- **Per plan:** hub counts from the lower bound up to the lower bound + 2 are offered to Part 3; the search deadline is `time_budget_s x (1 - final_check_reserve_fraction)`; the winning design is re-checked and only then written. A search that ran out of time but holds a verified design still counts as a success.
- **Files** go to `--out`, named after the plan's own id (see section 7).

Batch summary columns: `plan_id, status, hubs, lower_bound, proven_min, L, X, O, B, J, runtime_s, notes`. `status` is `ok`, `no_design_found` or `failed`.

Exit code: **0** if the plans were processed (even `no_design_found` or `failed`), **2** for a bad command (settings, missing source), **1** if the result folder cannot be used. One bad plan never stops a batch.

From Python: `othello.run.run_plan(source, out_dir, config)` and `run_batch(...)`; `source` may also be an already built `FloorPlan`.

## 7. What the frontend sees (Ritvik)

Ritvik's frontend reads files only:

1. `<plan_id>.overlay.json`: everything needed to draw rooms, walls, doors, hubs, nodes, visiting order, rings, markers and metrics.
2. `<plan_id>.json`: the official output (to display the submitted answer).
3. `<plan_id>.failure.json` when no design exists, to show a clear message (field list: BACKEND_SCHEMA section 11).

The field-by-field description of the overlay is [OVERLAY_FORMAT.md](OVERLAY_FORMAT.md); real example folders are in `tests/data/samples/`.

The frontend never calls solver code. If it wants to run a plan from the UI, it calls the runner (`run_plan`) through whatever thin wrapper the team agrees on later. A wrapper is not part of v1.

## 8. State kept during a run

- **Incumbent**: the best checker-verified `Design` so far.
- **Candidate pool**: other verified designs (optional, for debugging).
- **Hub-count status table**: feasible / proven_infeasible / unresolved per hub count.
- **Caches**: leg routes keyed by (endpoint pair, geometry version, transit policy).
- **Log**: repairs, decisions, timeouts, rejected candidates with violation codes.

Nothing is written to the official JSON until the checker verifies the chosen design.

## 9. Lifecycle of a typical plan (example)

1. Plan with 7 eligible rooms, 128 m². `H_LB = max(ceil(7/5), ceil(128/100)) = 2`.
2. Partition search at `H = 2` returns 5 options; one has areas 71 and 57.
3. Quick design passes the checker: valid incumbent in about 2 s.
4. Optimiser tries more hub spots, orders, routes; J drops from 74 to 61.
5. Time ends; the final check passes; write official JSON, overlay JSON and PNG.
6. Report: `H = 2`, lower bound 2, proven minimum (equals the bound), best J 61.
