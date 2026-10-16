# Part 1 reader audit — 17 October 2026

The ResPlan adapter now preserves individual source room and door components. It clips rooms to the house boundary, gives enclosed smaller interior rooms their own area, and uses one precision grid for room ownership and the inner boundary. Door crossing lines run across the doorway. Inferred passages require a continuous free-space crossing between the two named rooms. Hub zones are checked against their room, the inner boundary, free space, and the 0.5 m wall/window clearance.

## Reproduce the checks

```powershell
python real_plan_check.py data/resplan/ResPlan.pkl 50
python real_plan_check.py data/resplan/ResPlan.pkl 1000 --skip-routing
python -m pytest -q tests/test_part1_foundation.py
```

The first 1,000 ResPlan records loaded without errors. The Part 1 audit found no malformed openings, room overlaps between interior rooms, invalid hub zones, or clearance violations. Part 3's strict geometry reader also accepted all 1,000. The audit emitted 4,924 valid door contracts and 837 inferred open-passage contracts. It dropped 1,469 raw door shapes that could not establish a unique internal free-space connection or duplicated another crossing contract.

Sixteen of those 1,000 development plans contain at least one generated node below the required 0.5 m wall/window clearance. In one plan, the remaining eligible room fragment has no free-space point at all. ResPlan does not provide the competition's fixed nodes; the adapter logs these fallback points so such plans are not mistaken for verified competition inputs. It does not silently remove an eligible room to make the test pass.

With the audit script's default ten-second routing budget, the first 50 plans loaded with zero Part 1 issues. Of 167 connected eligible-room pairs, 62 routed, 101 timed out, and 4 returned a Part 3 path-validation rejection. A timeout is not evidence that the rooms are disconnected. In plan 1428 the rejected R03-to-R04 transition has no source opening and the rooms are separated by about 0.173 m; its room graph correctly omits that edge. Plan 7067 now has the previously missing bedroom door; its remaining rejection is an unlabelled path span that leaves and re-enters the living room.

The territory generator also produced 366 partition options across lower-bound and nearby hub counts on those 50 plans. Every checked option covered each eligible room exactly once, respected node and area caps, and contained connected assigned rooms.

The specific doorway checks requested by the team are resolved as follows: plan 624783's rejected raw doors do not join two free interior rooms, but its kitchen has a verified open passage to the living room; plan 10808 keeps its internal doors; plan 2814 keeps its valid doors, while its kitchen has no source door or wall-free passage and remains a separate territory.

The repository test suite passes with the known Part 2 runner deadline test deselected (`1168 passed, 1 deselected, 1 xpassed`). That test currently expects a `time_budget_s=1e-9` run to fail, but the runner returns `ok`; it also fails when run alone and is outside Part 1 ownership.

The official organiser input schema and fixed node coordinates are still pending. When supplied, the organiser adapter must attach those nodes and apply the same geometry contracts.
