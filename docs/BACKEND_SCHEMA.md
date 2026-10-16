# Backend Schema

Defines every data model, the input adapter, the two hand-off contracts between the three work parts, the official output JSON and the separate overlay JSON. The models are written as Pydantic v2 sketches; field names are the contract.

Status labels: **[CONFIRMED]** (PPT), **[ASSUMED]**, **[PENDING]** (see ASSUMPTIONS.md).

Rules for this file:

- **Naming:** hub, node (not panel, device). IDs are strings: rooms `R01`, nodes `N1`, hubs `H1`, doors `D1`, passages `P1`. **[CONFIRMED slide 8 uses H1, N1, R05]**
- **Units:** every `*_m` or `Coord` in a model is **metres**; `*_units` is raw drawing units. Never mix them in one field. [CONFIRMED slide 4]
- **Contracts are frozen.** Contract 1 and Contract 2 below change only if all three people agree and this file is updated first.

---

## 1. Layers

```
Domain models   (this file)        FloorPlan, Room, Node, Hub, Ring, Route, Territory, Design, SolverConfig
Solving         (Parts 1 and 3)    graphs, candidates, routing, scoring
Application     (Part 2)           input adapter, runner, JSON writers, overlay data
```

The frontend only reads files from the application layer. It never touches Shapely, graphs or solver internals.

## 2. Primitive types

```python
Coord = tuple[float, float]            # (x, y) in metres unless a name ends with _units
Polyline = list[Coord]                 # >= 2 points; every segment horizontal or vertical

class Violation(BaseModel):
    type: str                          # see section 10 (violation codes)
    hub_id: str | None = None
    ring_id: str | None = None
    segment_index: int | None = None
    point: Coord | None = None         # where it happened (metres), so the overlay can draw it in red
    detail: str = ""
```

## 3. Input adapter and normalised FloorPlan

### 3.1 Input adapter [PENDING P1]

The organiser input JSON is **one file per plan** [CONFIRMED slide 1]. Its exact schema is not yet known. The adapter is the only code that reads it:

```python
def load_plan(path: str, config: SolverConfig) -> FloorPlan: ...
```

Until a sample arrives, the adapter targets the ResPlan dictionary fields (`inner`, `area`, `net_area`, `wall`, `window`, `door`, `front_door`, and room keys such as `living`, `bedroom`, `kitchen`, `storage`, `bathroom`, `balcony`) as geometry. If the organiser JSON differs, only this function changes; the models below stay.

The adapter must:

1. Read geometry in drawing units.
2. Compute `scale = sqrt(inner.area / area)` once. [CONFIRMED slide 4]
3. Repair invalid polygons deterministically and log each repair. [A11, P11]
4. Convert everything to metres once (keep the original unit geometry too).
5. Mark eligible rooms (living, bedroom, kitchen, storage) and excluded rooms. [CONFIRMED slide 2]
6. Attach each fixed node to its room.
7. Build doors, open passages, free space and the room-link graph.

### 3.2 Models

```python
class Room(BaseModel):
    room_id: str                       # "R01"
    kind: str                          # living | bedroom | kitchen | storage | bathroom | balcony | ...
    eligible: bool                     # true only for living, bedroom, kitchen, storage
    polygon_m: object                  # Shapely Polygon in metres (serialised as WKT or coordinate list)
    polygon_units: object              # original drawing-unit polygon
    area_m2: float                     # polygon area in m2 (never net_area)
    inside_inner: bool                 # false for balcony, garden, parking, land

class Node(BaseModel):
    node_id: str                       # "N1"
    room_id: str
    point_m: Coord                     # fixed input; never moved
    point_units: Coord

class Opening(BaseModel):              # a door or an open passage
    opening_id: str                    # "D1" or "P1"
    kind: str                          # door | open_passage | entrance
    rooms: list[str]                   # rooms it joins (entrance joins only one + outside)
    gap_polyline_m: Polyline           # a short line ACROSS the doorway, in the wall plane, with both ends in free space; the cable crosses it (it is not drawn along the cable's travel direction)
    counts_for_zone: bool              # false for entrance [CONFIRMED slide 3]

class FloorPlan(BaseModel):
    plan_id: str
    scale: float                       # drawing units per metre
    rooms: dict[str, Room]
    nodes: dict[str, Node]
    openings: dict[str, Opening]
    inner_m: object                    # inner polygon (routing boundary)
    walls_m: object                    # union of wall polygons
    windows_m: object                  # union of window polygons
    free_space_m: object               # inner - (walls U windows)
    hub_zones_m: dict[str, object]     # room_id -> allowed hub region (room minus 0.5 m wall buffers)
    room_graph: dict[str, list[str]]   # adjacency over eligible rooms via doors/passages
    excluded_interior_rooms: list[str] # transit candidates (policy-controlled)
    output_transform: dict             # info needed by to_output_coords (origin, y flip) [PENDING P6]
    repairs: list[str]                 # log of geometry repairs applied
```

Derived helpers on `FloorPlan`: `eligible_rooms()`, `total_eligible_area_m2()`, `hub_lower_bound()`.

## 4. SolverConfig

All settings, with their default and status. Nothing here is a hidden constant elsewhere in the code.

```python
class Weights(BaseModel):
    length: float = 1.0                # L  [CONFIRMED slide 6; may change, C9]
    crossing: float = 5.0              # X
    overlap: float = 1.0               # O
    bend: float = 0.5                  # B

class Limits(BaseModel):
    partitions_per_H: int = 12
    hub_candidates: int = 40
    orders_kept: int = 12
    routes_per_leg: int = 6
    beam_width: int = 20
    exact_combo_limit: int = 20000

class SolverConfig(BaseModel):
    # official rules [CONFIRMED]
    max_nodes_per_hub: int = 5
    max_area_per_hub_m2: float = 100.0
    hub_wall_clearance_m: float = 0.5
    tolerance_m: float = 0.001
    weights: Weights = Weights()
    # policies [ASSUMED / PENDING]
    cable_transit_policy: str = "forbid"              # forbid | avoid_if_possible | allow   (A1, P3)
    zone_link_through_excluded: bool = False          # (A2, P4)
    tiebreak_gap_definition: str = "max_minus_min"    # (A3, P9)
    cable_wall_margin_m: float = 0.02                 # (A4)
    t_touch_counts_as_crossing: bool = True           # (A7, P7)
    overlap_endpoint_crossing: bool = False           # (A8, P7)
    parallel_lane_offset_m: float = 0.0               # 0 = off in v1 (A10)
    repair_geometry: bool = True                      # (A11)
    # search
    time_budget_s: float = 60.0                       # (A12, P10)
    final_check_reserve_fraction: float = 0.10
    hub_grid_step_m: float = 0.25
    min_hub_node_distance_m: float = 0.002             # 0 permits coincidence (A15, P14)
    grid_step_m: float = 0.10
    output_precision_decimals: int = 4
    limits: Limits = Limits()
```

## 5. Contract 1: Part 1 to Part 3 (Territories)

Part 1 gives Part 3 the `FloorPlan` plus, for each hub count it tries, a list of `PartitionOption`.

```python
class Territory(BaseModel):
    territory_id: str                  # "T1"
    room_ids: list[str]                # eligible rooms, connected through doors/passages
    node_ids: list[str]                # one per room, at most 5
    area_m2: float                     # sum of room areas, at most 100
    hub_zone_m: object                 # union of hub_zones_m of its rooms (never empty for a valid territory)

class PartitionOption(BaseModel):
    hub_count: int
    territories: list[Territory]       # cover every eligible room exactly once
    area_gap_m2: float                 # tie-break value (max minus min area)

class HubCountStatus(BaseModel):
    lower_bound: int
    tried: dict[int, str]              # hub count -> "feasible" | "proven_infeasible" | "unresolved"
    proven_minimum: bool
```

Guarantees from Part 1: every `Territory` already satisfies H1 (coverage across the option), H2 (capacity) and H3 (zone connectivity). Part 3 does not recheck these but the checker will.

Part 3 can be developed against a **hand-made `PartitionOption`** list; no Part 1 code needed.

## 6. Contract 2: Part 3 to Part 2 (Design)

Part 3 returns one `Design` to the checker.

```python
class RouteLeg(BaseModel):
    from_id: str                       # "H1" or "N3"
    to_id: str
    polyline: Polyline                 # metres, axis-aligned
    doors_used: list[str] = []         # opening ids (for diagnostics and the overlay)

class Ring(BaseModel):
    hub_id: str                        # "H1"
    sequence: list[str]                # ["H1","N1","N3","N2","H1"]  [CONFIRMED slide 8]
    legs: list[RouteLeg]               # len(sequence) - 1 legs, chained end to start

class Hub(BaseModel):
    hub_id: str
    room_id: str                       # room containing the hub point
    location: Coord                    # metres
    assigned_rooms: list[str]
    assigned_nodes: list[str]
    ring: Ring

class Design(BaseModel):
    plan_id: str
    hubs: list[Hub]
    solver_notes: dict = {}            # internal only; never in the official JSON
```

Part 2 can be developed against **hand-made `Design` objects** (including deliberately broken ones); no Part 3 code needed.

## 7. Checker result and solve result

```python
class Metrics(BaseModel):
    hub_count: int
    L: float
    X: int
    O: float
    B: int
    J: float
    per_hub: dict[str, dict]           # per hub: area_m2, node_count, ring_length_m, bends
    area_gap_m2: float                 # tie-break value

class RuleCheck(BaseModel):            # one line of the audit panel
    rule_id: str
    label: str
    passed: bool
    value: str = ""
    detail: str = ""

class ValidationResult(BaseModel):
    valid: bool
    violations: list[Violation]
    metrics: Metrics | None            # filled when computable (also when valid=False if possible)
    checks: list[RuleCheck]            # per-rule pass/fail summary for the audit panel

class SolveResult(BaseModel):
    status: str                        # "ok" | "no_design_found"
    design: Design | None              # only a checker-verified design
    validation: ValidationResult | None
    hub_count_status: HubCountStatus | None   # proven / best found / unresolved
    lower_bound_hubs: int
    runtime_s: float
    notes: list[str] = []              # reasons for failure, timeouts, repairs
```

Public functions (the same names in every part):

```python
# Part 1
def build_floor_plan(path, config) -> FloorPlan
def generate_partitions(plan, hub_count, config, deadline) -> tuple[list[PartitionOption], str]  # status
def hub_lower_bound(plan, config) -> int
# Part 3
def solve(plan, config, deadline) -> SolveResult      # uses Part 1 functions and returns a Design (not yet verified)
# Part 2   (othello.checker, othello.io, othello.run)
def check_design(plan, design, config=None) -> ValidationResult   # accepts a Design or a dict; a bad design gives valid=False, never an exception
def score_design(plan, design, config=None) -> Metrics            # L, X, O, B, J and the area gap from the polylines only
def write_official_json(plan, design, path, config=None) -> ValidationResult
                                                                  # re-checks the rounded numbers; raises UnverifiedDesignError and writes nothing if invalid
def write_overlay_json(plan, design, validation, path, **options) -> Path   # also for invalid or missing designs
def write_plan_picture(plan, design, validation, path, **options) -> Path   # debug PNG (needs matplotlib)
def run_plan(source, out_dir, config=None, **options) -> RunOutcome         # the runner: load, solve, check, write; never raises for a plan-level problem
def run_batch(source, out_dir, config=None, summary_path=None, **options) -> list[RunOutcome]
```

`RunOutcome` is the runner's own result (plan_id, status `ok` | `no_design_found` | `failed`, hubs, lower_bound, proven_min, metrics, runtime_s, notes, files, failure). It is not part of the hand-off contracts.

## 8. Official output JSON (the only file the organiser reads) [CONFIRMED slide 8]

One file per plan, metres, origin lower-left, y up. [ASSUMED] We write exactly these fields and no extra ones, so debug data can never break the organiser's reader.

```json
{
  "plan_id": "PLAN_0123",
  "units": "m",
  "hubs": [
    {
      "hub_id": "H1",
      "room_id": "R05",
      "location": {"x": 5.2, "y": 3.6},
      "assigned_rooms": ["R05", "R01", "R04"],
      "assigned_nodes": ["N1", "N2", "N3"],
      "closed_sequence": ["H1", "N1", "N3", "N2", "H1"],
      "route_segments": [
        {"from": "H1", "to": "N1", "length_m": 4.8,
         "polyline": [[5.2, 3.6], [7.6, 3.6], [7.6, 1.2]]},
        {"from": "N1", "to": "N3", "length_m": 7.1,
         "polyline": [[7.6, 1.2], [6.6, 1.2], [6.6, 2.2], [1.5, 2.2]]}
      ],
      "ring_length_m": 23.8
    }
  ],
  "reported_metrics": {"hub_count": 2, "total_length_m": 49.0}
}
```

Notes:

- The `route_segments` array lists **every** leg including the return to the hub (the example above is abridged on slide 8).
- `reported_metrics` is ignored by the checker [CONFIRMED slide 6] but we still write correct values.
- Polyline coordinates are rounded to `output_precision_decimals`.
- The writer maps internal coordinates with `to_output_coords` (one function; [PENDING P6]). Default: no change, so output uses the plan's own origin with y up. `FloorPlan.output_transform` may hold `dx`, `dy` (added) and `flip_y` (y negated before `dy` is added); `from_output_coords` is its exact inverse.
- The serialiser builds this file from a `Design` only if `ValidationResult.valid` is true. The check runs on the rounded numbers that will actually be written.

## 9. Overlay JSON (our own file, for Ritvik's frontend; never submitted)

Written next to the official file as `<plan_id>.overlay.json`. The frontend can ignore everything except this file and the official JSON. It carries `schema_version` (currently `1`) and a `status` (`ok`, `invalid` or `no_design_found`), and it is also written when no design exists, so the house can still be drawn.

The full field-by-field description is in **[OVERLAY_FORMAT.md](OVERLAY_FORMAT.md)**, and real examples (kept up to date by a test) are in `tests/data/samples/`. Those two are the reference; this section does not repeat them.

Fixed visual conventions (for the overlay): zones coloured per hub, hub as a square, node as a dot, numbers give visiting order with the last number being the return to the hub, excluded rooms shown grey, violations red. [CONFIRMED slide 3, 5]

## 10. Violation codes

`COVERAGE_MISSING`, `COVERAGE_DUPLICATE`, `CAPACITY_NODES`, `CAPACITY_AREA`, `ZONE_DISCONNECTED`, `HUB_NOT_IN_ROOM`, `HUB_WALL_CLEARANCE`, `RING_NOT_CLOSED`, `RING_WRONG_HUB`, `RING_NODE_MISSING`, `RING_NODE_REPEATED`, `RING_BRANCH`, `SEGMENT_NOT_AXIS_ALIGNED`, `WALL_INTERSECTION`, `WINDOW_INTERSECTION`, `OUTSIDE_INNER`, `ROOM_TRANSITION_NOT_VIA_OPENING`, `NODE_MOVED`, `OUTPUT_SCHEMA`. The same list lives in `othello.models.result.VIOLATION_CODES`.

Crossings and overlaps are **never** violations; they only raise J. [CONFIRMED slide 6]

## 11. Failure model

| Situation | Result |
|---|---|
| A plan has an eligible room over 100 m² | `status = "no_design_found"`, note explains (A14) |
| No partition exists for any hub count tried | `no_design_found`, notes list tried counts |
| Time ran out before any verified design | `no_design_found`, note "timeout" |
| Time ran out but a verified design exists | success: the best verified design is written, with a note |
| Checker rejects the best candidate | Try the next candidate; if none passes, `no_design_found`; never write an invalid official JSON |
| Input adapter cannot read a plan | `InputError` with the field name; the runner records it and continues in batch mode |

Every failure becomes one file, `<plan_id>.failure.json` (named after the input file when the plan could not be read), and **no** official JSON and no picture. Old results of an earlier run are deleted so nothing stale looks current. The note has these fields:

```json
{"schema_version": 1, "status": "failed", "plan_id": "PLAN_0123",
 "code": "NO_DESIGN_FOUND", "stage": "solve", "message": "...", "hint": "...",
 "details": {}, "settings_changed": {}, "created_at": "2026-10-16T12:00:00Z"}
```

`code` is one of `BAD_INPUT`, `INPUT_NOT_FOUND`, `BAD_SETTINGS`, `NO_DESIGN_FOUND`, `DESIGN_INVALID`, `OUTPUT_WRITE_FAILED`, `IO_ERROR`, `INTERNAL_ERROR`. `stage` is one of `load`, `settings`, `solve`, `check`, `write`, `unknown`. `settings_changed` lists only the settings that differ from the defaults. The same codes live in `othello.io.errors`.

## 12. File layout and ownership of schema

Models live in `src/othello/models/` (one file per group: `plan.py`, `config.py`, `design.py`, `result.py`). The three parts import models; they do not redefine them. A model change is a small pull request that all three review.
