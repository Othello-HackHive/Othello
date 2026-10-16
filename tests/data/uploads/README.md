# Sample upload files

Small made-up plans in the **upload format** (the ResPlan field names; every shape is a GeoJSON
object, or null when there is none). They are what `POST /api/jobs` accepts as its body.
See `docs/API.md`.

| File | What it is |
|---|---|
| `one_room.json` | one 8 m x 8 m room; solved in about a second |
| `three_rooms.json` | a living room, a bedroom and a kitchen joined by two doors |

Rewrite them with `python tests/make_uploads.py`; a test fails if they ever go out of date.
