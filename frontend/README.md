# NodeRithm frontend

Interactive floor plan viewer for hub placement and closed-ring cable routing. Upload a plan JSON to see the browser preview, or load its backend output and overlay files to view the computed design and independent checker results. The scrolling explanation uses the current plan's scale, hub lower bound, room groups, ring sequences and score.

## Features
- **Interactive Three.js 3D Twin (`TwinScene.tsx`)**: Real-time 3D isometric and 2D floor plan rendering with camera presets, door cutouts, equipment nodes, cable trays, and animated pulse routing rings.
- **ResPlan & Overlay Parsing (`resplan.ts`)**: Loads official schema JSON and overlay representations with door connections, zone partitions, and violations.
- **Routing & Optimization Engine (`optimizer.ts`)**: Browser preview heuristic and estimated metrics. The backend overlay supplies independent rule checks and score data.
- **BIM Inspector & Audit Deck**: Real-time penalty breakdown, loop continuity verification, and node status indicators.
- **Cost history**: The sidebar plots measured candidate scores as they arrive from AWS.
- **Hub editing**: Drag a hub inside its assigned room. For a completed AWS job, the API reroutes that hub's ring at its new location and checks the result; the original result remains available with **Reset hub edit**. Browser examples show a local preview score and checks.
- **Cable density heatmap**: Toggle **Heatmap** in the viewer toolbar to see how many route segments occupy each floor cell. Blue marks lower density and red marks higher density.
- **Viewer controls**: Use **Model View** for an unobstructed canvas and watch progress placeholders while an AWS job runs. Checked hubs show subtle particles.
- **Share Result**: Copies a link that restores the plan, result, edits, and view settings in another browser. The data is stored in the URL fragment, so anyone with the link can read it; very large plans may exceed the link size limit.
- **PDF report**: Select or upload a plan, then use **PDF Report** in the bottom toolbar and choose **Save as PDF** in the browser print dialog. The report contains a vector floor plan and route overlay, hubs, score, and rule checks.

## Project Structure
```
frontend/
├── index.html              # HTML entry point
├── package.json            # Dependencies & scripts
├── tsconfig.json           # TypeScript configuration
├── vite.config.ts          # Vite configuration with @ path alias
└── src/
    ├── main.tsx            # React root mount
    ├── App.tsx             # Main dashboard UI & state controller
    ├── index.css           # Dashboard and viewer styling
    ├── components/
    │   └── TwinScene.tsx   # Three.js 3D Scene canvas & rendering logic
    ├── lib/
    │   ├── optimizer.ts    # Cable routing optimization & solver helpers
    │   └── resplan.ts      # ResPlan models, normalization, presets
    └── utils/
        └── cn.ts           # Class merge utility (clsx + twMerge)
```

## Getting Started

### 1. Install dependencies
```bash
npm install
```

### 2. Start development server
```bash
npm run dev
```

### 3. Production build
```bash
npm run build
```
The build outputs a single-file distributable to `dist/index.html`.

## AWS deployment

The repository's [`amplify.yml`](../amplify.yml) builds `frontend/dist` for AWS Amplify Hosting. After the SAM API is deployed, set `VITE_OTHELLO_API_URL` in Amplify to the stack's `ApiUrl` output (no trailing slash), then redeploy the frontend. Add the Amplify site's exact HTTPS origin to the API stack's `AllowedOrigins` parameter. For local development, copy `.env.example` to `.env.local` and set the same URL. These are Vite build variables, so changing them requires a new frontend build.

When the API URL is set, uploading a ResPlan JSON with GeoJSON or WKT geometry starts an AWS job, shows its progress, and loads the official result and checker overlay. The built-in examples and other browser-format uploads remain local previews. The **Rerun on AWS** button starts another job for the uploaded ResPlan. The API accepts the schema documented in [`docs/API.md`](../docs/API.md); sample uploads are in `tests/data/uploads/`.

If the API stack uses `ApiKey`, `VITE_OTHELLO_API_KEY` sends it as `x-api-key`. A Vite variable is embedded in public browser JavaScript, so this key must not be treated as secret authentication. See [`docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md) for the backend deployment and CORS setup.

## Test AWS hub rerouting locally

Run the API in one PowerShell terminal from the repository root:

```powershell
$env:PYTHONPATH = "src"
python -m othello.service.local_server --port 8000 --origin http://localhost:5173
```

Set `frontend/.env.local` to `VITE_OTHELLO_API_URL=http://localhost:8000`, then start the website in another terminal:

```powershell
cd frontend
npm run dev
```

Open the Vite URL (normally `http://localhost:5173`) and upload `tests/data/uploads/three_rooms.json`. Once the job succeeds, drag a hub to another legal point in its room. The score panel should show **AWS REROUTING**, then **EDIT CHECKED** with the new measured score. Use **More → Reset hub edit** to restore the original result. If AWS cannot find a valid route at that point, the panel keeps the browser preview and shows the API error. The built-in example buttons are browser previews and cannot reroute on AWS. On a deployed site, deploy the updated AWS API image as well as the frontend; an old API returns 404 for `/api/jobs/{id}/reroute`.
