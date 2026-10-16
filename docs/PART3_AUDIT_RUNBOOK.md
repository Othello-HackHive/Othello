# Reproducing the Part 3 audit

Use Python 3.11 or 3.13 with `pip install -e ".[dev]" numpy`. The ResPlan
pickle is local development data and is intentionally excluded from Git and CI.
The adapter generates deterministic temporary node positions because those
records do not contain the competition's fixed nodes.

From the repository root:

```powershell
python audit_full_pipeline.py resplan/ResPlan.pkl --out results/part3_real --count 50 --time-budget 60
python benchmark_part3.py --out results/part3_benchmark --runs 3 --time-budget 60
```

The audit command selects distinct plan IDs by SHA-256 of the ID, includes
6492, and writes `manifest.json`. Reusing the same output directory reuses
that manifest; copy it into another output directory to compare code versions
on exactly the same records. The 60-second limit covers reader, partitioner,
solver, final checker, and writers. Pictures are disabled. Outputs include
`results.json`, `results.csv`, `summary.json`, `report.md`, and individual
official or failure files. Each official design is reconstructed and checked
again from its emitted JSON. The report distinguishes load failures,
unsupported geometry, grid disconnection, timeout, checker rejection, missing
partitions, and successful output. A grid disconnection is never labelled
geometric infeasibility.

The benchmark command runs the slide house, nine-room grid, and L-shaped
house three times each, reporting first verified design, final J, completed
graph builds and build time, and total runtime. It belongs in local
performance testing, not the submission format or timing-sensitive CI.

CI uses the small checked-in reader and passage fixtures plus deterministic
graph-build assertions. Run `python -m pytest -q` on both supported Python
versions. The required lint gate is documented in `.github/workflows/ci.yml`.
