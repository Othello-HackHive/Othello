# Othello: Cable Ring Challenge, docs index

Team Othello's v1 specification. The official 8-slide PPT is the final authority; where a doc and the PPT differ, the PPT wins.

| Read in this order | What it gives you |
|---|---|
| [PRD.md](PRD.md) | What we are building, the rules, ranking, scope, success criteria |
| [TRD.md](TRD.md) | Algorithm per job, geometry rules, metric maths, checker basis, limits, robustness |
| [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) | Data models, the two hand-off contracts, official JSON, overlay JSON |
| [APP_FLOW.md](APP_FLOW.md) | Step-by-step run, time budget, failure and timeout paths, the command-line runner |
| [OVERLAY_FORMAT.md](OVERLAY_FORMAT.md) | The overlay file field by field (for the frontend) |
| [API.md](API.md) | The web API: upload a plan, follow progress, fetch results (for the frontend) |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Putting the API on AWS: one-time setup, deploying, logs, taking it down |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | Three backend parts, tasks, milestones, test gates, git workflow |
| [ASSUMPTIONS.md](ASSUMPTIONS.md) | Confirmed / Assumed / Pending register and the organiser question list |

Status labels: **[CONFIRMED]** (in the PPT), **[ASSUMED]** (our switchable default), **[PENDING]** (needs the organiser JSON, checker or an answer).

One sentence summary: read one floor plan, choose the fewest hubs, give each hub one closed axis-aligned cable ring through doors only, check the answer with an independent checker, and write the official JSON plus overlay data, always preferring a verified valid design over a risky better one.
