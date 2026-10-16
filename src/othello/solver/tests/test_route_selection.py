"""Bounded combination selection with an injected independent toy checker."""

from pathlib import Path
import sys
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest
from shapely.geometry import LineString

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from othello.routing import RoutingCache
from othello.routing.types import RouteLibraryResult, RouteVariant
from othello.solver import construct_f0_design, select_whole_design_routes
from othello.solver.tests.test_f0 import config as f0_config, excluded_detour_plan, single_door_plan


def config(*, exact=20000, beam=20):
    return f0_config(
        weights=NS(length=1.0, bend=0.0, crossing=20.0, overlap=1.0),
        limits=NS(hub_candidates=12, orders_kept=12, routes_per_leg=2,
                  exact_combo_limit=exact, beam_width=beam),
    )


def _variant(points, tag):
    line = LineString(points)
    return RouteVariant(tuple(points), line.length, line.length, 0, 0, (), None, None, (tag,))


def _two_ring_case(monkeypatch, *, reverse=False):
    from othello.solver import route_selection
    h1, n1, h2, n2 = (0.0, 0.0), (4.0, 0.0), (2.0, -2.0), (2.0, 2.0)
    plan = NS(plan_id="two-rings", nodes={"N1": NS(point_m=n1), "N2": NS(point_m=n2)})
    design = {
        "plan_id": plan.plan_id,
        "hubs": [
            {"hub_id": "H1", "room_id": "R1", "location": h1, "assigned_rooms": ["R1"],
             "assigned_nodes": ["N1"], "ring": {"hub_id": "H1", "sequence": ["H1", "N1", "H1"],
                "legs": [{"from_id": "H1", "to_id": "N1", "polyline": [h1, n1], "doors_used": []},
                         {"from_id": "N1", "to_id": "H1", "polyline": [n1, h1], "doors_used": []}]}},
            {"hub_id": "H2", "room_id": "R2", "location": h2, "assigned_rooms": ["R2"],
             "assigned_nodes": ["N2"], "ring": {"hub_id": "H2", "sequence": ["H2", "N2", "H2"],
                "legs": [{"from_id": "H2", "to_id": "N2", "polyline": [h2, n2], "doors_used": []},
                         {"from_id": "N2", "to_id": "H2", "polyline": [n2, h2], "doors_used": []}]}}
        ], "solver_notes": {"verification": "not_run"},
    }
    options = {
        ("H1", "N1"): (_variant([h1, n1], "baseline"),
                         _variant([h1, (0, 3), (4, 3), n1], "above")),
        ("N1", "H1"): (_variant([n1, h1], "baseline"),
                         _variant([n1, (4, -3), (0, -3), h1], "below")),
        ("H2", "N2"): (_variant([h2, n2], "baseline"),),
        ("N2", "H2"): (_variant([n2, h2], "baseline"),),
    }
    if reverse:
        options = {key: tuple(reversed(routes)) for key, routes in options.items()}
    calls = []

    def library(table, source, target, cfg, deadline=None):
        calls.append((source, target))
        return RouteLibraryResult("ready", options[source, target])

    monkeypatch.setattr(route_selection, "build_route_library", library)
    return plan, design, calls


def _toy_checker(plan, design, cfg):
    """Test oracle only: inspect all legs, including cross-ring intersections."""
    lines = []
    for hub in design["hubs"]:
        seq = hub["ring"]["sequence"]
        assert seq[0] == seq[-1] == hub["hub_id"]
        assert sorted(seq[1:-1]) == sorted(hub["assigned_nodes"])
        for leg in hub["ring"]["legs"]:
            assert leg["polyline"][0] != leg["polyline"][-1]
            lines.append(LineString(leg["polyline"]))
    points = set()
    overlap = 0.0
    for i, left in enumerate(lines):
        for right in lines[i + 1:]:
            intersection = left.intersection(right)
            overlap += intersection.length
            if intersection.geom_type == "Point":
                point = (intersection.x, intersection.y)
                stops = {tuple(left.coords[0]), tuple(left.coords[-1]),
                         tuple(right.coords[0]), tuple(right.coords[-1])}
                if point not in stops:
                    points.add(point)
    j = sum(line.length for line in lines) + cfg.weights.crossing * len(points) + cfg.weights.overlap * overlap
    return NS(valid=True, metrics=NS(J=j))


@pytest.mark.parametrize("exact,beam,mode", [(20000, 20, "exact"), (1, 4, "beam")])
def test_complete_checker_selects_interacting_routes_across_rings(monkeypatch, exact, beam, mode):
    plan, base, calls = _two_ring_case(monkeypatch)
    result = select_whole_design_routes(plan, base, config(exact=exact, beam=beam), checker=_toy_checker)
    assert result.status == "verified"
    assert result.verified and result.search_mode == mode and result.combinations == 4
    assert result.J == pytest.approx(_toy_checker(plan, result.design, config()).metrics.J)
    assert result.J < _toy_checker(plan, base, config()).metrics.J
    assert result.checked_designs == 5  # baseline, then every library combination
    first_ring = result.design["hubs"][0]["ring"]
    assert first_ring["legs"][0]["polyline"][1] == (0, 3)
    assert first_ring["legs"][1]["polyline"][1] == (4, -3)
    assert calls == [("H1", "N1"), ("N1", "H1"), ("H2", "N2"), ("N2", "H2")]
    assert base["hubs"][0]["ring"]["legs"][0]["polyline"] == [(0, 0), (4, 0)]


def test_absent_checker_uses_interaction_proxy_but_never_claims_J(monkeypatch):
    from othello.solver import route_selection
    plan, base, _ = _two_ring_case(monkeypatch)
    monkeypatch.setattr(route_selection, "_optional_checker", lambda: None)
    result = select_whole_design_routes(plan, base, config())
    assert result.status == "unverified" and not result.verified and result.J is None
    assert result.combinations == 4 and result.checked_designs == 0
    assert result.design["hubs"][0]["ring"]["legs"][0]["polyline"][1] == (0, 3)
    assert result.design["hubs"][0]["ring"]["legs"][1]["polyline"][1] == (4, -3)
    assert base["hubs"][0]["ring"]["legs"][0]["polyline"] == [(0, 0), (4, 0)]


@pytest.mark.parametrize("exact,beam", [(20000, 20), (1, 4)])
def test_equal_score_ties_do_not_depend_on_variant_insertion(monkeypatch, exact, beam):
    plan, base, _ = _two_ring_case(monkeypatch)
    equal = lambda *_: NS(valid=True, metrics=NS(J=1.0))
    first = select_whole_design_routes(plan, base, config(exact=exact, beam=beam), checker=equal)
    plan, base, _ = _two_ring_case(monkeypatch, reverse=True)
    second = select_whole_design_routes(plan, base, config(exact=exact, beam=beam), checker=equal)
    assert first.design == second.design and first.J == second.J == 1.0


def test_invalid_checker_results_never_become_incumbent(monkeypatch):
    plan, base, _ = _two_ring_case(monkeypatch)
    invalid = lambda *_: NS(valid=False, metrics=None)
    result = select_whole_design_routes(plan, base, config(), checker=invalid)
    assert result.status == "no_verified_design"
    assert result.design is None and result.J is None and not result.verified


@pytest.mark.parametrize("failure", ["timeout", "unsupported_geometry", "unreachable_on_grid"])
def test_library_failure_keeps_only_checked_F0_and_propagates_status(monkeypatch, failure):
    from othello.solver import route_selection
    plan, base, _ = _two_ring_case(monkeypatch)
    monkeypatch.setattr(route_selection, "build_route_library", lambda *args, **kwargs:
                        RouteLibraryResult(failure, diagnostics=("test failure",)))
    result = select_whole_design_routes(plan, base, config(), checker=_toy_checker)
    assert result.status == failure and result.verified and result.design == base
    assert result.J == _toy_checker(plan, base, config()).metrics.J
    assert result.diagnostics == ("test failure",)


def test_timeout_retains_only_previously_verified_incumbent(monkeypatch):
    from othello.solver import route_selection
    plan, base, _ = _two_ring_case(monkeypatch)
    calls = 0
    def checker(*args):
        nonlocal calls
        calls += 1
        return _toy_checker(*args)
    monkeypatch.setattr(route_selection, "_check_deadline", lambda deadline: (
        (_ for _ in ()).throw(route_selection._TimedOut()) if calls >= 2 else None
    ))
    result = select_whole_design_routes(plan, base, config(), checker=checker, deadline=10**20)
    assert result.status == "timeout" and result.verified
    assert result.design == base and result.J == _toy_checker(plan, base, config()).metrics.J


def test_selected_variant_keeps_ordered_repeated_opening_ids(monkeypatch):
    from othello.solver import route_selection
    plan, base, _ = _two_ring_case(monkeypatch)
    original = route_selection.build_route_library

    def with_metadata(table, source, target, cfg, deadline=None):
        found = original(table, source, target, cfg, deadline)
        if (source, target) == ("H1", "N1"):
            route = replace(found.routes[0], opening_ids=("D2", "D1", "D2"))
            return RouteLibraryResult("ready", (route,))
        return found

    monkeypatch.setattr(route_selection, "build_route_library", with_metadata)
    monkeypatch.setattr(route_selection, "_optional_checker", lambda: None)
    result = select_whole_design_routes(plan, base, config())
    assert result.status == "unverified"
    assert result.design["hubs"][0]["ring"]["legs"][0]["doors_used"] == ["D2", "D1", "D2"]
    assert result.design["hubs"][0]["ring"]["legs"][0]["polyline"][0] == (0, 0)
    assert result.design["hubs"][0]["ring"]["legs"][0]["polyline"][-1] == (4, 0)


def test_real_route_library_keeps_endpoints_openings_and_unverified_status(monkeypatch):
    from othello.solver import route_selection
    plan, option = single_door_plan()
    cfg = config()
    built = construct_f0_design(plan, option, cfg)
    assert built.status == "constructed_unverified"
    monkeypatch.setattr(route_selection, "_optional_checker", lambda: None)
    result = select_whole_design_routes(plan, built.design, cfg, cache=RoutingCache())
    assert result.status == "unverified" and result.design == built.design
    assert result.J is None
    assert [leg["doors_used"] for leg in result.design["hubs"][0]["ring"]["legs"]] == [[], ["D1"], ["D1"]]


@pytest.mark.parametrize("policy", ["forbid", "avoid_if_possible"])
def test_real_selected_variants_obey_excluded_room_policy(policy):
    plan, option = excluded_detour_plan()
    cfg = config(exact=20, beam=5)
    cfg.cable_transit_policy = policy
    built = construct_f0_design(plan, option, cfg)
    assert built.status == "constructed_unverified", built.diagnostics
    length_checker = lambda p, d, c: NS(valid=True, metrics=NS(J=sum(
        LineString(leg["polyline"]).length for hub in d["hubs"] for leg in hub["ring"]["legs"]
    )))
    result = select_whole_design_routes(plan, built.design, cfg, checker=length_checker)
    assert result.status == "verified", result.diagnostics
    excluded = plan.rooms["X"].polygon_m
    for hub in result.design["hubs"]:
        for leg in hub["ring"]["legs"]:
            assert not set(leg["doors_used"]) & {"D1", "D2"}
            for a, b in zip(leg["polyline"], leg["polyline"][1:]):
                if a != b:
                    assert LineString([a, b]).intersection(excluded).length == 0
