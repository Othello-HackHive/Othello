"""Temporary visual audit tool for Part 1 geometry validation.

This module deliberately does not write the frontend overlay contract.  It is
for checking the reader's rooms, accepted links, openings, and hub zones before
the solver or official output writer is involved.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Polygon as PolygonPatch

from othello.models import FloorPlan

from .load_plan import build_floor_plan_from_resplan, load_resplan_dataset
from .benchmark import benchmark_issues

ROOM_COLOURS = {
    "living": "#d9d9d9",
    "bedroom": "#66c2a5",
    "kitchen": "#8da0cb",
    "storage": "#fc8d62",
    "bathroom": "#fddbc7",
    "balcony": "#bdbdbd",
    "stair": "#9e9ac8",
    "garden": "#d9f0a3",
    "parking": "#cccccc",
    "pool": "#9ecae1",
    "land": "#efefef",
}


def _polygons(geometry: Any) -> list[Any]:
    if geometry is None or geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if hasattr(geometry, "geoms"):
        return [part for part in geometry.geoms if part.geom_type == "Polygon" and not part.is_empty]
    return []


def _draw_geometry(axis: Any, geometry: Any, *, facecolor: str, edgecolor: str, alpha: float = 1.0, hatch: str | None = None, linewidth: float = 0.8, zorder: int = 1) -> None:
    for polygon in _polygons(geometry):
        axis.add_patch(
            PolygonPatch(
                list(polygon.exterior.coords),
                closed=True,
                facecolor=facecolor,
                edgecolor=edgecolor,
                alpha=alpha,
                hatch=hatch,
                linewidth=linewidth,
                zorder=zorder,
            )
        )


def render_plan_inspector(plan: FloorPlan, output_path: str | Path) -> Path:
    """Render a Part 1-only validation image for a normalised plan."""

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(12, 10))

    _draw_geometry(axis, plan.inner_m, facecolor="#ffffff", edgecolor="#111111", linewidth=1.5, zorder=0)
    for room in plan.rooms.values():
        _draw_geometry(
            axis,
            room.polygon_m,
            facecolor=ROOM_COLOURS.get(room.kind, "#eeeeee"),
            edgecolor="#555555",
            alpha=0.75,
            zorder=1,
        )
        point = room.polygon_m.representative_point()
        axis.text(point.x, point.y, f"{room.room_id}\n{room.kind}", ha="center", va="center", fontsize=7, zorder=8)

    _draw_geometry(axis, plan.walls_m, facecolor="#333333", edgecolor="#333333", alpha=0.85, zorder=3)
    _draw_geometry(axis, plan.windows_m, facecolor="#6baed6", edgecolor="#2171b5", alpha=0.9, zorder=4)
    for room_id, hub_zone in plan.hub_zones_m.items():
        _draw_geometry(axis, hub_zone, facecolor="none", edgecolor="#756bb1", alpha=0.9, hatch="///", linewidth=0.6, zorder=5)

    room_centres = {room_id: room.polygon_m.representative_point() for room_id, room in plan.rooms.items()}
    drawn_links: set[tuple[str, str]] = set()
    for source, neighbours in plan.room_graph.items():
        for target in neighbours:
            link = tuple(sorted((source, target)))
            if link in drawn_links:
                continue
            drawn_links.add(link)
            start, end = room_centres[source], room_centres[target]
            axis.plot([start.x, end.x], [start.y, end.y], color="#006d2c", linewidth=1.2, linestyle="--", zorder=6)

    for opening in plan.openings.values():
        (x1, y1), (x2, y2) = opening.gap_polyline_m
        if opening.kind == "door":
            axis.plot([x1, x2], [y1, y2], color="#dd3497", linewidth=2.5, zorder=7)
        elif opening.kind == "open_passage":
            axis.plot([x1, x2], [y1, y2], color="#31a354", linewidth=3, zorder=7)

    for node in plan.nodes.values():
        x, y = node.point_m
        axis.scatter([x], [y], marker="o", s=22, color="#e6550d", edgecolors="white", linewidths=0.5, zorder=9)
        axis.text(x, y, node.node_id, fontsize=6, ha="left", va="bottom", zorder=10)

    empty_zones = [room_id for room_id, zone in plan.hub_zones_m.items() if zone.is_empty]
    warnings = []
    if empty_zones:
        warnings.append("Empty hub zones: " + ", ".join(empty_zones))
    warnings.extend(repair for repair in plan.repairs if repair.startswith("Repaired") or repair.startswith("Development node"))
    warnings.extend(benchmark_issues(plan))
    if warnings:
        axis.text(0.01, 0.01, "\n".join(warnings), transform=axis.transAxes, ha="left", va="bottom", fontsize=7, color="#b30000", bbox={"facecolor": "white", "alpha": 0.85, "edgecolor": "#b30000"})

    axis.set_title(f"Part 1 geometry audit — plan {plan.plan_id}")
    axis.set_aspect("equal", adjustable="box")
    axis.set_axis_off()
    axis.legend(
        handles=[
            Patch(facecolor="#333333", label="wall"),
            Patch(facecolor="#6baed6", label="window"),
            Patch(facecolor="#dd3497", label="door gap"),
            Patch(facecolor="white", edgecolor="#756bb1", hatch="///", label="hub-eligible zone"),
            Patch(facecolor="#006d2c", label="accepted zone link (not a cable route)"),
            Patch(facecolor="#e6550d", label="development node"),
        ],
        loc="upper left",
        bbox_to_anchor=(1.01, 1),
        frameon=False,
    )
    figure.tight_layout()
    figure.savefig(destination, dpi=180, bbox_inches="tight")
    plt.close(figure)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a temporary Part 1 geometry-inspection PNG")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("plan_id", help="Numeric ResPlan plan id")
    parser.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args()
    records = load_resplan_dataset(arguments.dataset)
    record = next((record for record in records if str(record.get("id")) == arguments.plan_id), None)
    if record is None:
        raise SystemExit(f"Plan {arguments.plan_id} was not found")
    render_plan_inspector(build_floor_plan_from_resplan(record), arguments.out)


if __name__ == "__main__":
    main()
