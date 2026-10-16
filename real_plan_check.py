"""Audit ResPlan reader output and Part 3 connectivity on real plans.

Usage:
    python real_plan_check.py path/to/ResPlan.pkl 50
    python real_plan_check.py path/to/ResPlan.pkl 1000 --skip-routing

The script never claims that an unreachable grid proves a plan is impossible.
It checks Part 1 room, node, zone, and opening contracts separately from
routing misses and timeouts, so the team can compare reader changes on the
same corpus slice.
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

from shapely.geometry import LineString, Point

from othello.models import SolverConfig
from othello.reader import build_floor_plan_from_resplan, load_resplan_dataset
from othello.routing import DistanceTable, RoutingCache


def _gap_issue(plan, opening) -> str | None:
    gap = LineString(opening.gap_polyline_m)
    if len(opening.rooms) != 2 or len(set(opening.rooms)) != 2:
        return "opening does not map to exactly two rooms"
    if any(room_id not in plan.rooms or not plan.rooms[room_id].inside_inner for room_id in opening.rooms):
        return "opening maps to a non-interior room"
    if not gap.is_valid or not gap.is_simple or gap.is_ring or gap.length == 0:
        return "opening gap is empty, invalid, or non-simple"
    if not all(math.isfinite(value) for point in opening.gap_polyline_m for value in point):
        return "opening gap has non-finite coordinates"
    if not plan.free_space_m.covers(gap):
        return "opening gap is outside free space"
    if gap.intersection(plan.walls_m).length > 0 or gap.intersection(plan.windows_m).length > 0:
        return "opening gap intersects an obstacle"
    return None


def _plan_issues(plan, clearance_m: float) -> list[str]:
    issues = []
    if not plan.inner_m.is_valid or not plan.free_space_m.is_valid or not plan.walls_m.is_valid or not plan.windows_m.is_valid:
        issues.append("invalid floor or obstacle geometry")
    if plan.free_space_m.difference(plan.inner_m).area > 1e-9:
        issues.append("free space outside inner boundary")
    nodes_by_room = Counter(node.room_id for node in plan.nodes.values())
    eligible_ids = {room.room_id for room in plan.eligible_rooms()}
    for room in plan.rooms.values():
        if not room.polygon_m.is_valid:
            issues.append("invalid room polygon")
        if room.eligible:
            if not room.inside_inner or room.polygon_m.difference(plan.inner_m).area > 1e-9:
                issues.append("eligible room outside inner boundary")
            if nodes_by_room[room.room_id] != 1:
                issues.append("eligible room has incorrect node count")
            zone = plan.hub_zones_m.get(room.room_id)
            if zone is None or not zone.is_valid:
                issues.append("missing or invalid hub zone")
            elif not zone.is_empty:
                if not room.polygon_m.covers(zone) or not plan.inner_m.covers(zone) or not plan.free_space_m.covers(zone):
                    issues.append("hub zone outside room or free interior")
                if (not plan.walls_m.is_empty and zone.distance(plan.walls_m) + 1e-9 < clearance_m) or (
                    not plan.windows_m.is_empty and zone.distance(plan.windows_m) + 1e-9 < clearance_m
                ):
                    issues.append("hub zone below wall or window clearance")
    if set(nodes_by_room) != eligible_ids:
        issues.append("node belongs to non-eligible or missing room")
    for node in plan.nodes.values():
        point = Point(node.point_m)
        if node.room_id in plan.rooms and not plan.rooms[node.room_id].polygon_m.covers(point):
            issues.append("development node outside its room")
        if not plan.free_space_m.covers(point):
            issues.append("development node outside free space")
        if (not plan.walls_m.is_empty and point.distance(plan.walls_m) + 1e-9 < clearance_m) or (
            not plan.windows_m.is_empty and point.distance(plan.windows_m) + 1e-9 < clearance_m
        ):
            issues.append("development node below wall or window clearance")
    room_items = list(plan.rooms.values())
    for index, room in enumerate(room_items):
        for other in room_items[index + 1:]:
            if room.inside_inner and other.inside_inner and room.polygon_m.intersection(other.polygon_m).area > 0:
                issues.append("room interiors overlap")
    for room_id, neighbours in plan.room_graph.items():
        if room_id not in eligible_ids or any(neighbour not in eligible_ids for neighbour in neighbours):
            issues.append("room graph contains non-eligible room")
        for neighbour in neighbours:
            if room_id not in plan.room_graph.get(neighbour, []):
                issues.append("room graph link is asymmetric")
            if not any(set(opening.rooms) == {room_id, neighbour} for opening in plan.openings.values()):
                issues.append("room graph link has no opening")
    opening_lines = [(opening.opening_id, LineString(opening.gap_polyline_m)) for opening in plan.openings.values()]
    for index, (_, gap) in enumerate(opening_lines):
        for _, other in opening_lines[index + 1:]:
            if gap.intersection(other).length > 0 or gap.crosses(other):
                issues.append("ambiguous intersecting openings")
    return sorted(set(issues))


def audit(dataset_path: Path, count: int, route_seconds: float, *, skip_routing: bool = False) -> dict[str, object]:
    records = load_resplan_dataset(dataset_path)[:count]
    load_errors: Counter[str] = Counter()
    route_statuses: Counter[str] = Counter()
    gap_issues: Counter[str] = Counter()
    opening_issues: Counter[str] = Counter()
    plan_issues: Counter[str] = Counter()
    opening_counts: Counter[str] = Counter()
    examples: dict[str, object] = {}
    checked_pairs = 0
    routed_pairs = 0
    dropped_doors = 0
    plans_with_part1_issues = 0

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
        issues = _plan_issues(plan, SolverConfig().hub_wall_clearance_m)
        plans_with_part1_issues += bool(issues)
        for issue in issues:
            plan_issues[issue] += 1
            examples.setdefault(f"part1:{issue}", plan_id)
        for opening in plan.openings.values():
            opening_counts[opening.kind] += 1
            issue = _gap_issue(plan, opening)
            if issue:
                opening_issues[issue] += 1
                if opening.kind == "door":
                    gap_issues[issue] += 1
                examples.setdefault(f"gap:{issue}", [plan.plan_id, opening.opening_id])

        if skip_routing:
            continue
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
        "opening_issues": dict(opening_issues),
        "opening_counts": dict(opening_counts),
        "part1_issues": dict(plan_issues),
        "plans_with_part1_issues": plans_with_part1_issues,
        "dropped_doors": dropped_doors,
        "examples": examples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit real ResPlan reader and Part 3 routing contracts")
    parser.add_argument("dataset", type=Path, help="Path to ResPlan.pkl")
    parser.add_argument("count", type=int, help="Number of records to inspect")
    parser.add_argument("--route-seconds", type=float, default=10.0, help="Shared Part 3 routing budget per plan")
    parser.add_argument("--skip-routing", action="store_true", help="Audit Part 1 contracts without Part 3 route searches")
    arguments = parser.parse_args()
    if arguments.count <= 0 or arguments.route_seconds <= 0:
        raise SystemExit("count and --route-seconds must be positive")
    print(json.dumps(audit(arguments.dataset, arguments.count, arguments.route_seconds, skip_routing=arguments.skip_routing), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
