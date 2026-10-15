"""Dataset statistics for the Part 1 development corpus.

Usage: ``python -m othello.reader.stats path/to/ResPlan.pkl``.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from .load_plan import ELIGIBLE_KINDS, load_resplan_dataset


def _component_count(geometry: Any) -> int:
    if geometry is None or geometry.is_empty:
        return 0
    return len(geometry.geoms) if hasattr(geometry, "geoms") else 1


def summarise_resplan(path: str | Path) -> dict[str, Any]:
    records = load_resplan_dataset(path)
    categories = Counter()
    eligible_counts: list[int] = []
    scales: list[float] = []
    edge_types = Counter()
    for record in records:
        for key, value in record.items():
            if hasattr(value, "is_empty") and not value.is_empty:
                categories[key] += 1
        eligible_counts.append(sum(_component_count(record.get(kind)) for kind in ELIGIBLE_KINDS))
        scales.append(math.sqrt(record["inner"].area / float(record["area"])))
        edge_types.update(edge.get("type") for _, _, edge in record["graph"].edges(data=True))
    ordered_scales = sorted(scales)
    percentile = lambda fraction: ordered_scales[round((len(ordered_scales) - 1) * fraction)]
    return {
        "plan_count": len(records),
        "eligible_room_count": {
            "min": min(eligible_counts),
            "median": ordered_scales and sorted(eligible_counts)[len(eligible_counts) // 2],
            "max": max(eligible_counts),
            "over_ten": sum(count > 10 for count in eligible_counts),
        },
        "scale_units_per_metre": {
            "min": round(min(scales), 4),
            "p05": round(percentile(0.05), 4),
            "median": round(percentile(0.5), 4),
            "p95": round(percentile(0.95), 4),
            "max": round(max(scales), 4),
        },
        "net_area_zero": sum(float(record.get("net_area", 1)) == 0 for record in records),
        "non_empty_categories": dict(sorted(categories.items())),
        "graph_edge_types": dict(sorted(edge_types.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarise a ResPlan pickle for Part 1 planning")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--out", type=Path, help="Optional JSON report path")
    arguments = parser.parse_args()
    report = summarise_resplan(arguments.dataset)
    report_json = json.dumps(report, indent=2, sort_keys=True)
    if arguments.out:
        arguments.out.write_text(report_json + "\n", encoding="utf-8")
    print(report_json)


if __name__ == "__main__":
    main()
