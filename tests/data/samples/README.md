# Sample output files

Real files written by the backend code, for building and testing the frontend. Each folder looks like the output folder of one run. The field-by-field description is in [`docs/OVERLAY_FORMAT.md`](../../../docs/OVERLAY_FORMAT.md).

| Folder | Plan | What it shows | Files |
|---|---|---|---|
| `tiny/` | 2 rooms, 1 hub | the smallest case: start wiring the frontend here | `TINY_PLAN.json`, `TINY_PLAN.overlay.json` |
| `valid/` | 7 rooms, 2 hubs | a good design: no crossings, no overlap, `status` `ok` | `SLIDE5_PLAN.json`, `SLIDE5_PLAN.overlay.json` |
| `costly/` | same house | a legal design that costs more: crossing X marks and overlap stretches in `markers` | `SLIDE5_PLAN.json`, `SLIDE5_PLAN.overlay.json` |
| `invalid/` | same house | a rule-breaking design: `status` `invalid`, red `violations`, failed `checks`; **no official file** | `SLIDE5_PLAN.overlay.json` |
| `no_design/` | same house | nothing found: only the house, `notes`, `bounds_report`, and the failure note | `SLIDE5_PLAN.overlay.json`, `SLIDE5_PLAN.failure.json` |

Notes:

- `*.overlay.json` is the drawing data; `*.json` (without `overlay`) is the official answer file; `*.failure.json` is the "what went wrong" note.
- The same house appears in four folders on purpose: load a different folder to see a different situation.
- Do not edit these files by hand. They are generated: `python tests/make_samples.py`. A test (`tests/test_part2_samples.py`) fails if they differ from what the code produces.
