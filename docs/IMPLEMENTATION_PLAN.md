# Implementation Plan

How we build v1 in the hackathon window. Read PRD.md, TRD.md, BACKEND_SCHEMA.md and APP_FLOW.md first. Status labels: **[CONFIRMED]**, **[ASSUMED]**, **[PENDING]**.

---

## 1. Who does what

- **Ritvik:** the frontend only. He reads `<plan_id>.overlay.json` and the official JSON (BACKEND_SCHEMA sections 8 and 9). No backend plan is needed for him.
- **Backend: three parts. Names are not assigned yet; each of Saket, Aadith and Dev picks one.** The parts are balanced by effort, and separated by two frozen contracts, so nobody has to guess another person's internals.

| Part | Name | In one line | Folder |
|---|---|---|---|
| **1** | Plan to Territories | Read the plan; decide which rooms can share a hub and where hubs may sit | `src/othello/reader/`, `geometry/`, `territory/` |
| **2** | Checker, Output and Runner | The independent referee, the file writers and the runner that ties everything together | `src/othello/checker/`, `io/`, `run.py`, `tests/` (checker side) |
| **3** | Territories to Cables | Decide hub spots, visiting orders and cable routes; search for the cheapest valid design | `src/othello/routing/`, `solver/` |

Models in `src/othello/models/` are shared and change only by agreement of all three.

### Contracts that keep the three parts consistent

```
Part 1 --Contract 1--> Part 3 --Contract 2--> Part 2
 FloorPlan + PartitionOption       Design             ValidationResult, Metrics, files
```

- **Contract 1** (BACKEND_SCHEMA section 5): `FloorPlan` and a list of `PartitionOption` (each a list of `Territory`).
- **Contract 2** (BACKEND_SCHEMA section 6): `Design` (hubs, sequences, polylines in metres).
- Everyone codes against the models in `models/`. If a model needs to change, edit BACKEND_SCHEMA.md first, tell the other two, then change the code.

Stand-ins so nobody waits:

| Part | Can start immediately with |
|---|---|
| 1 | A hand-drawn sample plan (small JSON built from the ResPlan fields); no other part needed |
| 2 | Hand-made `Design` objects (valid and deliberately broken) and the slide 5 / slide 7 examples |
| 3 | Hand-made `PartitionOption` lists and a toy `FloorPlan` (rectangle rooms) |

### Rules of work (to avoid inconsistency)

1. One owner per folder. Cross-folder changes go through a short pull request that the owner reviews.
2. Shared metric functions (L, X, O, B) live in `checker/metrics.py` (Part 2). Part 3 imports them for final scoring; it does not write its own.
3. Part 3 never decides validity. Only `check_design` says valid.
4. All constants go through `SolverConfig`. If a number is not in the PPT, it is in ASSUMPTIONS.md.
5. Use the PPT words: hub, node, ring, leg, territory.
6. Every function in BACKEND_SCHEMA section 7 keeps its name and signature.
7. Daily 10-minute sync: what finished, what is blocked, any contract change.

## 2. Part details

### Part 1: Plan to Territories

| # | Task | Done when |
|---|---|---|
| 1.1 | Input adapter `load_plan` (ResPlan fields until the organiser sample arrives) | Loads 20 sample plans without error |
| 1.2 | Scale conversion and unit tests (slide 4 example: 36,000 / 110 gives 18.09 units/m) | Test matches slide numbers |
| 1.3 | Geometry repair with a log | Invalid polygons repaired deterministically |
| 1.4 | Eligible and excluded room marking; fixed node attachment | Every eligible room has exactly one node |
| 1.5 | Free space `F`; doors and open-passage detection; entrance excluded from zone links | Overlay debug picture shows openings correctly on 10 plans |
| 1.6 | Room-link graph | Matches a hand-checked graph on 5 plans |
| 1.7 | Hub zones (room minus 0.5 m buffers around real walls) | Visual check; zones not empty for normal rooms |
| 1.8 | Lower bound `H_LB` | Hand-checked on 10 plans |
| 1.9 | Connected subset generation with caps (5 rooms, 100 m²) | Counts match brute-force on small cases |
| 1.10 | Partition search (exact cover, diverse set, area-gap ordering) and status per hub count | Test: known partitions found; infeasible case marked `proven_infeasible` |
| 1.11 | Plan statistics script (room counts, areas, scale range, door counts) for the team | Table of dataset statistics |

### Part 2: Checker, Output and Runner

| # | Task | Done when |
|---|---|---|
| 2.1 | Metric functions L, X, O, B, J, area gap (TRD section 7) | Slide 5 and slide 7 examples reproduce exactly (J = 27.8 and 31.2) |
| 2.2 | Rule checks H1 to H6 plus axis, free space, door passage, node match | Each rule has a passing and a failing test |
| 2.3 | Violation model and `ValidationResult` | Each code in BACKEND_SCHEMA section 10 raised by a test |
| 2.4 | Mutation test suite (TRD section 11) | All mutations caught; crossing and overlap mutations stay valid |
| 2.5 | Official JSON writer and `to_output_coords` | Output equals the slide 8 schema; round-trips |
| 2.6 | Overlay JSON writer and debug PNG (Matplotlib) | Ritvik can load it; violations show red |
| 2.7 | Runner and batch mode with summary CSV | `python -m othello.run` works on a folder |
| 2.8 | Failure files and logging | Every failure path in APP_FLOW section 4 produces a `failure.json` |
| 2.9 | Many-plan test harness and statistics (validity rate, hubs vs bound, J, runtime) | Report on hundreds of plans |

### Part 3: Territories to Cables

| # | Task | Done when |
|---|---|---|
| 3.1 | Search grid builder (Hanan-style plus fallback grid), edges tested against true free space | Grid paths never leave `F` on 20 plans |
| 3.2 | Direction-aware A\* with turn cost | Unit tests: min length, min bends on small mazes |
| 3.3 | Distance table with caching | Cache hit rate logged; repeat calls instant |
| 3.4 | Hub candidate generator (grid, node-aligned, near doors, deepest point; diverse) | Candidates all inside the hub zone |
| 3.5 | Visiting-order enumeration, ranking, diverse keep | All `k!` orders generated; diverse set kept |
| 3.6 | Bounded diverse route library with dedupe | At most `K_r` routes per leg, different geometry |
| 3.7 | Whole-design route selection (beam search or exact) using Part 2 metrics | Design J equals checker J |
| 3.8 | F0 quick valid constructor | Verified incumbent on test plans in a few seconds |
| 3.9 | Outer loop: hub counts, time budget, proven/unresolved record, fallback ladder | Deterministic; stops at deadline |
| 3.10 | Improvement search (hub nudges, order swaps, re-route one ring) | J decreases on average vs F0 |
| 3.11 | Handling of single-door rooms (in-and-out on the same line) | Test plan with a single-door room passes the checker |

## 3. Milestones and order

Suggested timeline for a 48-hour event (adjust to the real clock). "H" is hours from kickoff.

| Milestone | When | What |
|---|---|---|
| **M0 Contracts** | H0 to H3 | All three read the docs; confirm `models/` files together; create repo layout; agree a sample plan; send the organiser questions (ASSUMPTIONS section 4) |
| **M1 Foundations (parallel)** | H3 to H14 | Part 1: tasks 1.1 to 1.8. Part 2: tasks 2.1 to 2.3, 2.5. Part 3: tasks 3.1 to 3.3, 3.5 using stand-ins |
| **M2 First end to end** | H14 to H20 | Part 1 provides partitions (1.9, 1.10). Part 3 produces F0 designs (3.4, 3.6 to 3.8). Part 2 checks and writes files. First valid output on one plan |
| **M3 Quality and safety** | H20 to H32 | Mutation tests; improvement search; time budget and fallback ladder; many-plan runs; fix crashes; apply organiser answers |
| **M4 Polish and demo** | H32 to H44 | Overlay quality with Ritvik; dataset-wide stress run; statistics; final presentation visuals |
| **M5 Freeze** | H44 to H48 | No new features; re-run the plan suite; final submission files |

Dependencies (nothing else blocks anything):

- Part 3 needs Part 1's `FloorPlan` for real plans (M2) but not before.
- Part 2 needs Part 3's `Design` for real designs (M2) but not before.
- Part 1's partition search (1.10) is on the critical path for M2: start it early.

## 4. Test gates (cannot move forward without)

| Gate | Condition |
|---|---|
| G1 (end of M1) | Slide 5 and 7 hand examples reproduce exactly in the metric functions; scale test passes; sample plans load |
| G2 (end of M2) | One plan runs end to end and the checker passes the official JSON |
| G3 (M3) | Mutation suite: 100% caught; 100% of verified designs on a 30-plan suite are valid |
| G4 (M4) | Hundreds of plans: 0 crashes, 100% valid whenever a design is returned, runtime within the budget |
| G5 (M5) | Final suite re-run on the frozen code |

## 5. Git workflow

- Branches: `part1/<topic>`, `part2/<topic>`, `part3/<topic>`; small pull requests; `main` always runs.
- Commit often; do not commit datasets, outputs, caches or virtual environments (`.gitignore`).
- Contract changes: a pull request touching `docs/BACKEND_SCHEMA.md` and `src/othello/models/` together, reviewed by all three.
- Tag stable points (`m1`, `m2`, `m3`) so we can roll back.

## 6. Repository layout

```
docs/                          PRD.md, TRD.md, BACKEND_SCHEMA.md, APP_FLOW.md,
                               IMPLEMENTATION_PLAN.md, ASSUMPTIONS.md, README.md
src/othello/
  models/                      plan.py config.py design.py result.py          (shared)
  reader/                      load_plan, repair, scale                       (Part 1)
  geometry/                    free space, openings, hub zones, room graph    (Part 1)
  territory/                   subsets, partitions, lower bound               (Part 1)
  routing/                     grid, A*, route library, distance cache        (Part 3)
  solver/                      hub candidates, orders, selection, loop        (Part 3)
  checker/                     rules, metrics, mutation helpers               (Part 2)
  io/                          official JSON, overlay JSON, debug PNG         (Part 2)
  run.py                       runner and batch mode                          (Part 2)
tests/                         unit tests per part, plus data/ for tiny sample plans
```

Data (the ResPlan dataset, organiser JSON) is **not** committed; keep it in a local `data/` folder (ignored).

## 7. Definition of done for v1

1. For every plan in the test suite, either an official JSON that the checker verifies, or a clear failure file.
2. The official JSON matches the slide 8 schema.
3. The overlay shows zones, hubs, nodes, visiting order, rings and violations.
4. The mutation suite passes.
5. Hub count equals the lower bound on plans where that is feasible; J measurably below the F0 baseline.
6. Every assumption is in ASSUMPTIONS.md with its status.
7. A short batch report: validity rate, hubs vs lower bound, J, runtime, failures.

## 8. Risks and what to do first

| Risk | Early action |
|---|---|
| No organiser JSON yet | Build against the ResPlan fields; send the question list today |
| Checker rules differ from ours | Switches in `SolverConfig`; update ASSUMPTIONS.md when answered |
| Open passage detection is wrong | Draw openings on 10 plans and check by eye (task 1.5) |
| Route library too slow | Caps from TRD 6.5; time-bounded beam search; cache distances |
| Integration surprises | First end-to-end at M2; contract tests with stand-ins |
| Time lost on perfection | F0 first; freeze at H44 |

## 9. Not in v1

RewireLess, parallel lanes, joint multi-hub tuning, ML, LangGraph, stored answers for hidden plans. Revisit only after G4.
