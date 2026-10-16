"""Task 3.9: anytime partition search with explicit proof and verification states."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import math
import time
from typing import Any, Callable

from othello.routing import DistanceTable, RoutingCache

from .f0 import construct_f0_design
from .hub_candidates import HubCandidateLimitError, generate_hub_candidates
from .node_orders import enumerate_visiting_orders
from .route_selection import _invoke_checker, _optional_checker, select_whole_design_routes
from .types import HubCountSummary, OuterSearchResult


class _Deadline(Exception):
    pass


class _Unsupported(Exception):
    pass


class _CandidateConfiguration(Exception):
    pass


def _check(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise _Deadline


def _partition_key(option: Any) -> tuple:
    try:
        count = option.hub_count
        gap = float(option.area_gap_m2)
        territories = tuple(sorted((tuple(sorted(t.room_ids)), tuple(sorted(t.node_ids)), t.territory_id)
                                   for t in option.territories))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("partition needs Contract 1 fields") from exc
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0 or count != len(territories):
        raise ValueError("partition hub_count does not match territories")
    if not math.isfinite(gap) or gap < 0:
        raise ValueError("partition area_gap_m2 must be finite and nonnegative")
    return gap, territories


def _batch(value: Any, count: int) -> tuple[tuple[Any, ...], str]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError("each hub count needs Part 1's (PartitionOption list, status) pair")
    options, status = value
    if not isinstance(options, Sequence) or isinstance(options, (str, bytes)):
        raise ValueError("Part 1 partition options must be a sequence")
    if status not in {"feasible", "proven_infeasible", "unresolved", "timeout"}:
        raise ValueError("Part 1 status must be feasible, proven_infeasible, unresolved or timeout")
    if status == "proven_infeasible" and options:
        raise ValueError("Part 1 cannot prove grouping infeasible and return partitions")
    ordered = tuple(sorted(options, key=_partition_key))
    if any(option.hub_count != count for option in ordered):
        raise ValueError("partition hub_count differs from its batch key")
    return ordered, status


def _ring_variants(plan: Any, territory: Any, hub_id: str, config: Any,
                   cache: RoutingCache, deadline: float | None, cap: int) -> list[tuple[float, dict]]:
    try:
        candidates = generate_hub_candidates(plan, territory, config, deadline)
    except HubCandidateLimitError as exc:
        _check(deadline)
        raise _CandidateConfiguration(str(exc)) from exc
    if candidates.status == "timeout":
        raise _Deadline
    if candidates.status == "unsupported_geometry":
        raise _Unsupported("; ".join(candidates.diagnostics))
    if candidates.status == "no_candidates":
        return []
    found: list[tuple[float, dict]] = []
    for candidate in candidates.candidates:
        _check(deadline)
        stops = {hub_id: candidate.point_m}
        stops.update({node_id: plan.nodes[node_id].point_m for node_id in territory.node_ids})
        table = DistanceTable(plan, stops, config, cache, deadline)
        orders = enumerate_visiting_orders(hub_id, territory.node_ids, table, config, deadline)
        if orders.status == "timeout":
            raise _Deadline
        if orders.status == "unsupported_geometry":
            raise _Unsupported("; ".join(orders.diagnostics))
        for order in orders.orders:
            _check(deadline)
            legs = []
            for source, target in zip(order.sequence, order.sequence[1:]):
                _check(deadline)
                route = table.get_route(source, target)
                if route.status == "timeout":
                    raise _Deadline
                if route.status == "unsupported_geometry":
                    raise _Unsupported("; ".join(route.diagnostics))
                if route.status != "success":
                    break
                if not route.polyline or route.opening_ids is None:
                    raise ValueError("successful route lacks geometry or opening metadata")
                polyline = list(route.polyline)
                if polyline[0] != stops[source] or polyline[-1] != stops[target]:
                    raise ValueError("routing changed an exact endpoint")
                if len(polyline) == 1:
                    polyline.append(polyline[0])
                legs.append({"from_id": source, "to_id": target,
                             "polyline": polyline, "doors_used": list(route.opening_ids)})
            if len(legs) != len(order.sequence) - 1:
                continue
            hub = {"hub_id": hub_id, "room_id": candidate.room_id, "location": candidate.point_m,
                   "assigned_rooms": list(territory.room_ids), "assigned_nodes": list(territory.node_ids),
                   "ring": {"hub_id": hub_id, "sequence": list(order.sequence), "legs": legs}}
            found.append((order.estimated_length_m, hub))
    # Keep one order from each distinct candidate before filling the remaining
    # slots by estimated length. This preserves some hub-location diversity.
    found.sort(key=lambda item: (item[0], item[1]["room_id"], item[1]["location"],
                                 tuple(item[1]["ring"]["sequence"])))
    selected: list[tuple[float, dict]] = []
    selected_locations: set[tuple[str, tuple[float, float]]] = set()
    for item in found:
        _check(deadline)
        identity = (item[1]["room_id"], tuple(item[1]["location"]))
        if identity not in selected_locations:
            selected.append(item)
            selected_locations.add(identity)
            if len(selected) >= cap:
                return selected
    for item in found:
        _check(deadline)
        if len(selected) >= cap:
            break
        if not any(item[1] == existing[1] for existing in selected):
            selected.append(item)
    return selected


def _configuration_designs(plan: Any, option: Any, config: Any, cache: RoutingCache,
                           deadline: float | None, cap: int):
    territories = sorted(option.territories, key=lambda territory: territory.territory_id)
    beam: list[tuple[float, tuple[dict, ...]]] = [(0.0, ())]
    for index, territory in enumerate(territories, start=1):
        _check(deadline)
        variants = _ring_variants(plan, territory, f"H{index}", config, cache, deadline, cap)
        if not variants:
            return
        expanded = [(cost + estimate, (*hubs, hub))
                    for cost, hubs in beam for estimate, hub in variants]
        beam = sorted(expanded, key=lambda item: (item[0], tuple(
            (hub["room_id"], hub["location"], tuple(hub["ring"]["sequence"])) for hub in item[1]
        )))[:cap]
    for _, hubs in beam:
        _check(deadline)
        yield {"plan_id": plan.plan_id, "hubs": list(deepcopy(hubs)),
               "solver_notes": {"stage": "outer", "verification": "not_run"}}


def _design_key(design: dict[str, Any]) -> tuple:
    return tuple((hub["room_id"], tuple(hub["location"]), tuple(hub["ring"]["sequence"]),
                  tuple((tuple(tuple(p) for p in leg["polyline"]), tuple(leg["doors_used"]))
                        for leg in hub["ring"]["legs"])) for hub in design["hubs"])


def _improve_incumbent(plan: Any, design: dict[str, Any], config: Any,
                       checker: Callable[[Any, Any, Any], Any], cache: RoutingCache,
                       deadline: float):
    # Import here because improvement reuses this module's canonical design key.
    from .improvement import improve_design
    return improve_design(plan, design, config, checker, cache, deadline)


def optimize_partitions(
    plan: Any, partition_batches: Mapping[int, tuple[Sequence[Any], str]], config: Any,
    checker: Callable[[Any, Any, Any], Any] | None = None,
    cache: RoutingCache | None = None, deadline: float | None = None,
    lower_bound_hubs: int | None = None,
) -> OuterSearchResult:
    """Anytime search over Part 1's supplied hub-count batches.

    An absent batch or exhausted cable search is unresolved, never a proof.
    A returned design is always independently checker-verified; unverified
    candidates live only in ``provisional_design``. This local result is not
    the shared SolveResult or an official JSON submission.
    """
    if not isinstance(partition_batches, Mapping) or not partition_batches:
        raise ValueError("partition_batches must map hub counts to Part 1 batches")
    if deadline is not None and (isinstance(deadline, bool) or not isinstance(deadline, (int, float))
                                 or not math.isfinite(deadline)):
        raise ValueError("deadline must be a finite absolute monotonic time")
    if cache is not None and not isinstance(cache, RoutingCache):
        raise ValueError("cache must be a RoutingCache")
    if checker is not None and not callable(checker):
        raise ValueError("checker must be callable")
    try:
        partition_cap = config.limits.partitions_per_H
        configuration_cap = config.limits.beam_width
        budget = float(config.time_budget_s)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("config needs limits.partitions_per_H, limits.beam_width and time_budget_s") from exc
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
           for value in (partition_cap, configuration_cap)) or not math.isfinite(budget) or budget <= 0:
        raise ValueError("outer search limits and time_budget_s must be positive")
    counts = sorted(partition_batches)
    if any(isinstance(count, bool) or not isinstance(count, int) or count <= 0 for count in counts):
        raise ValueError("hub-count keys must be positive integers")
    trusted_bound = lower_bound_hubs is not None
    if lower_bound_hubs is None:
        helper = getattr(plan, "hub_lower_bound", None)
        if callable(helper):
            lower_bound_hubs = helper()
            trusted_bound = True
        else:
            lower_bound_hubs = counts[0]
    if isinstance(lower_bound_hubs, bool) or not isinstance(lower_bound_hubs, int) or lower_bound_hubs <= 0:
        raise ValueError("lower_bound_hubs must be a positive integer")
    if counts[0] < lower_bound_hubs:
        raise ValueError("partition batches cannot start below the supplied hub lower bound")
    budget_deadline = time.monotonic() + budget
    effective_deadline = min(deadline, budget_deadline) if deadline is not None else budget_deadline
    route_cache = cache if cache is not None else RoutingCache()
    checker = checker if checker is not None else _optional_checker()
    tried: dict[int, str] = {}
    incumbent: dict[str, Any] | None = None
    best_key: tuple | None = None
    provisional: dict[str, Any] | None = None
    diagnostics: list[str] = []
    partitions_seen = 0
    configs_seen = 0
    timed_out = False
    candidate_configuration_error = False

    def accept(design: dict[str, Any], score: float, hub_count: int, gap: float) -> None:
        nonlocal incumbent, best_key
        key = (hub_count, score, gap, _design_key(design))
        if best_key is None or key < best_key:
            incumbent, best_key = deepcopy(design), key

    for count in range(lower_bound_hubs, counts[-1] + 1):
        if count not in partition_batches:
            tried[count] = "unresolved"
            diagnostics.append(f"H={count}: no Part 1 batch supplied")
            continue
        try:
            _check(effective_deadline)
            options, grouping_status = _batch(partition_batches[count], count)
        except _Deadline:
            tried[count] = "unresolved"
            timed_out = True
            break
        if grouping_status == "proven_infeasible":
            tried[count] = "proven_infeasible"
            continue
        tried[count] = "unresolved"
        if grouping_status == "timeout":
            diagnostics.append(f"H={count}: Part 1 grouping timed out; lower count remains unresolved")
        if len(options) > partition_cap:
            diagnostics.append(f"H={count}: only {partition_cap} of {len(options)} supplied partitions were explored")
        for option in options[:partition_cap]:
            try:
                _check(effective_deadline)
                partitions_seen += 1
                baseline = construct_f0_design(plan, option, config, route_cache, effective_deadline)
                if baseline.status == "timeout":
                    raise _Deadline
                if baseline.status != "constructed_unverified" or baseline.design is None:
                    diagnostics.append(f"H={count}: F0 {baseline.status}: {'; '.join(baseline.diagnostics)}")
                    candidate_configuration_error |= baseline.status == "candidate_configuration_error"
                    continue
                if provisional is None:
                    provisional = deepcopy(baseline.design)
                gap = float(option.area_gap_m2)
                if checker is not None:
                    _, score, failure = _invoke_checker(checker, plan, baseline.design, config)
                    if failure is not None:
                        diagnostics.append(f"H={count}: F0 {failure}")
                    if score is not None:
                        accept(baseline.design, score, count, gap)
                _check(effective_deadline)
                selected = select_whole_design_routes(plan, baseline.design, config, checker, route_cache,
                                                      effective_deadline)
                configs_seen += 1
                if selected.verified and selected.design is not None and selected.J is not None:
                    accept(selected.design, selected.J, count, gap)
                elif selected.status == "unverified" and selected.design is not None and provisional is None:
                    provisional = deepcopy(selected.design)
                if selected.status == "timeout":
                    raise _Deadline
                if selected.status not in {"verified", "unverified", "no_verified_design"}:
                    diagnostics.append(f"H={count}: route selection {selected.status}: {'; '.join(selected.diagnostics)}")
                elif any("checker" in detail.lower() for detail in selected.diagnostics):
                    diagnostics.extend(f"H={count}: route selection: {detail}" for detail in selected.diagnostics
                                       if "checker" in detail.lower())
                # With no checker, further configurations cannot yield an
                # authoritative incumbent; retain one provisional design.
                if checker is None:
                    continue
                baseline_signature = _design_key(baseline.design)
                for alternative in _configuration_designs(plan, option, config, route_cache,
                                                          effective_deadline, configuration_cap):
                    _check(effective_deadline)
                    if _design_key(alternative) == baseline_signature:
                        continue
                    configs_seen += 1
                    _, score, failure = _invoke_checker(checker, plan, alternative, config)
                    if failure is not None:
                        diagnostics.append(f"H={count}: alternative {failure}")
                    if score is not None:
                        accept(alternative, score, count, gap)
                    _check(effective_deadline)
                    selected = select_whole_design_routes(plan, alternative, config, checker,
                                                          route_cache, effective_deadline)
                    if selected.verified and selected.design is not None and selected.J is not None:
                        accept(selected.design, selected.J, count, gap)
                    if selected.status == "timeout":
                        raise _Deadline
                    if selected.diagnostics:
                        diagnostics.extend(f"H={count}: route selection: {detail}" for detail in selected.diagnostics
                                           if "checker" in detail.lower())
            except _Unsupported as exc:
                diagnostics.append(f"H={count}: unsupported_geometry: {exc}")
            except _CandidateConfiguration as exc:
                candidate_configuration_error = True
                diagnostics.append(f"H={count}: candidate_configuration_error: {exc}")
            except _Deadline:
                timed_out = True
                diagnostics.append(f"H={count}: deadline expired; no infeasibility proof")
                break
        if incumbent is not None and best_key is not None and best_key[0] == count:
            tried[count] = "feasible"
            try:
                _check(effective_deadline)
                improved = _improve_incumbent(plan, incumbent, config, checker,
                                              route_cache, effective_deadline)
                if improved.verified and improved.design is not None and improved.J is not None:
                    accept(improved.design, improved.J, count, best_key[2])
                if improved.status == "timeout":
                    timed_out = True
                elif improved.status == "unsupported_geometry":
                    diagnostics.append(f"H={count}: improvement unsupported_geometry: "
                                       f"{'; '.join(improved.diagnostics)}")
                elif improved.diagnostics:
                    diagnostics.extend(f"H={count}: improvement: {detail}" for detail in improved.diagnostics
                                       if "checker" in detail.lower() or "candidate" in detail.lower())
            except _Deadline:
                timed_out = True
            if timed_out:
                diagnostics.append(f"H={count}: improvement deadline expired; incumbent retained")
            break  # A verified design exists; never raise H after this count.
        if timed_out:
            break
    if best_key is not None:
        best_count, best_j, _, _ = best_key
        proven = trusted_bound and all(tried.get(h) == "proven_infeasible"
                                       for h in range(lower_bound_hubs, best_count))
    else:
        best_j = None
        proven = False
    summary = HubCountSummary(lower_bound_hubs, tried, proven)
    status = "timeout" if timed_out else "verified" if incumbent is not None else \
             "unverified" if provisional is not None else \
             "candidate_configuration_error" if candidate_configuration_error else "no_design_found"
    if checker is None:
        diagnostics.append("Part 2 check_design is unavailable; provisional designs are never incumbents")
    return OuterSearchResult(status, incumbent, provisional, incumbent is not None, best_j,
                             summary, partitions_seen, configs_seen, tuple(diagnostics))
