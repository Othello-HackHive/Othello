# Part 3 (Territories to Cables): independent read-only audit

Date 2026-10-16. Code audited: `main` @ `590447f` (Part 1 + Part 3 + Part 2 merged; Python 3.13; Part 2's `check_design` present). **No file was modified, committed or pushed by the audit** (verified: `git diff origin/main -- src/othello/routing src/othello/solver` is empty). All experiments were throw-away scripts outside the repo; the few places where a speed-up was patched in memory are marked.

What was done: read every production module in `src/othello/routing/` (8 files) and `src/othello/solver/` (7 files) in full (about 3,500 lines); ran all 316 Part 3 tests (pass, clean venv, `coverage` 90%); built 8 adversarial houses (tiny, slide-5 7 rooms, L-shaped living room, 0.3 m thick walls, 4-room chain with single-door rooms, excluded-room shortcut, open passage, 3x3 grid of 9 rooms) plus 3 micro-houses (0.45 m doors, door beside a corner, 1.8 x 2 m closet); solved them through Part 1's `generate_partitions` -> Part 3's `optimize_partitions` -> Part 2's `check_design`; re-checked every design with a separate Shapely-only validator (axis alignment, inner containment, walls, windows, 2 cm cable margin, hub clearance, room changes only through the matching door, ring and node coverage); compared A* with a brute-force Dijkstra on 3,720 queries; compared cached vs uncached routing; ran deadline sweeps, misbehaving checkers, ranking and determinism tests; profiled.

## Verdict: CONDITIONAL GO

The design is sound and the geometry is trustworthy: no invalid physical route was found in any design the solver produced. The blocker is **speed to the first valid design** (a missing design scores zero), which has a small, proven fix. Two further conditions concern robustness and real-data verification.

Conditions to turn this into GO:
1. Fix the per-leg graph rebuild (P0, B1). Prototype: 10.8x and 22.3x faster, identical designs.
2. Run Part 1 -> Part 3 on a sample of REAL ResPlan plans (50+) and report the share ending in `unsupported_geometry` (UNVERIFIED here: no dataset on this machine).
3. Make checker/config failures non-fatal for the incumbent (B3, B4).

## Result per audit area

| # | Area | Verdict | Evidence in one line |
|---|---|---|---|
| 1 | Requirements traceability | PASS with gaps | tasks 3.1-3.11 all exist with tests; gaps: no "avoid placed cable" route variant, F0 not quick, no final-check time reserve |
| 2 | Geometry correctness | PASS | 11 houses, all returned designs (F0 and improved) pass my checker AND the independent validator; hub clearance in F0 designs 0.70-1.95 m |
| 3 | Routing correctness | PASS | A* equals Dijkstra on 3,720 queries (7 houses, 4 weight sets, 3 start headings); every cost recomputes from its polyline; every route is valid against real walls/windows/doors; route library: 0 invalid variants, 0 duplicates, door ids match true crossings |
| 4 | Optimization | PASS (quality headroom) | all 5! = 120 orders enumerated, optimum equals brute force, independent of input order; ranking is fewest hubs, then J, then area gap; identical designs on repeated runs; at convergence J is 29 to 46% above my hand-made reference on the 7-room house (83.4 to 94.4 vs 64.8) |
| 5 | Anytime safety | PASS with caveats | stops within 10 ms of the deadline; returned designs always verified; a checker that turns invalid keeps the incumbent; caveats B3 (exception loses incumbent) and B1 (no incumbent within budget) |
| 6 | Caching | PASS | cached == uncached in any query order; stale answers never returned after a door/wall/node/policy/weights change (copy or in-place); policies isolated; returned routes cannot poison the cache; graphs read-only; ~1 MB per graph |
| 7 | Interfaces | PASS with notes | works on the real `FloorPlan`, `Territory`, `PartitionOption`; the design dict validates as `Design`; see integration notes |
| 8 | Performance | **FAIL** | first valid design: 33 s (7 rooms), 46 s (9 rooms); with a shorter budget NO design |
| 9 | Test quality | PARTIAL | 316 tests, 90% line coverage, but mostly stand-in plans; no end-to-end test with a real plan + the real checker; no speed test |

## Bugs and risks (ranked)

### P0
**B1. A graph is rebuilt for every ordered leg; the graph cache never hits.**
`src/othello/routing/table.py:71-94` (`_make_path_key`, line 77 `leg = (from_id, to_id)`), `src/othello/routing/cache.py:94-108` (`GraphKey.leg`), `src/othello/routing/grid.py:400-480` (`build_routing_graph` builds the whole grid per call), `table.py:132-155`.
Effect: 24 graph builds of ~0.5 s for a 2-hub 7-room house (0 cache hits), 50 builds for the 9-room house; graph building is 62 of 67 profiled seconds. Time to the first valid design (F0 + check, as built): tiny 0.1 s, open 0.1, shortcut 0.3, chain 0.9, thick 5.8, L 5.5, **slide-5 32.9 s, 3x3 grid 45.5 s**. With a 20 s budget the 7-room house returns NO design; the 9-room house returns none within 40 s. ResPlan's median plan needs 2 hubs, so a slower grader can easily end with no design (score 0).
Repro (runs on `main`):
```python
import sys, time; sys.path[:0] = ["src", "tests"]
from fixtures.plans import slide5_plan
from othello.models import SolverConfig
from othello.territory import generate_partitions
from othello.routing import RoutingCache
from othello.solver import construct_f0_design
plan, cfg = slide5_plan(), SolverConfig()
opt = generate_partitions(plan, 2, cfg)[0][0]
cache = RoutingCache(); t = time.monotonic()
construct_f0_design(plan, opt, cfg, cache)
print(time.monotonic() - t, cache.stats())   # ~30 s, graph_hits=0, graph_misses=24
```
Proven fix (prototype, in memory): make the graph key independent of the leg, i.e. `leg=None` in `GraphKey` so one graph serves all legs of the same terminal set. Result: slide-5 F0 30.1 s -> 2.8 s (10.8x, 2 builds, 17 hits); 3x3 grid 48.0 s -> 2.2 s (22.3x); **identical designs and J** (103.540 and 68.500), both checkers valid. Safety net for the rare case where a shared graph does not connect a pair: if A* reports unreachable, fall back to the per-leg build (the fallback grid is chosen per pair).

### P1
**B2. After B1, the remaining cost is one graph per hub CANDIDATE.** `solver/outer.py:78`, `solver/improvement.py:264`, `solver/f0.py:97` build a new `DistanceTable` (new terminal set, new graph, ~0.5 s) for each of up to 40 hub candidates. Profile after B1 (house L, 40 s): `_edge_openings` 11 s, `_owner`/`_spatially_allowed`/Shapely scalar predicates dominate; the checker is only 3% (114 calls x 9.9 ms). Suggestions: build the base graph once and add a candidate's lines incrementally; use prepared geometry or `shapely.contains_xy` vectorised in `_owner`; cache `rooms` boundaries.
**B3. An exception from the checker (or a malformed checker result) aborts the whole call and the earlier verified design is lost.** `solver/route_selection.py:190` (`checker(plan, candidate, config)`), `:140-150` (`_score_of_verified` raises `ValueError`), `solver/outer.py:267,294`, `solver/improvement.py:176`; none is wrapped. Repro: a checker that works 5 times then raises -> `optimize_partitions` raises `RuntimeError`, no design returned. The real checker raised 0 exceptions on all designs tried, and Part 2 can mitigate by passing a safe wrapper that never raises (planned for the runner), but the solver should treat a checker exception as "design not verified" + a diagnostic.
**U1 (UNVERIFIED, integration risk). Real ResPlan geometry.** `routing/grid.py:107-134` is strict about opening gaps (must be covered by free space, must not touch walls or windows, must cross room boundaries transversally, exactly two interior rooms) and returns `unsupported_geometry` otherwise; Part 1's gap lines come from the first two boundary points of the door polygon. Clean toy gaps (11 houses, 0.45 m doors, corner doors) all pass; real data may not. Needs a run on real plans.

### P2
**B4. `ValueError` when `limits.hub_candidates` is below the number of required representatives.** `solver/hub_candidates.py:396-397`. Repro: house L with `hub_candidates=6` raises "below the 7 distinct required representatives"; default 40 works (also 12 and 8). The error escapes `construct_f0_design`/`optimize_partitions` instead of becoming a status. Also `solver/node_orders.py:71` hard-codes 1..5 nodes instead of using `max_nodes_per_hub`.
**B5. Hub exactly on a node (zero-length legs).** `solver/hub_candidates.py` (~line 340, the `"node_exact"` sample) gives proxy 0.0, so for a single-node territory the first candidate is the node point itself; `solver/f0.py` (~lines 128-135) encodes the two coincident legs as `[p, p]`. Result: hub cost 0 (house slide-5 with 3 hubs: hub at (2.0, 7.0) = node, J 80.2, valid per my checker). The PPT does not forbid it, but whether the organisers' checker accepts a hub on a node and zero-length polylines is unknown. Suggest a config switch (default: require a minimum hub-node distance) or an organiser question.
**B6. F0 is not "quick".** `solver/f0.py` enumerates all candidates and orders and routes 12-20+ legs before the first design (B1). The TRD runtime contract says "valid incumbent first": add a cheap path (first candidate, nearest-neighbour ring) so an incumbent exists in well under a second, then improve.

### P3
**B7. Route library lacks the "avoid already-chosen cable" variant** (TRD 5.5 item 4); the docstring at `routing/route_library.py:92-102` says so. The "fewest bends" variant uses weights (length 0, bend 1) at `:176-179`, so among equal-bend routes it may return an arbitrarily long one (still valid).
**B8. `OuterSearchResult.status` is `"timeout"` whenever the budget is used up, even with a verified design** (`solver/outer.py:339`). The runner must treat `timeout` with `design is not None` as success and `provisional_design` as never submittable. Also: the budget starts at the call (`outer.py:214`); there is no reserve for final checking and writing (TRD 6.3), so the runner should pass `deadline = start + 0.9 x budget`.
**B9. Improvement is one neighbourhood sweep** (`solver/improvement.py:127-132`), and exact mode calls the checker for every route combination (up to `exact_combo_limit` = 20,000, ~10-15 ms each, `solver/route_selection.py:238-243`). At convergence (graph sharing on): slide-5 J 83.4 (hand-made reference 64.8), 3x3 grid J 57.0, chain 29.1, L 24.2. Early budget goes to the first partition (1 partition, 3 configurations in 150 s on slide-5).
**B10. `unsupported_geometry` is never cached** (`routing/table.py:148-149`), so each query rebuilds the graph to rediscover it. `plan_fingerprint` is recomputed on every leg query (0.8 ms; fine).
**B11. The fallback grid is expensive:** slide-5 augmented 0.1 m grid = 19,862 nodes / 78,128 edges, 12 s and ~12 MB per graph (cache keeps up to 6). With B1 unfixed, a plan that needs the fallback for many legs would take minutes.
**B12 (fixed). Missing `from typing import Any`** and Part 3 tests not collected: fixed in PR #6.

## Confirmed vs assumed vs pending

- **Confirmed (PPT):** 5 nodes, 100 m2, hub >= 0.5 m, axis-aligned cables inside free space, rooms only through doors/openings; the solver and both checkers agree on all of these on every design.
- **Assumed (ours):** `avoid_if_possible` = use an excluded room only if no other route exists (verified: forbid/avoid never enter the excluded bathroom, allow does, same shared cache, any order); zone links only between assigned eligible rooms; 2 cm cable margin.
- **Pending (organisers):** cable through excluded rooms; zone link through an excluded room; encoding of open passages; coincident hub/node (B5); zero-length polylines in the official file; runtime limit.

## Integration readiness

- **Part 1 -> Part 3: CONDITIONAL.** Contracts match (real `FloorPlan`, `Territory`, `PartitionOption`). Aadith's zone fix (clearance + tolerance) is what unblocked the 7-room house. Open: real-data gap geometry (U1). The caller must pass `lower_bound_hubs` (there is no `plan.hub_lower_bound()` method); without it `proven_minimum` can never be true.
- **Part 3 -> Part 2: READY.** The solver's design dict validates as `Design`; Part 2's `check_design` accepts dicts; every returned design re-validated; J reported by the solver equals Part 2's J exactly. Runner rules: wrap the checker so it never raises; treat `status="timeout"` + `design` as success; never submit `provisional_design`; pass a deadline with a 10% reserve.

## Missing test coverage (coverage 90%: routing 95%, solver 76-90%)

Lowest: `solver/f0.py` 76%, `solver/outer.py` 81% (lines 125-131 candidate-diversity fill, 322-325 improvement-unsupported branch), `solver/improvement.py` 87%, `routing/route_library.py` 88%. Missing scenarios: end-to-end real plan + real checker; a speed regression test (graph builds per hub); checker raising mid-search; `hub_candidates` below the required representatives; hub on node; fallback-grid cost; real ResPlan geometry.

## Performance numbers (this machine)

| House | rooms | hubs | first valid design (as built) | J after improvement (budget) |
|---|---|---|---|---|
| tiny | 2 | 1 | 0.1 s | 10.02 (4 s, completes) |
| open passage | 2 | 1 | 0.1 s | 19.0 (12 s) |
| shortcut | 3 | 1 | 0.3 s | 52.0 (12 s) |
| chain (single-door rooms) | 4 | 1 | 0.9 s | 29.1 (11 s, completes) |
| thick walls | 4 | 1 | 5.8 s | 26.8 (15 s) |
| L-shaped | 3 | 1 | 5.5 s | 24.24 (40 s, completes) |
| slide-5 | 6 | 2 | **32.9 s** | 94.4 (45 s) / 83.4 (150 s, graph sharing) |
| 3x3 grid | 9 | 2 | **45.5 s** | 57.0 (150 s, graph sharing) |

Positive: the chain house reaches O = 0 with single-door rooms because the Hanan lines inside a door gap give parallel lanes automatically.
