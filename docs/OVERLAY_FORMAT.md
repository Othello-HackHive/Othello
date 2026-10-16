# Overlay format (for the frontend)

The backend writes one **overlay file** per plan. It holds everything needed to **draw** the plan: the house, the hubs, the cables, the red problem marks and the score. The frontend only needs to read this file (and, to show the submitted answer, the official file).

Real examples are in [`tests/data/samples/`](../tests/data/samples/README.md). They are generated from the code and a test fails if they ever go out of date, so they always match this note.

Current version: **`schema_version` = 1**.

## Files of one run

For a plan called `PLAN_X` the runner writes into one folder:

| File | What it is | Always written? |
|---|---|---|
| `PLAN_X.overlay.json` | drawing data (this note) | yes, also when there is a problem |
| `PLAN_X.json` | the official answer file (slide 8) | only for a design that passed every rule |
| `PLAN_X.failure.json` | what went wrong | only when no valid design exists |
| `PLAN_X.png`, `PLAN_X.log.txt`, `PLAN_X.settings.json` | debug picture, run diary, settings used | optional |

## Which situation is it? Read `status`

| `status` | Meaning | What to draw |
|---|---|---|
| `"ok"` | a design that passed every rule | house, hubs, cables, score; `valid` is `true` |
| `"invalid"` | a design with broken rules (shown so people can see why) | the same, plus **red** marks from `violations`; `valid` is `false` |
| `"no_design_found"` | nothing could be built | only the house; show `notes` and `bounds_report`; `hubs` is empty and `metrics` is `null` |

## Coordinates

- Everything is in **metres**, in the **same frame as the official file** (`units` is always `"m"`).
- A point is `[x, y]`; the origin is the plan's drawing origin (not shifted), `y` points up.
- `bounds` gives the house corners: `{"min": [x, y], "max": [x, y]}`. `scale` is the drawing units per metre of the source plan (information only).

## Top-level fields

| Field | Type | Notes |
|---|---|---|
| `schema_version` | int | `1` |
| `plan_id` | string | |
| `status` | string | see above |
| `valid` | bool | `true` only for `"ok"` |
| `units` | string | `"m"` |
| `scale` | number | drawing units per metre |
| `bounds` | object | `min`, `max` as `[x, y]` |
| `geometry` | object | the house, see below |
| `nodes` | list | one per eligible room |
| `hubs` | list | empty when there is no design |
| `violations` | list | broken rules, empty when valid |
| `markers` | object | `crossings`, `overlaps`, `bends` |
| `metrics` | object or `null` | the score |
| `checks` | list | one pass/fail line per rule (the audit panel) |
| `bounds_report` | object | the minimum hub count and what was tried |
| `runtime_s` | number or `null` | seconds the solver took, if known |
| `notes` | list of strings | free text, e.g. the reason for a failure |
| `settings_changed` | object | settings that differ from the defaults |

### `geometry`

A **polygon** is a list of rings (the outer ring first, then holes); a **ring** is a list of `[x, y]` points, closed (first point repeated at the end).

- `inner`: list of polygons, the inside of the house (the cable boundary).
- `walls`, `windows`: lists of polygons.
- `rooms`: list of `{room_id, kind, eligible, inside_inner, area_m2, hub_id, polygon, polygons}`.
  `polygon` is the outer ring of the largest piece (handy for simple drawing); `polygons` is the full shape.
  `eligible` is `false` for bathroom, balcony etc. (draw them grey). `hub_id` is the hub that owns the room, or `null`.
- `openings`: list of `{opening_id, kind, rooms, gap, counts_for_zone}`.
  `kind` is `"door"`, `"open_passage"` or `"entrance"`. `gap` is two points, the line where a cable may cross a wall. `counts_for_zone` is `false` for the entrance.

### `nodes`

`{node_id, room_id, point, hub_id, order}`. `order` is the node's position in its hub's ring (1, 2, 3, ...), or `null` without a design.

### `hubs`

`{hub_id, room_id, point, color_index, rooms, nodes, sequence, ring_length_m, area_m2, legs}`

- `color_index` is 0, 1, 2, ...: use it to pick the hub's colour (the frontend's zone colours work well).
- `sequence` is the visiting order, e.g. `["H1","N5","N4","N1","H1"]` (starts and ends at the hub).
- `legs` is a list of `{from, to, order, length_m, polyline, doors_used}`.
  `order` is 1, 2, 3, ...; **the last leg's number is the return trip to the hub**. `polyline` is a list of `[x, y]` corners, only horizontal and vertical pieces.

### `violations` (red marks)

`{type, hub_id, ring_id, segment_index, point, detail}`

- `type` is one of the 19 codes (for example `WALL_INTERSECTION`, `WINDOW_INTERSECTION`, `OUTSIDE_INNER`, `CAPACITY_NODES`, `ZONE_DISCONNECTED`, `RING_NODE_MISSING`, `NODE_MOVED`).
- `segment_index` is the **leg number inside the ring, starting at 0**, or `null`.
- `point` is where to put the red mark, or `null` when the problem has no place (for example "a room belongs to no hub").
- `detail` is a short human sentence.

Crossings and overlaps are **not** violations: they only cost points and are shown through `markers`.

### `markers`

- `crossings`: list of `[x, y]` (draw an X).
- `overlaps`: list of `[[x1, y1], [x2, y2]]` stretches where cables lie on one line (draw a thick translucent line).
- `bends`: list of `[x, y]` corners (usually too many to draw; optional).

### `metrics` (the score, lower is better)

`{hub_count, L, X, O, B, J, area_gap_m2, per_hub}` where `J = L + 5X + O + 0.5B`.
`per_hub` maps a hub id to `{ring_length_m, bends, node_count, area_m2}`.
Fewer hubs always ranks first; `J` decides between designs with the same hub count.

### `checks` (the audit panel)

A list of `{rule_id, label, passed, value, detail}`, in this order: `schema`, `coverage`, `capacity`, `zones`, `hub_points`, `rings`, `nodes`, `free_space`, `doors`. `value` is a short text such as `"3/5 NODES · 71.0/100 M2"`. Empty when there is no design.

### `bounds_report`

`{lower_bound_hubs, proven_minimum, tried}`.
`proven_minimum` is `true`, `false` or `null` (unknown). `tried` maps a hub count (as a text key) to `"feasible"`, `"proven_infeasible"` or `"unresolved"`.

## The failure file (`PLAN_X.failure.json`)

Shown when there is no design: `{schema_version, status: "failed", plan_id, code, stage, message, hint, details, settings_changed, created_at}`. `code` is a short word such as `NO_DESIGN_FOUND`, `BAD_INPUT` or `BAD_SETTINGS`; `message` and `hint` are plain sentences you can show to the user.

## Rules for changing this format

- Adding a new field keeps `schema_version` at 1: **ignore fields you do not know**.
- Renaming or removing a field, or changing a meaning, raises `schema_version`.
- The backend refuses to write an overlay that does not match its own schema check, and the samples are tested against this note, so what you read here is what you get.
