# Part 1 handoff: Plan to Territories

Part 1 turns a raw floor-plan record into a normalised `FloorPlan` in metres,
then produces connected, capacity-valid `PartitionOption` records for Part 3.
The public contract is in BACKEND_SCHEMA.md sections 3 and 5.

## Current development adapter

The source adapter reads one ResPlan record through
`build_floor_plan_from_resplan(record)`. It computes the scale as
`sqrt(inner.area / area)`, assigns stable room and node IDs, keeps wall/window
geometry, derives door and open-passage links, builds free space and hub zones,
then generates temporary deterministic nodes because public ResPlan has no
Cable Ring node records.

The organiser adapter must replace only the raw-input and node-reading step.
It must preserve the normalised `FloorPlan` and `PartitionOption` contracts.

## Commands for local validation

```powershell
$env:PYTHONPATH = "$PWD/src"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m othello.reader.stats data\resplan\ResPlan.pkl
.\.venv\Scripts\python.exe -m othello.reader.inspector data\resplan\ResPlan.pkl 14433 --out tmp\part1-audit\14433.png
```

The inspector is deliberately temporary and isolated from the production
overlay JSON/PNG owned by Part 2. It shows rooms, walls, windows, door gaps,
accepted zone links, hub-eligible zones, development nodes and warnings.

## Benchmark diagnostics

`benchmark_issues(plan)` reports development-data cases outside the stated
benchmark filters. It does not silently modify or remove them:

- an eligible room over 100 m²;
- an eligible room below 2 m²;
- eligible nodes in more than one free-space component.

The organiser plans should already pass these filters. If they do not, retain
the diagnostic in the runner log and handle the plan as an input-quality issue.

## Remaining organiser integration

Part 1 needs the organiser's one-plan JSON sample to bind its field names,
geometry serialisation, fixed node IDs/points and coordinate orientation. The
team also needs an answer to whether an excluded room may link two eligible
rooms for zone connectivity. The current configuration uses the conservative
default: no.
