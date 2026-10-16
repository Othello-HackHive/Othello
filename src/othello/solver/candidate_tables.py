"""Share a bounded Hanan terminal set across nearby hub candidates."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from othello.routing import DistanceTable, RoutingCache
from othello.routing.grid import _Budget, _coordinate_lines, _read_geometry

MAX_PROJECTED_INTERSECTIONS = 10_000
MAX_CANDIDATES_PER_FAMILY = 8


class _CandidateTable:
    """Present the selected candidate as the real hub ID to solver callers."""

    def __init__(self, shared: DistanceTable, hub_id: str, alias: str,
                 node_ids: Sequence[str]):
        self._shared = shared
        self._hub_id = hub_id
        self._alias = alias
        self.deadline = shared.deadline
        self.stops = {hub_id: shared.stops[alias], **{
            node_id: shared.stops[node_id] for node_id in node_ids
        }}

    def get_route(self, from_id: str, to_id: str, initial_direction: str | None = None):
        source = self._alias if from_id == self._hub_id else from_id
        target = self._alias if to_id == self._hub_id else to_id
        return self._shared.get_route(source, target, initial_direction)


def candidate_tables(plan: Any, hub_id: str, node_ids: Sequence[str], candidates: Sequence[Any],
                     config: Any, cache: RoutingCache, deadline: float | None
                     ) -> Iterator[tuple[Any, _CandidateTable | DistanceTable]]:
    """Yield original candidate order, using one safe family graph per batch.

    The ordinary DistanceTable remains the fallback for a family whose projected
    coordinate product is too large or whose geometry cannot be pre-read. A
    family graph only replaces a leg when the shared graph connects that pair;
    DistanceTable retains its existing pair-specific fallback and policy rules.
    """
    nodes = {node_id: plan.nodes[node_id].point_m for node_id in node_ids}
    if not candidates:
        return
    try:
        geometry = _read_geometry(plan, _Budget(deadline))
    except Exception:  # noqa: BLE001  family preflight is optional; the ordinary table reports errors
        geometry = None

    def projected(items: Sequence[Any]) -> int:
        if geometry is None:
            return MAX_PROJECTED_INTERSECTIONS + 1
        stops = {f"__hub_candidate_{index}": candidate.point_m
                 for index, candidate in enumerate(items)}
        stops.update(nodes)
        try:
            xs, ys = _coordinate_lines(plan, geometry, stops, config.cable_wall_margin_m,
                                       _Budget(deadline))
            return len(xs) * len(ys)
        except Exception:  # noqa: BLE001  preserve pair-specific routing as the safe fallback
            return MAX_PROJECTED_INTERSECTIONS + 1

    batches: list[list[Any]] = []
    current: list[Any] = []
    for candidate in candidates:
        proposed = [*current, candidate]
        if current and (len(proposed) > MAX_CANDIDATES_PER_FAMILY or
                        projected(proposed) >= MAX_PROJECTED_INTERSECTIONS):
            batches.append(current)
            current = [candidate]
        else:
            current = proposed
    if current:
        batches.append(current)

    for batch in batches:
        if len(batch) == 1 or projected(batch) >= MAX_PROJECTED_INTERSECTIONS:
            for candidate in batch:
                yield candidate, DistanceTable(plan, {hub_id: candidate.point_m, **nodes},
                                               config, cache, deadline)
            continue
        stops = {f"__hub_candidate_{index}": candidate.point_m
                 for index, candidate in enumerate(batch)}
        stops.update(nodes)
        shared = DistanceTable(plan, stops, config, cache, deadline)
        for index, candidate in enumerate(batch):
            yield candidate, _CandidateTable(shared, hub_id, f"__hub_candidate_{index}", node_ids)
