"""Exact small-instance territory generation and partitioning."""

from __future__ import annotations

import math
import time
from collections import deque

from shapely.ops import unary_union

from othello.models import FloorPlan, PartitionOption, SolverConfig, Territory


def hub_lower_bound(plan: FloorPlan, config: SolverConfig | None = None) -> int:
    config = config or SolverConfig()
    return max(
        math.ceil(len(plan.nodes) / config.max_nodes_per_hub),
        math.ceil(plan.total_eligible_area_m2() / config.max_area_per_hub_m2),
    )


def _connected(room_ids: frozenset[str], graph: dict[str, list[str]]) -> bool:
    if not room_ids:
        return False
    visited = {next(iter(room_ids))}
    queue = deque(visited)
    while queue:
        current = queue.popleft()
        for neighbour in graph.get(current, []):
            if neighbour in room_ids and neighbour not in visited:
                visited.add(neighbour)
                queue.append(neighbour)
    return visited == set(room_ids)


def _node_ids_by_room(plan: FloorPlan) -> dict[str, str]:
    return {node.room_id: node_id for node_id, node in plan.nodes.items()}


def _valid_subsets(plan: FloorPlan, config: SolverConfig) -> list[frozenset[str]]:
    room_ids = sorted(room.room_id for room in plan.eligible_rooms())
    result: list[frozenset[str]] = []
    for mask in range(1, 1 << len(room_ids)):
        subset = frozenset(room_ids[index] for index in range(len(room_ids)) if mask & (1 << index))
        if len(subset) > config.max_nodes_per_hub or not _connected(subset, plan.room_graph):
            continue
        if sum(plan.rooms[room_id].area_m2 for room_id in subset) > config.max_area_per_hub_m2 + config.tolerance_m:
            continue
        if all(plan.hub_zones_m[room_id].is_empty for room_id in subset):
            continue
        result.append(subset)
    return result


def generate_partitions(
    plan: FloorPlan,
    hub_count: int,
    config: SolverConfig | None = None,
    deadline: float | None = None,
) -> tuple[list[PartitionOption], str]:
    """Return exact connected set partitions, or ``unresolved`` at a deadline."""

    config = config or SolverConfig()
    deadline = deadline if deadline is not None else float("inf")
    room_ids = frozenset(room.room_id for room in plan.eligible_rooms())
    by_room: dict[str, list[frozenset[str]]] = {room_id: [] for room_id in room_ids}
    for subset in _valid_subsets(plan, config):
        for room_id in subset:
            by_room[room_id].append(subset)

    solutions: list[list[frozenset[str]]] = []

    def search(uncovered: frozenset[str], chosen: list[frozenset[str]]) -> bool:
        if time.monotonic() >= deadline:
            return False
        if not uncovered:
            if len(chosen) == hub_count:
                solutions.append(chosen.copy())
            return True
        if len(chosen) >= hub_count:
            return True
        anchor = min(uncovered)
        for subset in by_room[anchor]:
            if subset <= uncovered and search(uncovered - subset, chosen + [subset]) is False:
                return False
        return True

    if search(room_ids, []) is False:
        return [], "unresolved"

    node_by_room = _node_ids_by_room(plan)
    options: list[PartitionOption] = []
    for solution in solutions:
        territories = []
        for index, subset in enumerate(solution, start=1):
            ordered_rooms = sorted(subset)
            territories.append(
                Territory(
                    territory_id=f"T{index}",
                    room_ids=ordered_rooms,
                    node_ids=[node_by_room[room_id] for room_id in ordered_rooms],
                    area_m2=sum(plan.rooms[room_id].area_m2 for room_id in ordered_rooms),
                    hub_zone_m=unary_union([plan.hub_zones_m[room_id] for room_id in ordered_rooms]),
                )
            )
        areas = [territory.area_m2 for territory in territories]
        options.append(PartitionOption(hub_count=hub_count, territories=territories, area_gap_m2=max(areas) - min(areas)))
    options.sort(key=lambda option: option.area_gap_m2)
    return options[: config.limits.partitions_per_H], "feasible" if options else "proven_infeasible"
