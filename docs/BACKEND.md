# Backend overview

One page that explains how the pieces fit and how to run them. Details live in the other documents; this is the map.

## The pipeline

```
plan file (ResPlan JSON)
   |  Part 1  reader        othello.reader        scale, rooms, walls, doors, nodes, hub zones, room groups
   v
   |  Part 3  solver        othello.solver        hub spots, visiting orders, rectilinear routes, whole-design search
   v
   |  Part 2  checker       othello.checker       every rule, recomputed from the cable lines only (independent)
   v
   |  Part 2  writers       othello.io            official JSON, overlay JSON, picture, failure note, log
   v
result files  ->  runner (othello.run)  ->  web API (othello.service)  ->  website
```

The one rule that never bends: **an answer file is written only for a design that the independent checker accepts.** An invalid plan scores zero in the challenge, so the solver's own opinion of its design is never enough.

## Where things are

| Folder | What it holds |
|---|---|
| `src/othello/models/` | the shared data shapes (plan, design, results, settings) |
| `src/othello/reader/` and `src/othello/territory/` | Part 1: read a plan, make room groups |
| `src/othello/routing/` and `src/othello/solver/` | Part 3: routes and the search |
| `src/othello/checker/` | Part 2: rules and the score (L, X, O, B, J) |
| `src/othello/io/` | Part 2: writing the files, settings, failure notes |
| `src/othello/run.py` | the runner: one plan or a folder, with progress steps |
| `src/othello/harness.py` | the report card: many plans, statistics, independent re-check |
| `src/othello/service/` | the web API, the job worker, the local server, the AWS store and Lambda entry points |
| `infra/` | the AWS setup (SAM template, container file, deploy role) |
| `tests/` | all tests, sample files for the frontend (`data/samples`, `data/uploads`) |

## Run it on your computer

```bash
python -m othello.run tests/try_runner.py --out out/x                         # a bad file: shows the failure note
python tests/try_runner.py                                                    # a practice house, files in out/try_tiny
python -m othello.harness data/resplan/ResPlan.pkl --count 20 --time-budget 10 --out out/report   # the report card
python -m othello.service.local_server --port 8000 --origin http://localhost:3000                  # the web API
python -m pytest -q                                                           # every test
```

The runner's flags and the settings are in [APP_FLOW.md](APP_FLOW.md) section 6. The web API is in [API.md](API.md).

## What keeps it honest

- **Tests** (about 1,300): rules, metrics, writers, the API, the AWS code against a pretend AWS, and the deployment files. Some tests exist only to stop documents and code drifting apart.
- **Mutation tests:** deliberately broken designs (a cable through a wall, a missing node, a hub too close to a wall...) must each be caught by the matching rule.
- **The report card** re-reads every written answer file and checks it again from scratch, the way an outsider would.
- **CI** runs lint and every test on Python 3.11 and 3.13 for each push and pull request; a second check lints the AWS templates and builds the container image.

## How it is deployed

The web API runs on AWS in `us-east-1`: a Lambda function for web requests, a Lambda worker that solves one plan, a DynamoDB table for job state and a private S3 bucket for result files. Finished jobs delete themselves. The website runs on Vercel and calls the API. Steps and troubleshooting: [DEPLOYMENT.md](DEPLOYMENT.md).

## Limits to know

- A job has a time budget (default 60 s, at most 120 s); the answer is the best verified design found in that time, never an unchecked one.
- The solver is a search, so a very large or unusual plan may end with "no design" inside the budget even if one exists. The failure note says which stage gave up.
- Open questions for the organisers (such as whether a hub may sit exactly on a node) are listed in [ASSUMPTIONS.md](ASSUMPTIONS.md).
