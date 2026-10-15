# Othello - 3D Cable Routing Digital Twin (Frontend)

Interactive 3D digital twin and BIM visualizer for Honeywell ResPlan joint hub placement and closed-ring cable routing challenge.

## Features
- **Interactive Three.js 3D Twin (`TwinScene.tsx`)**: Real-time 3D isometric and 2D floor plan rendering with camera presets, door cutouts, equipment nodes, cable trays, and animated pulse routing rings.
- **ResPlan & Overlay Parsing (`resplan.ts`)**: Loads official schema JSON and overlay representations with door connections, zone partitions, and violations.
- **Routing & Optimization Engine (`optimizer.ts`)**: Client-side solver support and metrics auditing.
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
