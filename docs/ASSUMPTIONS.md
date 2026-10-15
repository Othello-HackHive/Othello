# Assumptions and Verification Register

Every item that is not a plain fact from the PPT lives here. Status labels: **[CONFIRMED]** (in the PPT), **[ASSUMED]** (our temporary default, switchable in SolverConfig), **[PENDING]** (needs the organiser JSON, checker or an answer).

The rule for the team: an **[ASSUMED]** or **[PENDING]** item must never silently become a hard-coded constant. It goes through `SolverConfig`.

When a **[PENDING]** item is answered, update its row here first, then the TRD, then the code.

---

## 1. Confirmed facts (from the PPT and its speaker notes)

| ID | Fact | Source |
|---|---|---|
| C1 | Cap is 5 nodes and 100 m² of assigned eligible-room area per hub; both apply together. (The original PS sheet said 20; the deck replaced it.) | Slide 1, 2, notes 1 |
| C2 | Nodes are fixed inputs, one per eligible room, 0.5 m from walls; teams cannot move them | Slide 2 |
| C3 | Eligible: living, bedroom, kitchen, storage. Excluded (no node, no hub): bathroom, balcony, stair, garden, pool, parking, land | Slide 2 |
| C4 | Cable runs horizontal and vertical only | Slide 1, 3 |
| C5 | Cable stays inside the inner boundary, outside walls and windows, never through external areas; between rooms only through door or open-passage gaps | Slide 1, 3 |
| C6 | Hub: inside an eligible room assigned to it, at least 0.5 m from walls; may share a room with a node | Slide 3 |
| C7 | Zone: a hub's rooms connect through doors or open passages only; windows, wall-only adjacency and the entrance do not count | Slide 3 |
| C8 | Ranking: fewest hubs, then J = L + 5X + O + 0.5B; tie-break smaller assigned-area gap between hubs | Slide 3, 6 |
| C9 | Weights are starting values, fixed after reference runs | Slide 6 |
| C10 | Tolerance 1 mm: points within 1 mm are the same point; lines within 1 mm are the same line | Slide 6, 7 |
| C11 | Scale per plan: `sqrt(inner.area / area)` drawing units per metre; do not use `net_area` | Slide 4 |
| C12 | Retracing a door to reach a single-door room is allowed; same-line shared length counts as overlap; parallel runs on different lines do not | Slide 2, 3, 5, 7 |
| C13 | Output in metres, origin lower-left, y up; reported metrics in our JSON are ignored; the checker recomputes from polylines | Slide 6, 8 |
| C14 | An invalid or missing plan scores zero; rank points add up over hidden plans | Slide 6 |
| C15 | At most 10 eligible rooms per plan; median eligible area 95 m²; benchmark filters (room under 100 m², over 2 m², one connected free-space piece) | Notes 2, 3 |
| C16 | Free space is described as inner minus wall minus window; balconies lie outside inner | Notes 3, slide 4 |
| C17 | Bend counting: direction changes along each closed ring, nodes included; a reversal counts once | Slide 6 |
| C18 | Crossing counted once per point, within one ring or between rings; meeting at a node does not count | Slide 6, 7 |
| C19 | Overlap: a stretch covered by k legs counts (k-1) times its length; a retrace on the same line counts | Slide 6, 7 |
| C20 | Deliverables: overlay (zones, IDs, order, full rings with return leg, violations in red) plus JSON | Slide 3, 8 |

## 2. Assumed (our defaults; switchable)

| ID | Assumption | Default | Risk if wrong | Config key |
|---|---|---|---|---|
| A1 | Cable passage through excluded interior rooms | `avoid_if_possible`: use an excluded interior room only if no other route exists | If allowed and we avoid: slightly longer cable. If forbidden and we use: plan invalid (zero) | `cable_transit_policy` (forbid, avoid_if_possible, allow) |
| A2 | Zone links count only between assigned eligible rooms (no linking through an excluded room) | direct links only | May use more hubs than necessary | `zone_link_through_excluded` (false) |
| A3 | Tie-break "assigned-area gap" definition | max hub area minus min hub area | Only matters on exact ties | `tiebreak_gap_definition` |
| A4 | Cable clearance from wall and window edges in the search | 0.02 m margin (the checker itself uses only the 1 mm tolerance) | Too large: narrow doors lost. Too small: touches fail the checker | `cable_wall_margin_m` |
| A5 | Hub clearance: feasible hub region = eligible room minus 0.5 m buffers around real wall shapes only (open passages are not walls); windows are in walls so they fall inside the wall buffer | as stated | Too strict: valid hub spots lost | `hub_wall_clearance_m` (0.5) |
| A6 | One node per eligible room, so node count equals eligible-room count | as stated | none expected | none |
| A7 | Crossing definition for a vertex of one cable touching the interior of another (T-touch): counts as a meeting point (a crossing) | counts | Over- or under-counts X | `t_touch_counts_as_crossing` (true) |
| A8 | Overlap endpoints do not add extra crossings | no extra crossing | Slight X miscount | `overlap_endpoint_crossing` (false) |
| A9 | Reversal at a single-door room node counts one bend (per slide 6) | 1 bend | Slight B miscount | none (follows PPT) |
| A10 | Single-door room: in and out on the same line in v1 (overlap counted) | same line | Higher O than the parallel-lane trick | `parallel_lane_offset_m` (0 = off) |
| A11 | Input polygons may be invalid and are repaired (make valid) by us if the organiser JSON does not do it | we repair, log every change | Altered shape; must be logged | `repair_geometry` (true) |
| A12 | Time budget per plan | 60 s default, configurable | Organiser limit unknown | `time_budget_s` |
| A13 | Search caps (hub candidates, orders kept, route library size, beam width) | see TRD section 6 | Quality vs speed | `limits.*` |
| A14 | A plan with one eligible room over 100 m² cannot be solved; return a structured failure | structured failure, not invented JSON | none | none |

## 3. Pending (need organiser input or files)

| ID | Question | Why it matters | Blocks |
|---|---|---|---|
| P1 | Sample organiser input JSON schema (units, field names, ids, node points, doors, open passages, y direction) | Whole reader | Part 1, schema freeze |
| P2 | Organiser checker or a reference output | Exact rule behaviour | Part 2 |
| P3 | May cable pass through excluded rooms inside the inner boundary (e.g. bathroom)? | A1 | Part 3 |
| P4 | Can two eligible rooms be connected if the only link goes through an excluded room? | A2 | Part 1 |
| P5 | How are open passages encoded in the input? | Zone graph | Part 1 |
| P6 | Definition of "origin lower-left": plan bounding box or inner bounding box? Is input y-down or y-up? | Output transform | Part 1, 2 |
| P7 | T-touch and overlap-endpoint conventions for crossings (A7, A8) | X counts | Part 2 |
| P8 | Is a reversal at a node 1 bend (slide 6) even when slide 3 says "all 90° turns"? | B | Part 2 |
| P9 | Exact tie-break definition (A3) | ties | Part 2 |
| P10 | Runtime limit per plan | Time budget | Part 3 |
| P11 | Who repairs invalid polygons, the organiser JSON or us? (A11) | Reader | Part 1 |
| P12 | Are the weights 1/5/1/0.5 final? | Config values | none (configurable) |
| P13 | Is the parallel-lane trick (offsetting out and back runs) accepted? Slides 5 and 7 suggest yes. | Lower O | Later experiment |

## 4. Organiser questions (copy and send)

1. Please share a sample input JSON and, if possible, the checker or one reference output.
2. Can cable pass through excluded rooms (like the bathroom) when they are inside the inner boundary, or must it only run through rooms that have nodes?
3. Can two rooms count as connected for a hub if the only link between them goes through a bathroom (an excluded room)?
4. How are open passages encoded in the input JSON?
5. Is the input y-down or y-up, and what is the origin of the output coordinates?
6. How are crossings counted when a cable endpoint touches the middle of another cable, and when two cables overlap?
7. Is there a runtime limit per plan?
8. Is the double run in and out of a single-door room on two different parallel lines accepted?
