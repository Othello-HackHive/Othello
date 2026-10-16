# TRD: Technical Requirements (Cable Ring Solver)

Read PRD.md first. Status labels: **[CONFIRMED]** (PPT), **[ASSUMED]** (our default, in SolverConfig), **[PENDING]** (needs organiser input). The register is ASSUMPTIONS.md; IDs such as A1, P3, C5 refer to it.

Stack: Python 3.11+, Shapely (geometry), NetworkX (graphs), NumPy, Pydantic (models and JSON), OR-Tools CP-SAT (optional, for set partitioning), Matplotlib (debug drawings), pytest.

---

## 1. Principles

1. **Validity first, optimisation second.** An invalid plan scores zero.
2. **The search grid is an approximation; the original geometry is the truth.** Routes are found on a grid but always validated against the real normalised geometry.
3. **The checker is independent of the solver.** Different code paths for the rule tests and the metric maths. They share only the normalised `FloorPlan`.
4. **Complete-design scoring.** J is always recomputed from the full set of polylines. Pairwise estimates only guide the search.
5. **Honest claims.** Distinguish *valid* (passes all rules), *best found* (lowest J found in budget) and *proven minimum* (a bound or exhaustive argument shows nothing better exists).
6. **No unchecked output.** Only a checker-verified design is ever written as the official JSON.
7. **Everything uncertain is configuration** (ASSUMPTIONS.md).

## 2. Coordinates, units and scale

- Input is in **drawing units**, not metres. [CONFIRMED slide 4]
- Per plan, once, before any limit or clearance test:

```
scale = sqrt(inner.area / area)        # drawing units per metre   [CONFIRMED slide 4]
metres   = units / scale
m^2      = units^2 / scale^2
0.5 m    = 0.5 * scale units
```

  `area` is the plan's floor area in m². Never use `net_area`. [CONFIRMED]
- Typical scale is about 18 units/m (range 12 to 26); wall thickness 10 to 40 cm after conversion. [CONFIRMED notes 4]
- **Internal working unit is metres.** The reader converts once and keeps both the original drawing-unit geometry and the metre geometry. Metres are used by the solver, checker and output.
- Output is metres, origin lower-left, y up. [CONFIRMED slide 8] Whether the input is y-down and what "origin" means is **[PENDING P6]**. The transform lives in one function (`to_output_coords`) and nothing else flips axes.
- Room area for capacity is the area of the eligible room polygon in m², summed per hub. [CONFIRMED slide 2, 4]

## 3. Geometry model

### 3.1 Layers

| Layer | What | Used by |
|---|---|---|
| Normalised geometry | Rooms, walls, windows, doors, inner, nodes in metres (original shapes, repaired once) | Checker, reader, everything that tests truth |
| Free space `F` | `inner minus (walls union windows)`; doors and open passages are the gaps. [CONFIRMED notes 3] | Routing truth |
| Search grid | A simplified rectilinear grid built inside `F` | Path search only |

### 3.2 Free space and cable rules

- A cable polyline is valid if every segment lies in `F` (within the 1 mm tolerance), is exactly horizontal or vertical, and any transition between rooms goes through a door or open-passage gap. [CONFIRMED]
- The search keeps `cable_wall_margin_m` (0.02 m, A4) off wall and window edges so a path never merely grazes a wall. The checker uses only the true 1 mm tolerance.
- Space outside the inner boundary (balcony, garden, parking, land) is forbidden in every mode. [CONFIRMED]

### 3.3 Excluded rooms and cable transit

- Excluded rooms never hold a hub or node. [CONFIRMED]
- Whether cable may cross an excluded interior room is **[PENDING P3]**. `cable_transit_policy`:

| Mode | Behaviour |
|---|---|
| `forbid` (**default until P3 is answered**) | Never traverse excluded interior rooms |
| `avoid_if_possible` (A1) | Use an excluded interior room only if no other route exists for that leg |
| `allow` | Treat all interior free space normally; compare routes by J |

  Switching to `allow` after the organisers answer is a one-line config change.

### 3.4 Hub allowed zone

- For each eligible room `r`: `Z_r = room_r minus buffer(walls, 0.5 m)`, kept inside `room_r`. The buffer is around the **real wall shapes**, not the whole room outline, because an open passage is not a wall and shrinking the room polygon would reject valid spots. [CONFIRMED rule C6; A5 for the method]
- Hub zone of a territory is the union of its rooms' `Z_r`. If a room's `Z_r` is empty, no hub can sit in that room.
- Node points are inputs and never moved. [CONFIRMED]

## 4. Room-link graph and zones

- Vertices: eligible rooms (excluded interior rooms are kept in a separate set for transit decisions).
- An **edge** between two rooms exists if a door polygon touches both, or they share a boundary stretch that is not covered by wall (an open passage). Windows, wall-only adjacency and the **entrance** (front door) do not count. [CONFIRMED slide 3]
- How open passages appear in the input is **[PENDING P5]**; the reader derives them from geometry if not given.
- **Zone rule (H3):** the subgraph induced by a hub's assigned rooms must be connected. In v1 only direct links among assigned eligible rooms count (A2, **[PENDING P4]**).
- Door and passage records also keep an id and a gap polyline so routing can reference "which door was used".

## 5. Algorithms, one per job

Sizes are tiny: at most 10 eligible rooms (so at most 10 nodes), at most 5 nodes per hub, so hub counts are usually 1 or 2. Exact methods are practical for the discrete decisions; the work is in geometry and routing.

### 5.1 Fewest hubs (lower bound)

```
n = number of eligible rooms (= nodes)        A = total eligible area in m2
H_LB = max( ceil(n / 5), ceil(A / 100) )
```

- This is a lower bound, not the guaranteed answer. We start the search at `H_LB`. [CONFIRMED formula: slide 2]
- See section 6.2 for when we move to `H_LB + 1`.

### 5.2 Ways to group rooms (territories)

1. **Generate connected room subsets** by breadth-first expansion over the room-link graph (each subset in canonical form, so no duplicates). With at most 10 rooms there are at most 1,023 subsets.
2. **Filter** each subset by: at most 5 rooms (= nodes), at most 100 m² area, and at least one room with a non-empty hub zone.
3. **Pick partitions**: find sets of subsets that cover every room exactly once using exactly `H` subsets (exact cover). Small backtracking search, or CP-SAT set partitioning if preferred.
4. **Return several partitions, not one.** Order them by the tie-break (smaller area gap) and keep a diverse set (cap in section 6) so later stages can try alternatives.
5. If no partition exists for `H`, that is a **proven infeasible at the grouping level** and `H + 1` is tried with that proof recorded. If the partition search itself times out, the result is **unresolved** (section 6.2).

### 5.3 Hub spots

For each territory build candidate hub points inside its hub zone:

- A coarse grid of points (default step 0.25 m).
- Points aligned with the territory's node x or y coordinates (Hanan-style, because the optimal rectilinear tour often turns at such lines).
- Points just inside each door gap of the territory's rooms.
- The deepest interior point of each room's zone (a "pole of inaccessibility" point).
- Deduplicate within 1 mm.

**Keep a diverse set, not only the shortest.** Rank by estimated ring cost, but always keep at least one candidate per room and one per door cluster, up to the cap (section 6). A slightly longer ring may avoid crossings and overlaps. Later, refine the best candidates by nudging along the grid lines.

### 5.4 Order to visit nodes

- For a hub with `k` nodes there are `k!` visiting orders, at most 120. Enumerate **all** of them; each is a directed sequence (the two directions are different designs because the geometry, crossings and bends differ).
- Estimate each by the sum of leg distances from the obstacle-aware distance table (section 5.5), rank, and keep the best `K_o` plus a diverse set (different first door, different last door). Do not prune to only the shortest.
- Exact methods stay possible: Held-Karp is unnecessary at this size because full enumeration is cheap.

### 5.5 Cable paths

**Search.** A\* on a rectilinear grid inside free space. State is `(cell, facing direction)`, so every turn costs its bend weight while searching. Step cost is metres; turn cost is `0.5` (the B weight). Moves are only horizontal or vertical.

**Grid construction (approximation only).**

- Lines at the x and y coordinates of: node points, hub candidates, door and passage gap centres and edges, and wall corners offset by `cable_wall_margin_m`.
- Plus a uniform fallback grid (default 0.1 m) if the Hanan-style grid leaves a region unreachable.
- Every grid edge is tested against the true free-space geometry before use.

**Route library (bounded, diverse).** For each leg (start point, end point) generate at most `K_r` candidate polylines:

1. Shortest path (turn cost as normal).
2. Fewest-bends path (high turn penalty).
3. Alternative doorway sequence: ban the doors used by the shortest path and search again.
4. Avoiding already-chosen cable: penalise cells used by routes already selected for other legs or rings.
5. For a single-door room, the in-and-out run on the same line (C12; the offset-lane variant is not in v1).

Rules: deduplicate equivalent polylines by geometry signature; cap the library; a route that is individually longer or has more bends is **not** discarded as dominated, because it may avoid crossings or overlaps. Any pruning must be interaction-aware.

**Whole-design route selection.** For each (territory partition, hub choice, visiting order) choose one route per leg to minimise the true J of the **complete design**, including other rings:

- Beam search over the legs, tracking the running design and incremental X, O, B (beam width `W`).
- When the number of combinations is below `exact_combo_limit`, evaluate them all.
- Pairwise interaction costs `5*X_ij + O_ij` guide the search. They are **not additive** (three cables meeting at one point is one crossing; bend cost at a node depends on the legs joining there), so the final J is always computed from all polylines together with the shared metric functions (section 7).

An exact choice over a library is **not** a proof of geometric optimality, since the library is only a sample of possible paths.

## 6. Search control and limits

### 6.1 Outer loop

```
incumbent = None                       # best checker-verified design
prepare plan, graphs, hub zones
H = H_LB
while time remains:
    partitions = grouping(H)                       # section 5.2
    for partition in partitions (ordered):
        build quick design (shortest routes, first valid hub/order)   # F0 valid incumbent
        if checker passes: keep if better
    improve incumbent by hub spots, orders, route library, selection (section 5)
    if a verified design with H hubs exists: stop raising H, keep improving it until budget
    else if infeasibility at H is proven OR the budget share for H expired:
        record H as "proven infeasible" or "unresolved"; H = H + 1
return incumbent or a structured failure
```

### 6.2 Proven, best found, unresolved

| Label | Meaning |
|---|---|
| **proven minimum hubs** | `H = H_LB`, or every lower count was proven infeasible at the grouping level |
| **best found** | A verified design with `H` hubs; lower counts were not exhaustively excluded |
| **unresolved** | A lower count timed out without a valid design and was not proven infeasible |

- **A timeout is not infeasibility.** Raising `H` after a timeout is allowed, but the lower count stays recorded as unresolved and we never claim a proven minimum.
- Adding hubs is **not** a guaranteed route to feasibility (a single-door room or a missing 0.5 m spot can still block it).

### 6.3 Runtime contract

1. The solver gets `time_budget_s` (default 60 s, A12; the organiser limit is **[PENDING P10]**).
2. It reserves about 10% for final checking and serialisation.
3. It builds a valid incumbent first, then improves it.
4. It returns the best **verified** design before the deadline.
5. If none exists it returns a structured failure internally; it never fabricates JSON.
6. Keep separate: "best valid design found" and "best design fully verified". Only the latter can be submitted.

### 6.4 Fallback ladder

| Step | Action |
|---|---|
| F0 | Fast simple constructor: first valid partition, one hub spot, shortest routes. Establish an incumbent |
| F1 | Full optimiser improves the incumbent |
| F2 | Relax the hub-count target (try `H + 1`), rebuilding valid territories and rings |
| F3 | Return the best verified incumbent; if none, a structured failure |

### 6.5 Limits (all configurable, A13)

| Key | Default | Meaning |
|---|---|---|
| `limits.partitions_per_H` | 12 | Partitions tried per hub count |
| `limits.hub_candidates` | 40 | Hub spots kept per territory |
| `limits.orders_kept` | 12 | Visiting orders kept per hub |
| `limits.routes_per_leg` | 6 | Route library size per leg |
| `limits.beam_width` | 20 | Beam width in route selection |
| `limits.exact_combo_limit` | 20000 | Evaluate all combinations below this |
| `grid.step_m` | 0.10 | Fallback grid step |
| `hub_grid_step_m` | 0.25 | Hub candidate grid step |
| `min_hub_node_distance_m` | 0.002 | Interim separation from assigned fixed nodes; 0 permits coincidence |

## 7. Metric maths (official J)

All metrics are computed by shared pure functions used by both the checker and the solver's final scoring, tested against hand-computed examples. Tolerance 1 mm everywhere. [CONFIRMED slide 6, 7]

```
J = wL*L + wX*X + wO*O + wB*B        weights default 1, 5, 1, 0.5     (configurable)
```

- **L (length):** sum of the lengths of every polyline of every leg of every ring, return legs included, in metres. A retraced stretch counts each time it is run. [CONFIRMED]
- **X (crossings):** the number of **distinct points** where two cables meet away from a node or hub, within one ring or between rings. Points within 1 mm are one point. Three cables meeting at one point count once. Consecutive legs meeting at a node or hub are not a crossing. [CONFIRMED]
  - A vertex of one cable touching the interior of another counts as a meeting point (A7, **[PENDING P7]**).
  - Collinear overlap is counted in O, and its endpoints add no extra crossing (A8).
- **O (overlap):** group all axis-aligned segments by line (a line is the same if within 1 mm). On each line, merge into maximal stretches; a stretch covered by `k` legs counts `(k-1) * length`. Parallel runs more than 1 mm apart count 0. [CONFIRMED]
- **B (bends):** per ring, the number of direction changes along the closed ring, at vertices, at nodes and at the hub. A reversal counts once. [CONFIRMED slide 6; **[PENDING P8]** wording]
- **H (hubs):** number of hubs; always compared first.
- **Tie-break:** smaller assigned-area gap between hubs, defined as max hub area minus min hub area (A3, **[PENDING P9]**).

## 8. Checker (independent validator and scorer)

The checker takes `FloorPlan` and a `Design` and returns a `ValidationResult` (valid flag, list of typed violations, metrics). It never uses the solver's routing grid.

Rule tests:

| Check | Method |
|---|---|
| Coverage (H1) | Every eligible room and node appears in exactly one hub; none missing or duplicated |
| Capacity (H2) | Sum eligible-room area in m² per hub (at most 100) and node count (at most 5) |
| Zone (H3) | Room-link graph induced on the hub's rooms is connected (breadth-first search) |
| Hub point (H6) | Point inside an eligible assigned room; distance to wall shapes at least 0.5 m (minus tolerance) |
| Ring (H4) | Sequence starts and ends at the same hub, contains every assigned node exactly once, no other hub, legs chain end to start |
| Axis-aligned (H5) | Every segment horizontal or vertical within 1 mm |
| Free space (H5) | Every segment lies inside `F` (true geometry, Shapely, tolerance 1 mm); stays inside `inner` |
| Doors (H5) | Each move between rooms passes through a door or open-passage gap |
| Nodes | Route endpoints equal the given node points (within 1 mm); nodes are not moved |
| Output | JSON has the required fields, IDs match, units are metres |

Violations carry a type, hub id, ring id and segment index, so the overlay can draw them in red and developers can debug quickly. Three outcomes only: **valid**, **invalid with violations**, **no design found within budget**. [Design choice]

## 9. Numerical robustness

These details decide whether a good design passes the real checker.

1. **Convert once.** Scale once per plan; keep the original unit geometry; never compound conversions.
2. **Snap routes to a fixed precision.** Round output coordinates to a fixed number of decimals (default 1e-4 m) consistently so two points meant to be the same are within 1 mm, and two lines meant to be different are not accidentally within 1 mm.
3. **Do not exploit tolerance.** Where cables must be on different lines, keep clearly more than 1 mm apart (v1 does not use offsets at all).
4. **Boundary contact.** Treat touching a wall or the inner boundary as a violation risk; the search margin (0.02 m) keeps cables off edges. Node and hub points are interior points, so segments meeting them are legal.
5. **Repair geometry deterministically** (make valid), log every repair, never silently accept an altered shape. [A11, P11]
6. **Segment arithmetic.** Use exact comparisons on rounded values for horizontal/vertical tests, and a single `almost_equal` helper with the 1 mm tolerance for point equality. No scattered epsilons.
7. **Merge collinear segments** before counting overlap and bends so a straight stretch split into pieces is not counted as bends.
8. **Validate polylines against the original geometry**, not the grid.
9. **One coordinate transform** for output (`to_output_coords`); never flip axes elsewhere.

## 10. Performance expectations

- At most 10 rooms, so at most 1,023 connected subsets; at most 120 orders per hub; hubs usually 1 or 2.
- Distance table: one A\* per pair among at most 6 stops per hub per hub candidate; cached by (endpoint pair, geometry version, policy).
- Cache route candidates; invalidate on any geometry change (needed later for RewireLess).
- Solving can run partitions in parallel across processes.
- Target: a valid incumbent in a few seconds, improvement until `time_budget_s`.

## 11. Testing

| Test | Purpose |
|---|---|
| Hand-scored polylines | Known L, X, O, B for the examples on slide 7 and slide 5 (H1 = 23.8 m, 8 bends; H2 = 25.2 m, 12 bends; J = 27.8 and 31.2) |
| Mutation tests | Corrupt valid designs and require detection: remove a node, six nodes in a hub, hub in a wall, cable through a window, diagonal segment, ring ending at another hub, broken territory, cable outside inner. Crossings and overlaps must stay valid and raise the score, not invalidate the design |
| Unit tests | Scale conversion (slide 4 example: 36,000 / 110 gives 18.09 units/m), hub zone, room graph, lower bound, partition enumeration |
| Contract tests | Hand-made `Territory` list for Part 3, hand-made `Design` for Part 2 |
| Plan suite | 20 to 30 varied plans first (single-door rooms, narrow passages, irregular polygons, tight capacities), then hundreds, then large offline runs once stable |
| Failure review | Log every crash, invalid result, timeout and unresolved count |

## 12. Later (not v1)

RewireLess (block a door, least rework), parallel lanes, joint multi-hub tuning, ML solver selection, LangGraph for natural-language requests. All use the same deterministic functions; none is required for v1.
