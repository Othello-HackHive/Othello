"""The report card (T08): one call to check a design, one call to score it.

    check_design(plan, design, cfg)  -> ValidationResult  (valid?, violations,
                                        scores, one pass/fail line per rule)
    score_design(plan, design, cfg)  -> Metrics           (L, X, O, B, J, area gap)

Only a design for which ``check_design(...).valid`` is True may ever be written
as the official answer.
"""

from __future__ import annotations

from othello.checker.geometry import distance_to
from othello.checker.metrics import compute_metrics
from othello.checker.rules import RULES, hub_area_m2, run_rules
from othello.models.config import SolverConfig
from othello.models.design import Design
from othello.models.plan import FloorPlan
from othello.models.result import Metrics, RuleCheck, ValidationResult, Violation

# One plain sentence per rule, shown next to its pass/fail mark.
RULE_DETAILS = {
    "schema": "ids and lists are filled in consistently",
    "coverage": "every eligible room and node belongs to exactly one hub",
    "capacity": "each hub has few enough nodes and little enough floor area",
    "zones": "each hub's rooms are joined through doors or open passages",
    "hub_points": "each hub sits in its own room, away from the walls",
    "rings": "each ring is closed, visits every node once and returns to its own hub",
    "nodes": "the cable meets every node exactly where the plan put it",
    "free_space": "straight pieces only, inside the house, never through walls or windows",
    "doors": "the cable changes room only through a door or open passage",
}


def score_design(plan: FloorPlan, design: Design, cfg: SolverConfig | None = None) -> Metrics:
    """L, X, O, B, J and the area gap, recomputed from the cable lines only."""
    config = cfg or SolverConfig()
    areas = {hub.hub_id: hub_area_m2(plan, hub) for hub in design.hubs}
    return compute_metrics(design, config, areas)


def check_design(plan: FloorPlan, design: Design, cfg: SolverConfig | None = None) -> ValidationResult:
    """Run every rule, collect the broken ones and the scores."""
    config = cfg or SolverConfig()
    by_rule = run_rules(plan, design, config)
    violations = [v for found in by_rule.values() for v in found]
    return ValidationResult(
        valid=not violations,
        violations=violations,
        metrics=score_design(plan, design, config),
        checks=_summaries(plan, design, config, by_rule),
    )


def is_valid(plan: FloorPlan, design: Design, cfg: SolverConfig | None = None) -> bool:
    return check_design(plan, design, cfg).valid


# ---------------------------------------------------------- the pass/fail lines


def _summaries(
    plan: FloorPlan,
    design: Design,
    cfg: SolverConfig,
    by_rule: dict[str, list[Violation]],
) -> list[RuleCheck]:
    values = {
        "schema": _schema_value,
        "coverage": _coverage_value,
        "capacity": _capacity_value,
        "zones": _zones_value,
        "hub_points": _hub_points_value,
        "rings": _rings_value,
        "nodes": _nodes_value,
        "free_space": _free_space_value,
        "doors": _doors_value,
    }
    checks: list[RuleCheck] = []
    for rule in RULES:
        found = by_rule[rule.rule_id]
        checks.append(
            RuleCheck(
                rule_id=rule.rule_id,
                label=rule.label,
                passed=not found,
                value=values[rule.rule_id](plan, design, cfg, found),
                detail=RULE_DETAILS[rule.rule_id],
            )
        )
    return checks


def _count(found: list[Violation], noun: str) -> str:
    return f"{len(found)} {noun}"


def _schema_value(plan, design, cfg, found):
    return "OK" if not found else _count(found, "PROBLEMS")


def _coverage_value(plan, design, cfg, found):
    rooms = [r for hub in design.hubs for r in hub.assigned_rooms]
    nodes = [n for hub in design.hubs for n in hub.assigned_nodes]
    rooms_ok = sum(1 for room in plan.eligible_rooms() if rooms.count(room.room_id) == 1)
    nodes_ok = sum(1 for node_id in plan.nodes if nodes.count(node_id) == 1)
    return f"{rooms_ok}/{len(plan.eligible_rooms())} ROOMS · {nodes_ok}/{len(plan.nodes)} NODES"


def _capacity_value(plan, design, cfg, found):
    most_nodes = max((len(h.assigned_nodes) for h in design.hubs), default=0)
    most_area = max((hub_area_m2(plan, h) for h in design.hubs), default=0.0)
    return f"{most_nodes}/{cfg.max_nodes_per_hub} NODES · {most_area:.1f}/{cfg.max_area_per_hub_m2:g} M2"


def _zones_value(plan, design, cfg, found):
    return "ALL CONNECTED" if not found else _count(found, "BROKEN")


def _hub_points_value(plan, design, cfg, found):
    if not design.hubs:
        return "NO HUBS"
    nearest = min(distance_to(h.location, plan.walls_m) for h in design.hubs)
    return f"{nearest:.2f} M FROM WALLS"


def _rings_value(plan, design, cfg, found):
    broken = {v.hub_id for v in found}
    return f"{len(design.hubs) - len(broken)}/{len(design.hubs)} CLOSED"


def _nodes_value(plan, design, cfg, found):
    return "ON POINT" if not found else _count(found, "MOVED")


def _free_space_value(plan, design, cfg, found):
    return "CLEAR" if not found else _count(found, "HITS")


def _doors_value(plan, design, cfg, found):
    return "ONLY VIA DOORS" if not found else _count(found, "BAD CROSSINGS")
