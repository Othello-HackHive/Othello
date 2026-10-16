"""Make the sample upload files for the frontend (and for trying the API by hand).

    python tests/make_uploads.py            # rewrites tests/data/uploads/

Each file is a plan in the upload format (ResPlan field names, shapes as GeoJSON).  They are small,
made-up houses, so the files are deterministic: running this twice gives the same bytes.  A test
(tests/test_part2_uploads.py) regenerates them and fails if the committed files differ.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "src")]

from fixtures.uploads import geojson
from shapely.geometry import MultiPolygon, box
from shapely.ops import unary_union

OUT = HERE / "data" / "uploads"


def one_room() -> dict:
    """8 m x 8 m room, one node: solved in about a second."""
    return {
        "id": "SAMPLE_ONE_ROOM",
        "area": 64.0,
        "wall_depth": 2.0,
        "inner": geojson(box(0, 0, 80, 80)),
        "wall": geojson(
            MultiPolygon([box(-2, -2, 82, 82).difference(box(0, 0, 80, 80))])
        ),
        "window": None,
        "door": None,
        "living": geojson(box(0, 0, 80, 80)),
    }


def three_rooms() -> dict:
    """A living room and two bedrooms joined by two doors (10 m x 10 m)."""
    walls = [box(48, 0, 52, 100), box(50, 48, 100, 52)]
    doors = [box(48, 20, 52, 30), box(48, 70, 52, 80)]
    shell = box(-2, -2, 102, 102).difference(box(0, 0, 100, 100))
    wall = unary_union([shell, *walls]).difference(unary_union(doors))
    return {
        "id": "SAMPLE_THREE_ROOMS",
        "area": 100.0,
        "wall_depth": 4.0,
        "inner": geojson(box(0, 0, 100, 100)),
        "wall": geojson(wall),
        "window": None,
        "door": geojson(MultiPolygon(doors)),
        "front_door": None,
        "living": geojson(box(0, 0, 50, 100)),
        "bedroom": geojson(MultiPolygon([box(50, 0, 100, 50)])),
        "kitchen": geojson(box(50, 50, 100, 100)),
    }


README = """# Sample upload files

Small made-up plans in the **upload format** (the ResPlan field names; every shape is a GeoJSON
object, or null when there is none). They are what `POST /api/jobs` accepts as its body.
See `docs/API.md`.

| File | What it is |
|---|---|
| `one_room.json` | one 8 m x 8 m room; solved in about a second |
| `three_rooms.json` | a living room, a bedroom and a kitchen joined by two doors |

Rewrite them with `python tests/make_uploads.py`; a test fails if they ever go out of date.
"""


def files() -> dict[str, str]:
    dump = lambda data: json.dumps(data, indent=2) + "\n"
    return {
        "one_room.json": dump(one_room()),
        "three_rooms.json": dump(three_rooms()),
        "README.md": README,
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in files().items():
        (OUT / name).write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {len(files())} files to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
