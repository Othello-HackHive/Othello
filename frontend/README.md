# NodeRithm frontend

Interactive floor plan viewer for hub placement and closed-ring cable routing. Upload a plan JSON to see the browser preview, or load its backend output and overlay files to view the computed design and independent checker results. The scrolling explanation uses the current plan's scale, hub lower bound, room groups, ring sequences and score.

## Features
- **Interactive Three.js 3D Twin (`TwinScene.tsx`)**: Real-time 3D isometric and 2D floor plan rendering with camera presets, door cutouts, equipment nodes, cable trays, and animated pulse routing rings.
- **ResPlan & Overlay Parsing (`resplan.ts`)**: Loads official schema JSON and overlay representations with door connections, zone partitions, and violations.
- **Routing & Optimization Engine (`optimizer.ts`)**: Browser preview heuristic and estimated metrics. The backend overlay supplies independent rule checks and score data.
- **BIM Inspector & Audit Deck**: Real-time penalty breakdown, loop continuity verification, and node status indicators.

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
    ├── index.css           # Tailwind CSS styling and theme tokens
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
