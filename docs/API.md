# Web API (for the frontend)

The frontend uploads a plan, gets a job id, polls for progress steps, and reads the result files when the job ends. The API never runs code from an upload: it only reads JSON.

Try it on your own machine (no AWS needed):

```bash
python -m othello.service.local_server --port 8000 --origin http://localhost:3000
```

Sample plans to upload are in `tests/data/uploads/` (see its README). Result files have the same formats as before: [OVERLAY_FORMAT.md](OVERLAY_FORMAT.md) for `overlay`, BACKEND_SCHEMA section 8 for `official`, section 11 for `failure`.

## The flow

```
POST /api/jobs  ->  202 {job_id, status: "queued", poll}
GET  /api/jobs/{job_id}/events?after=0   (repeat every ~1 s, pass back "next")  ->  progress steps, status, done
GET  /api/jobs/{job_id}                  ->  status, summary, links to files
GET  /api/jobs/{job_id}/result/overlay   ->  the drawing data for the screen
```

```js
const API = "https://<api-address>";
const start = await fetch(`${API}/api/jobs`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },     // required
  body: JSON.stringify({ plan: planJson, settings: { time_budget_s: 30 } }),
});
const { job_id } = await start.json();
let next = 0, done = false;
while (!done) {
  const page = await (await fetch(`${API}/api/jobs/${job_id}/events?after=${next}`)).json();
  page.events.forEach(showStep);          // {stage, message, elapsed_s, ...}
  next = page.next; done = page.done;
  if (!done) await new Promise(r => setTimeout(r, 1000));
}
const job = await (await fetch(`${API}/api/jobs/${job_id}`)).json();
if (job.status === "succeeded") draw(await (await fetch(API + job.files.overlay)).json());
else showProblem(job.error);              // {code, stage, message, hint}
```

## Routes

| Route | What it does |
|---|---|
| `GET /health` | `{"status": "ok", "version": "1"}`; needs no key |
| `POST /api/jobs` | start a job (answer `202`) |
| `GET /api/jobs/{id}` | status, `result` summary, `error`, `files` (links) |
| `GET /api/jobs/{id}/events?after=N` | progress steps from position `N`; reply has `events`, `next`, `status`, `done` |
| `GET /api/jobs/{id}/result/{kind}` | one file; `kind` is `overlay`, `official`, `baseline`, `failure`, `picture`, `log` or `settings` |
| `POST /api/jobs/{id}/validate` | independently check an edited official-schema design against the job's original plan and settings |
| `POST /api/jobs/{id}/reroute` | reroute one manually moved hub's ring at its fixed location and check the AWS result |
| `POST /api/jobs/{id}/cancel` | stop a queued or running job (`409` if it already ended) |
| `DELETE /api/jobs/{id}` | forget a finished job (`409` if it is still active) |

## The upload

The body is the plan, in the **ResPlan field names**: `id`, `area`, `wall_depth`, `inner`, `wall`, `window`, `door`, `front_door`, and the rooms `living`, `bedroom`, `kitchen`, `storage`, `bathroom`, `balcony`, `stair`, `garden`, `pool`, `parking`, `land`. A Python/Shapely shape cannot go in JSON, so **each shape is written as a GeoJSON object** (`Polygon`, `MultiPolygon`, or `GeometryCollection`) **or a WKT string**; a missing or `null` shape means "none". `inner` and `area` are required; other fields (`graph`, `neighbor`, `net_area`) are ignored.

Settings can travel with it as `{"plan": {...}, "settings": {...}}`. Only these may be set:

| Setting | Allowed |
|---|---|
| `time_budget_s` | a number from 1 to 120 (default 60) |
| `cable_transit_policy` | `forbid`, `avoid_if_possible`, `allow` |
| `weights` | all of `length`, `crossing`, `overlap`, `bend`, each from 0 to 1000 |

An unknown setting or an out-of-range value is an error, never silently ignored. Limits: the body is at most 5 MB, a plan at most 200,000 coordinate numbers and 5,000 shape parts, and at most 10 jobs may be queued or running at once (`429` with `Retry-After`).

## Job status

`queued` -> `running` -> one of `succeeded` (a checked design was written), `no_design` (the solver found nothing valid; see `result.notes`), `failed` (an error such as a bad plan, see `error`), `cancelled`. A job whose worker disappears is reported as `failed` with code `TIMED_OUT` once it is well past its time budget.

## Progress steps

Each step is `{"stage", "message", "elapsed_s", ...}`. Stages begin with `started`, `loaded` (`rooms`, `nodes`), `bound` (`lower_bound`), and `searching` (`hub_counts`, `budget_s`). During search, each checker-verified F0 baseline or new incumbent emits a `candidate` event with `candidate_kind` (`baseline` or `incumbent`), measured `metrics` (`L`, `X`, `O`, `B`, `J`), and `weights` for the four cost terms. Search then emits `checking`, `writing`, and `done` (`status`, `hubs`, `J`, `code`). The `baseline` result file contains the first checker-verified F0 design when one can be exported. Some plans have no verified F0 candidate, so the baseline file and candidate events may be absent.

## Validate an edited design

After a job succeeds, send a candidate in the same JSON schema as its `official` result file:

```js
const checked = await fetch(`${API}/api/jobs/${job_id}/validate`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(editedOfficialDesign),
}).then((response) => response.json());
// { valid, metrics: { L, X, O, B, J, ... }, checks: [...], violations: [...] }
```

The response reports the independent checker result and measured cost for that candidate. It does not replace the job's saved result. A job that has not succeeded returns `409`; malformed candidate JSON returns `400`. Include `x-api-key` when the API uses a key.

## Reroute a moved hub

After a job succeeds, send `{ "hub_id": "H1", "candidate": editedOfficialDesign }` to `POST /api/jobs/{id}/reroute`. The candidate uses the same official JSON schema; set the selected hub's `location` and `room_id` to its new legal point. The API keeps that point and all hub assignments fixed, searches node orders and routes for the selected ring, and runs the independent checker on the result. A successful response is `{ "official": ..., "validation": ... }`, where `validation.metrics.J` is the measured score. The original job result is unchanged. A move outside the hub's assigned room returns `400`; a position with no checker-valid route returns `422`. The request has a bounded search time inside the API Lambda.

## Errors

Every error answer is `{"error": {"code", "message", "hint"}}` with a matching HTTP status: `400` (`BAD_INPUT`, `BAD_SETTINGS`, `BAD_REQUEST`), `401` (`UNAUTHORIZED`), `404` (`NOT_FOUND`), `405` (`METHOD_NOT_ALLOWED`), `409` (`ALREADY_FINISHED`, `STILL_RUNNING`), `413` (`TOO_LARGE`), `415` (`UNSUPPORTED_MEDIA_TYPE`), `422` (`NO_VALID_ROUTE`), `429` (`BUSY`), `500` (`INTERNAL_ERROR`, never with details), `503` (`SERVICE_UNAVAILABLE`: the solver could not be started; nothing was queued, try again).

## Safety and access

- **Content type:** `POST /api/jobs` must send `Content-Type: application/json`, which also makes browsers send a preflight request first.
- **CORS:** only websites in `OTHELLO_API_ORIGINS` (comma-separated; default is localhost) may call the API from a browser. Add the Amplify address there.
- **API key:** when `OTHELLO_API_KEY` is set, every `/api` request must send it in the `x-api-key` header. A key placed in a public web page is visible to everyone, so treat it as a speed bump, not as security.
- **Job ids** are 32 random hex characters; anything else is answered `404` before any storage is touched. Uploaded plan files are never served back.
- **Settings of the service** (environment): `OTHELLO_API_DEFAULT_BUDGET_S`, `OTHELLO_API_MAX_BUDGET_S`, `OTHELLO_API_MAX_UPLOAD_BYTES`, `OTHELLO_API_MAX_ACTIVE_JOBS`, `OTHELLO_API_RETENTION_S` (finished jobs are deleted after this), `OTHELLO_API_ORIGINS`, `OTHELLO_API_KEY`.
