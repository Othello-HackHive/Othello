# PRD: Cable Ring Solver (NodeRithm)

Version 1.0 (frozen v1 scope). Source of truth: the official 8-slide PPT "Cable Ring Challenge - Problem Statement4.pptx" (UR Hackathon 2026H2). Where this document and the PPT differ, the PPT wins.

Status labels used in every document:

- **[CONFIRMED]**: stated in the official PPT (slide number given).
- **[ASSUMED]**: a temporary decision of ours where the PPT is silent. Safe default, switchable.
- **[PENDING]**: depends on the organiser JSON, the organiser checker, or an organiser answer. Do not treat as fact.

---

## 1. Purpose

Build a Python program that takes one ResPlan residential floor plan (organiser JSON) and, with no manual adjustment, produces a complete, valid hub-and-cable design:

1. How many hubs, and where each one goes.
2. Which rooms and nodes each hub serves.
3. The order in which each hub visits its nodes.
4. The physical cable route of every closed ring.

The output is scored by the organisers' checker, so correctness comes before cleverness. [CONFIRMED slide 1, 6]

## 2. The problem in plain words

A house has rooms, walls, doors and windows. Each eligible room (living room, bedroom, kitchen, storage) has one fixed point called a **node** (a device). We place **hubs** (control boxes). Each hub serves a connected group of rooms and gets one closed cable loop: hub, then every one of its nodes exactly once, then back to the same hub. The cable moves only horizontally and vertically, stays inside the house, and passes between rooms only through doors or open gaps.

Terminology: **hub** (not panel) and **node** (not device). Use these words in code and JSON. [CONFIRMED slide 1, 2; the first PS sheet used "panel/device"]

## 3. Who uses this

| Consumer | What they need |
|---|---|
| Organiser checker | One official JSON per plan; recomputes every metric from our polylines |
| Judges | A readable floor-plan overlay and a correct, strong result |
| Ritvik (frontend) | Stable result JSON and overlay data to draw (no backend knowledge needed) |
| Saket, Aadith, Dev (backend) | Clear parts, frozen hand-off contracts, a trusted checker |

## 4. Requirements

### 4.1 Hard rules (a plan is invalid if any is broken) [CONFIRMED slide 3]

| ID | Rule |
|---|---|
| H1 | **Coverage**: every eligible room and its node is assigned to exactly one hub; none omitted, split or duplicated. |
| H2 | **Capacity**: each hub serves at most 100 m² of assigned eligible-room area and at most 5 nodes. Both limits apply together. |
| H3 | **Zone**: a hub's rooms are connected through doors or open passages only. Windows, wall-only adjacency and the entrance do not count. |
| H4 | **Ring**: one closed ring per hub: H to every assigned node once to the same H. No branches, never ends at another hub. Retracing a door to reach a single-door room is allowed. |
| H5 | **Free space**: cable stays inside the inner boundary, outside walls and windows, runs only horizontally or vertically, and passes between rooms only through door or open-passage gaps. |
| H6 | **Hub point**: inside an eligible room assigned to that hub, at least 0.5 m from walls. A hub and a node may share a room. |

### 4.2 Fixed inputs [CONFIRMED slide 2]

- Eligible rooms: living, bedroom, kitchen, storage. Each polygon is one room with one node.
- Node points are given, fixed, one per room, 0.5 m from walls. Teams cannot move them.
- Excluded from node and hub: bathroom, balcony, stair, garden, pool, parking, land.
- Coordinates are drawing units. Convert once per plan with `scale = sqrt(inner.area / area)` (slide 4). Never use `net_area` (0 in 32% of plans).
- Benchmark plans are filtered: every eligible room at most 100 m², none under 2 m², and all room points in one connected piece of (inner minus wall minus window) (notes slides 2, 3). At most 10 eligible rooms per plan (notes slide 2).

### 4.3 Ranking [CONFIRMED slides 3, 6]

1. **Fewest hubs H** (always decides first).
2. Then lowest cost **J = L + 5X + O + 0.5B** (metres).
3. Tie-break: smaller assigned-area gap between hubs.
4. Tolerance 1 mm: points within 1 mm are the same point; lines within 1 mm are the same line.

| Symbol | Meaning | Weight |
|---|---|---|
| L | Cable length, every leg of every ring, return legs included | 1 per m |
| X | Crossings: points where two cables meet away from a node or hub, within one ring or between rings | 5 each |
| O | Overlap: metres where two legs run on the same line; a stretch covered by k legs counts (k-1) times | 1 per m |
| B | Bends: direction changes along each closed ring, nodes and hub included; a reversal counts once | 0.5 each |

The weights are starting values the organisers may change after reference runs (slide 6), so they are configuration, not constants. [CONFIRMED that they may change]

Scoring across plans: values in our JSON are ignored; the checker recomputes from polylines; rank points add up over hidden unseen plans; **an invalid or missing plan scores zero**. [CONFIRMED slide 6]

### 4.4 Deliverables per plan [CONFIRMED slide 3, 8]

- Official output JSON in metres, origin lower-left, y up (slide 8 schema; see BACKEND_SCHEMA.md).
- Floor-plan overlay: zones, hub and node IDs, visiting order, full rings with the return leg, violations in red.
- A visualisation set for the final presentation (slide 8).

### 4.5 Our own functional requirements

| ID | Requirement | Status |
|---|---|---|
| F1 | Process an unseen plan end to end with no manual step | [CONFIRMED slide 1] |
| F2 | An independent checker, separate from the solver, recomputes every hard rule and J from the final polylines | [ASSUMED design choice] |
| F3 | Always return the best design that the checker has passed; never submit an unchecked or invalid design | [ASSUMED design choice] |
| F4 | Build a simple valid design first, then improve it within a time budget | [ASSUMED design choice] |
| F5 | Report honestly: proven minimum hubs vs best found vs unresolved | [ASSUMED design choice] |
| F6 | Keep the official JSON separate from extra overlay data | [ASSUMED design choice] |
| F7 | All settings (weights, caps, clearances, policies, time budget) live in one SolverConfig | [ASSUMED design choice] |

## 5. Scope

### In v1

Reader and normaliser, room-link graph, hub-count lower bound, connected room grouping, hub candidates, visiting-order enumeration, rectilinear routing with a bounded diverse route library, whole-design route selection, independent checker and scorer, official JSON, overlay data, command-line runner, many-plan test runs.

### Not in v1 (revisit only after the base passes the checker)

- RewireLess (block a door, least-rework redesign).
- Parallel cable lanes to avoid overlap (an experiment; may add bends and crossings and leans on the metric definition).
- Joint tuning of neighbouring hubs.
- ML, LangGraph or any LLM in the solver.
- Looking up stored answers for hidden plans. Public plans are for development and benchmarking only; the solver always solves the plan it receives.
- Frontend work (Ritvik owns it).

## 6. Success criteria

| Metric | Target |
|---|---|
| Validity rate on test plans | 100% of submitted designs pass our checker |
| Checker trust | Hand-scored examples match exactly; every mutation test is caught |
| Hub count | Reaches the lower bound whenever a valid design at the bound exists |
| J | Measurably lower than the first valid baseline |
| Robustness | Hundreds of unseen plans run without crashes or manual edits |
| Runtime | Within the organiser limit (limit unknown; budget is configurable) |
| Output | JSON matches the slide 8 contract exactly |

## 7. Risks

| Risk | Mitigation |
|---|---|
| Organiser JSON schema unknown | Input adapter isolated; get a sample first; checker and metric maths can be written from the PPT alone |
| Checker interpretation differs from ours | ASSUMPTIONS.md register; switches in SolverConfig; ask organisers |
| Geometry rounding makes good designs fail | Original geometry kept, 1 mm tolerance, grid is only a search aid, checker uses true geometry |
| Route-library explosion | Small diverse library, dedupe, caps, time-bounded combination search |
| Timeout at the lowest hub count | Record as unresolved; never claim a proven minimum without proof |
| Unclear passages through excluded rooms | Three-mode transit policy; conservative default; organiser question |

## 8. Open organiser questions

See ASSUMPTIONS.md section "Organiser questions". The two most important:

1. May cable pass through excluded rooms (e.g. bathroom) inside the inner boundary?
2. Can two eligible rooms count as connected if the only link between them goes through an excluded room?

## 9. Glossary

- **Hub**: control box; one closed ring per hub.
- **Node**: fixed device point, one per eligible room.
- **Ring**: closed cable loop H, N1, ..., Nn, H.
- **Leg**: the cable between two consecutive stops on a ring.
- **Territory**: the group of rooms and nodes assigned to one hub.
- **Inner**: the polygon inside the outer walls; the routing boundary.
- **Open passage**: a wall gap without a door.
- **J**: cost L + 5X + O + 0.5B.
