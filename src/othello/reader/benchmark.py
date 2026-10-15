"""Checks for the benchmark filters described in the problem statement.

These are diagnostics for raw ResPlan development data, not submission-rule
violations.  Organiser plans are expected to have already passed the filters.
"""

from __future__ import annotations

from shapely.geometry import Point

from othello.models import FloorPlan, SolverConfig


def benchmark_issues(plan: FloorPlan, config: SolverConfig | None = None) -> list[str]:
    config = config or SolverConfig()
    issues: list[str] = []
    for room in plan.eligible_rooms():
        if room.area_m2 > config.max_area_per_hub_m2 + config.tolerance_m:
            issues.append(f"{room.room_id} exceeds the {config.max_area_per_hub_m2:g} m² benchmark capacity")
        if room.area_m2 < config.min_benchmark_room_area_m2 - config.tolerance_m:
            issues.append(f"{room.room_id} is below the {config.min_benchmark_room_area_m2:g} m² benchmark minimum")

    components = [part for part in getattr(plan.free_space_m, "geoms", [plan.free_space_m]) if not part.is_empty]
    occupied_components: set[int] = set()
    for node in plan.nodes.values():
        point = Point(node.point_m)
        occupied_components.update(index for index, component in enumerate(components) if component.covers(point))
    if len(occupied_components) > 1:
        issues.append("Eligible nodes occupy multiple free-space components")
    return issues
