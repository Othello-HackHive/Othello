"""Audit ResPlan reader output and Part 3 connectivity on real plans.

Usage:
    python real_plan_check.py path/to/ResPlan.pkl 50

The script never claims that an unreachable grid proves a plan is impossible.
It separates input/door-contract problems from normal routing misses and
timeouts so the team can compare reader changes on the same corpus slice.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from shapely.geometry import LineString

from othello.models import SolverConfig
from othello.reader import build_floor_plan_from_resplan, load_resplan_dataset
from othello.routing import DistanceTable, RoutingCache


def _gap_issue(plan, opening) -> str | None:
    if opening.kind != "door":
        return None
    gap = LineString(opening.gap_polyline_m)
    if len(opening.rooms) != 2:
        return "door does not map to exactly two rooms"
    if not gap.is_valid or not gap.is_simple or gap.is_ring or gap.length == 0:
        return "door gap is empty, invalid, or non-simple"
    if not all(math.isfinite(value) for point in opening.gap_polyline_m for value in point):
        return "door gap has non-finite coordinates"
    if not plan.free_space_m.covers(gap):
        return "door gap is outside free space"
    if gap.intersection(plan.walls_m).length > 0 or gap.intersection(plan.windows_m).length > 0:
        return "door gap intersects an obstacle"
    return None


def audit(dataset_path: Path, count: int, route_seconds: float) -> dict[str, object]:
    records = load_resplan_dataset(dataset_path)[:count]
    load_errors: Counter[str] = Counter()
    route_statuses: Counter[str] = Counter()
    gap_issues: Counter[str] = Counter()
    examples: dict[str, object] = {}
    checked_pairs = 0
    routed_pairs = 0
    dropped_doors = 0

    for record_index, record in enumerate(records, start=1):
        plan_id = str(record.get("id", "unknown"))
        print(f"Auditing {record_index}/{len(records)}: plan {plan_id}", file=sys.stderr, flush=True)
        try:
            plan = build_floor_plan_from_resplan(record)
        except Exception as error:  # Report bad source records without stopping the batch.
            label = f"{type(error).__name__}: {error}"
            load_errors[label] += 1
            examples.setdefault(f"load:{label}", plan_id)
            continue

        dropped_doors += sum(note.startswith("Dropped door") for note in plan.repairs)
        for opening in plan.openings.values():
            issue = _gap_issue(plan, opening)
            if issue:
                gap_issues[issue] += 1
                examples.setdefault(f"gap:{issue}", [plan.plan_id, opening.opening_id])

        room_to_node = {node.room_id: node_id for node_id, node in plan.nodes.items()}
        pairs = sorted({tuple(sorted((room_id, neighbour))) for room_id, neighbours in plan.room_graph.items() for neighbour in neighbours})
        if not pairs:
            continue
        deadline = time.monotonic() + route_seconds
        try:
            table = DistanceTable(
                plan,
                {node_id: node.point_m for node_id, node in plan.nodes.items()},
                SolverConfig(),
                RoutingCache(),
                deadline,
            )
        except Exception as error:
            label = f"setup_error: {type(error).__name__}: {error}"
            route_statuses[label] += len(pairs)
            examples.setdefault(f"route:{label}", [plan.plan_id, *pairs[0]])
            continue
        for source_room, target_room in pairs:
            checked_pairs += 1
            result = table.get_route(room_to_node[source_room], room_to_node[target_room])
            route_statuses[result.status] += 1
            if result.status == "success":
                routed_pairs += 1
            else:
                detail = (result.diagnostics or ("no diagnostic",))[0]
                examples.setdefault(f"route:{result.status}: {detail}", [plan.plan_id, source_room, target_room])

    return {
        "plans_requested": len(records),
        "plans_loaded": len(records) - sum(load_errors.values()),
        "load_errors": dict(load_errors),
        "connected_pairs_checked": checked_pairs,
        "connected_pairs_routed": routed_pairs,
        "route_statuses": dict(route_statuses),
        "door_gap_issues": dict(gap_issues),
        "dropped_doors": dropped_doors,
        "examples": examples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit real ResPlan reader and Part 3 routing contracts")
    parser.add_argument("dataset", type=Path, help="Path to ResPlan.pkl")
    parser.add_argument("count", type=int, help="Number of records to inspect")
    parser.add_argument("--route-seconds", type=float, default=10.0, help="Shared Part 3 routing budget per plan")
    arguments = parser.parse_args()
    if arguments.count <= 0 or arguments.route_seconds <= 0:
        raise SystemExit("count and --route-seconds must be positive")
    print(json.dumps(audit(arguments.dataset, arguments.count, arguments.route_seconds), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
