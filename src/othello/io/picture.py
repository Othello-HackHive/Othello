"""The debug picture (T14): a PNG of a plan, drawn from the overlay data.

Drawing from the overlay dictionary (the same data Ritvik's frontend reads)
keeps one source of truth and proves the overlay has everything needed to draw.

What you see: rooms coloured by hub (excluded rooms grey), walls dark, windows
blue, doors green, cables in the hub's colour with their visiting numbers (the
last number is the return to the hub), nodes (dots), hubs (squares), crossings
(red X), overlaps (thick red), violations (red circles).

matplotlib is needed only for this file; everything else works without it.
"""

from __future__ import annotations

import io
import itertools
from pathlib import Path
from typing import Any

from othello.io.atomic import atomic_write_bytes
from othello.io.errors import OthelloError
from othello.io.overlay import build_overlay

# Same colours as the frontend uses for zones.
HUB_COLORS = ("#1d4ed8", "#d97706", "#10b981", "#7c3aed", "#e11d48", "#0891b2")
RED = "#e60000"  # crossings, overlaps and violations only
EXCLUDED_FILL = "#d1d5db"
UNASSIGNED_FILL = "#f3f4f6"
WALL = "#374151"
WINDOW = "#38bdf8"
DOOR = "#16a34a"
ENTRANCE = "#f97316"


class MissingDependencyError(OthelloError):
    code = "INTERNAL_ERROR"
    stage = "write"
    hint = "install matplotlib to draw pictures: pip install matplotlib"


def hub_color(index: int | None) -> str:
    return HUB_COLORS[index % len(HUB_COLORS)] if index is not None else UNASSIGNED_FILL


def _signed_area(ring: list[list[float]]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in itertools.pairwise(ring)) / 2


def _patch(polygon: list[list[list[float]]], **style: Any):
    """A matplotlib patch for one polygon (outer ring first, then holes)."""
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path as MplPath

    vertices: list[tuple[float, float]] = []
    codes: list[int] = []
    for number, ring in enumerate(polygon):
        points = [tuple(p) for p in ring]
        wanted_ccw = number == 0  # outer ring counter-clockwise, holes clockwise
        if (_signed_area(ring) > 0) != wanted_ccw:
            points.reverse()
        vertices.extend(points)
        codes.extend([MplPath.MOVETO, *([MplPath.LINETO] * (len(points) - 2)), MplPath.CLOSEPOLY])
    return PathPatch(MplPath(vertices, codes), **style)


def _middle_of(polyline: list[list[float]]) -> tuple[float, float]:
    """The point half way along a polyline's length."""
    lengths = [((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in itertools.pairwise(polyline)]
    half, walked = sum(lengths) / 2, 0.0
    for (a, b), length in zip(itertools.pairwise(polyline), lengths, strict=False):
        if walked + length >= half and length > 0:
            t = (half - walked) / length
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        walked += length
    return (polyline[0][0], polyline[0][1])


def draw_overlay(overlay: dict[str, Any], show_bends: bool = False):
    """Draw an overlay dictionary and return the matplotlib figure."""
    try:
        from matplotlib.figure import Figure
        from matplotlib.lines import Line2D
        from matplotlib.patches import Patch
    except ImportError as problem:
        raise MissingDependencyError("matplotlib is not installed (pip install matplotlib)") from problem

    (min_x, min_y), (max_x, max_y) = overlay["bounds"]["min"], overlay["bounds"]["max"]
    width, height = max(max_x - min_x, 1.0), max(max_y - min_y, 1.0)
    figure = Figure(figsize=(10, max(4.5, 7.8 * height / width + 1.9)))
    ax = figure.subplots()
    ax.set_aspect("equal")
    ax.set_xlim(min_x - 0.6, max_x + 0.6)
    ax.set_ylim(min_y - 0.6, max_y + 0.6)
    ax.tick_params(labelsize=7)

    index_of_hub = {hub["hub_id"]: hub["color_index"] for hub in overlay["hubs"]}

    # rooms, coloured by the hub that owns them
    for room in overlay["geometry"]["rooms"]:
        if not room["eligible"]:
            fill, alpha = EXCLUDED_FILL, 0.9
        elif room["hub_id"] in index_of_hub:
            fill, alpha = hub_color(index_of_hub[room["hub_id"]]), 0.18
        else:
            fill, alpha = UNASSIGNED_FILL, 0.9
        for polygon in room["polygons"]:
            ax.add_patch(_patch(polygon, facecolor=fill, edgecolor="none", alpha=alpha, zorder=1))
        outline = room["polygon"]
        if outline:  # name tag in the top-left corner, away from the nodes
            left = min(p[0] for p in outline) + 0.15
            top = max(p[1] for p in outline) - 0.12
            label = f"{room['room_id']} {room['kind']}, {room['area_m2']:.0f} m2"
            ax.text(left, top, label, ha="left", va="top", fontsize=6, color="#4b5563", zorder=2)

    for polygon in overlay["geometry"]["walls"]:
        ax.add_patch(_patch(polygon, facecolor=WALL, edgecolor="none", zorder=3))
    for polygon in overlay["geometry"]["windows"]:
        ax.add_patch(_patch(polygon, facecolor=WINDOW, edgecolor="none", zorder=4))
    for opening in overlay["geometry"]["openings"]:
        xs, ys = [p[0] for p in opening["gap"]], [p[1] for p in opening["gap"]]
        colour = ENTRANCE if opening["kind"] == "entrance" else DOOR
        ax.plot(xs, ys, color=colour, lw=3, solid_capstyle="butt", zorder=4)

    # cables, with the visiting number of every leg (the last one is the return)
    for hub in overlay["hubs"]:
        colour = hub_color(hub["color_index"])
        for leg in hub["legs"]:
            xs, ys = [p[0] for p in leg["polyline"]], [p[1] for p in leg["polyline"]]
            ax.plot(xs, ys, color=colour, lw=2, solid_joinstyle="round", zorder=5)
            mx, my = _middle_of(leg["polyline"])
            ax.text(
                mx,
                my,
                str(leg["order"]),
                fontsize=7,
                ha="center",
                va="center",
                color=colour,
                bbox={"boxstyle": "round,pad=0.15", "fc": "white", "ec": colour, "lw": 0.8},
                zorder=8,
            )

    # costs: overlaps, bends, crossings
    for a, b in overlay["markers"]["overlaps"]:
        ax.plot([a[0], b[0]], [a[1], b[1]], color=RED, lw=7, alpha=0.35, solid_capstyle="butt", zorder=6)
    if show_bends:
        for x, y in overlay["markers"]["bends"]:
            ax.plot([x], [y], "o", color="#6b7280", ms=3, zorder=6)
    for x, y in overlay["markers"]["crossings"]:
        ax.plot([x], [y], marker="X", color=RED, ms=11, mec="white", mew=0.8, zorder=9)

    # nodes and hubs on top
    for node in overlay["nodes"]:
        colour = hub_color(index_of_hub.get(node["hub_id"]))
        ax.plot([node["point"][0]], [node["point"][1]], "o", color=colour, ms=8, mec="black", mew=0.8, zorder=10)
        ax.text(node["point"][0] + 0.15, node["point"][1] + 0.2, node["node_id"], fontsize=7, zorder=11)
    for hub in overlay["hubs"]:
        colour = hub_color(hub["color_index"])
        ax.plot([hub["point"][0]], [hub["point"][1]], "s", color=colour, ms=11, mec="black", mew=1.2, zorder=10)
        ax.text(hub["point"][0] + 0.18, hub["point"][1] - 0.4, hub["hub_id"], fontsize=8, weight="bold", zorder=11)

    # violations, in red
    for violation in overlay["violations"]:
        if violation["point"] is not None:
            ax.plot(
                [violation["point"][0]],
                [violation["point"][1]],
                "o",
                mfc="none",
                mec=RED,
                mew=2.2,
                ms=18,
                zorder=12,
            )

    figure.suptitle(_title(overlay), fontsize=10, color="black" if overlay["valid"] else RED)
    _footer(figure, overlay)
    ax.legend(
        handles=[
            *[Patch(fc=hub_color(h["color_index"]), alpha=0.5, label=f"{h['hub_id']} zone") for h in overlay["hubs"]],
            Line2D([], [], marker="X", color="none", mfc=RED, mec="white", ms=9, label="crossing"),
            Line2D([], [], color=RED, lw=6, alpha=0.35, label="overlap"),
            Line2D([], [], marker="o", color="none", mfc="none", mec=RED, mew=2, ms=10, label="violation"),
            Line2D([], [], color=DOOR, lw=3, label="door / opening"),
        ],
        loc="upper left",
        bbox_to_anchor=(1.01, 1.0),
        fontsize=7,
        frameon=False,
    )
    figure.subplots_adjust(left=0.05, right=0.82, top=0.92, bottom=0.12)
    return figure


def _title(overlay: dict[str, Any]) -> str:
    metrics = overlay["metrics"]
    state = {"ok": "VALID", "invalid": "INVALID", "no_design_found": "NO DESIGN FOUND"}[overlay["status"]]
    if not metrics or overlay["status"] == "no_design_found":
        return f"{overlay['plan_id']}  -  {state}"
    return (
        f"{overlay['plan_id']}  -  {state}  -  {metrics['hub_count']} hubs  -  J = {metrics['J']:.2f}"
        f"  (L {metrics['L']:.1f}, X {metrics['X']}, O {metrics['O']:.1f}, B {metrics['B']})"
    )


def _footer(figure, overlay: dict[str, Any]) -> None:
    report = overlay["bounds_report"]
    line = f"minimum hubs by area/nodes: {report['lower_bound_hubs']}"
    if report["proven_minimum"]:
        line += "  (proven)"
    failed = [c for c in overlay["checks"] if not c["passed"]]
    if overlay["checks"] and not failed:
        line += f"   |   all {len(overlay['checks'])} rules pass"
    elif failed:
        line += "   |   FAILED: " + ", ".join(c["label"] for c in failed)
    elif overlay["notes"]:
        line += "   |   " + "; ".join(overlay["notes"])
    figure.text(0.05, 0.04, line, fontsize=8, color=RED if failed else "#374151")


def picture_bytes(overlay: dict[str, Any], dpi: int = 130, show_bends: bool = False) -> bytes:
    figure = draw_overlay(overlay, show_bends=show_bends)
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=dpi)
    return buffer.getvalue()


def write_picture(overlay: dict[str, Any], path: str | Path, dpi: int = 130, show_bends: bool = False) -> Path:
    """Draw an overlay dictionary into a PNG file (written safely in one go)."""
    return atomic_write_bytes(path, picture_bytes(overlay, dpi=dpi, show_bends=show_bends))


def write_plan_picture(plan, design, validation, path: str | Path, **options: Any) -> Path:
    """Convenience: build the overlay for a plan/design/report card and draw it."""
    draw_options = {k: options.pop(k) for k in ("dpi", "show_bends") if k in options}
    return write_picture(build_overlay(plan, design, validation, **options), path, **draw_options)
