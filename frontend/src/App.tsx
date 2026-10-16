import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDownToLine,
  Axis3d,
  Braces,
  Check,
  ChevronDown,
  Copy,
  DoorOpen,
  FileJson2,
  Grid3x3,
  Layers3,
  LoaderCircle,
  Maximize2,
  Minus,
  MousePointer2,
  Play,
  Route,
  ScanLine,
  ShieldCheck,
  SlidersHorizontal,
  SquareStack,
  Target,
  TriangleAlert,
  Upload,
  X,
} from "lucide-react";
import TwinScene, { type CameraPresetId, type Inspection } from "./components/TwinScene";
import { normalizePlan, PRESET_IDS, PRESET_JSON, type PlanModel } from "./lib/resplan";
import { optimize, type Solution } from "./lib/optimizer";
import { parseOverlay, solutionFromOfficial, type OverlayDraw } from "./lib/overlay";

const phases = ["IMPORT", "ZONES", "ROUTE", "AUDIT"];

const presetButtons: Array<{ id: CameraPresetId; label: string; icon: typeof Grid3x3 }> = [
  { id: "top", label: "TOP 2D", icon: Grid3x3 },
  { id: "iso", label: "ISOMETRIC BIM", icon: Maximize2 },
  { id: "zone1", label: "ZONE 1 MACRO", icon: Target },
  { id: "zone2", label: "ZONE 2 MACRO", icon: Axis3d },
];

const presetCards: Record<string, { title: string; note: string }> = {
  BENCHMARK_6R: { title: "SLIDE 5 / 6-ROOM BENCHMARK", note: "2 hubs · single-door dead ends" },
  RESPLAN_8R: { title: "RESPLAN 8-ROOM", note: "area capacity bound → 2 hubs" },
  COMPLEX_12R: { title: "COMPLEX 12-ROOM", note: "L-shaped living · 3 hubs" },
};

const solverStages = [
  "scale normalisation  ·  m = units · sqrt(inner.area / area)",
  "min-hub contiguous districting  ·  ≤5 nodes · ≤100 m²",
  "axis-aligned door-constrained pathfinding  ·  BFS over door graph",
  "metric TSP ring sequencing  ·  NN seed + 2-opt refinement",
  "penalty audit  ·  J = L + 5X + O + 0.5B",
];

type RunState = "ready" | "solving" | "verified";

export default function App() {
  const [planKey, setPlanKey] = useState<string>("BENCHMARK_6R");
  const [rawPlan, setRawPlan] = useState<Record<string, unknown>>(() => PRESET_JSON.BENCHMARK_6R);
  const [planLabel, setPlanLabel] = useState("RESPLAN PRESET");
  const [planError, setPlanError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [showZones, setShowZones] = useState(true);
  const [showRoutes, setShowRoutes] = useState(true);
  const [wallSlice, setWallSlice] = useState(2.2);
  const [phase, setPhase] = useState(0);
  const [stage, setStage] = useState(-1);
  const [selected, setSelected] = useState<Inspection | null>(null);
  const [runState, setRunState] = useState<RunState>("ready");
  const [optimizeSignal, setOptimizeSignal] = useState(0);
  const [preset, setPreset] = useState<{ id: CameraPresetId; token: number }>({ id: "iso", token: 0 });
  const [activePreset, setActivePreset] = useState<CameraPresetId | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [official, setOfficial] = useState<{ text: string; data: Record<string, unknown>; name: string } | null>(null);
  const [overlayDraw, setOverlayDraw] = useState<OverlayDraw | null>(null);
  const [resultDismissed, setResultDismissed] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const resultInput = useRef<HTMLInputElement>(null);
  const overlayInput = useRef<HTMLInputElement>(null);

  const parsed = useMemo<{ plan: PlanModel | null; error: string | null }>(() => {
    try {
      const result = normalizePlan(rawPlan, planKey);
      setWarnings(result.warnings);
      return { plan: result.plan, error: null };
    } catch (error) {
      return { plan: null, error: error instanceof Error ? error.message : "Unreadable plan JSON" };
    }
  }, [rawPlan, planKey]);

  const plan = parsed.plan;
  useEffect(() => {
    setPlanError(parsed.error);
  }, [parsed.error]);

  const solution = useMemo<Solution | null>(() => (plan ? optimize(plan) : null), [plan]);

  /* backend v1: <plan_id>.json (official) + <plan_id>.overlay.json (drawing data) */
  const resultSolution = useMemo<Solution | null>(() => {
    if (!plan || !official || resultDismissed) return null;
    try {
      return solutionFromOfficial(official.data, official.text, plan, overlayDraw);
    } catch (error) {
      setPlanError(error instanceof Error ? `Result rejected — ${error.message}` : "Result rejected");
      return null;
    }
  }, [plan, official, overlayDraw, resultDismissed]);

  const activeSolution = resultSolution ?? solution;
  const isBackend = resultSolution !== null;

  const jsonText = useMemo(
    () => (activeSolution ? (activeSolution.rawText ?? JSON.stringify(activeSolution.output, null, 2)) : "{}"),
    [activeSolution],
  );

  useEffect(() => {
    if (runState !== "solving") return;
    const timers = solverStages.map((_, index) => window.setTimeout(() => setStage(index), 320 + index * 330));
    const done = window.setTimeout(() => {
      setStage(solverStages.length - 1);
      setRunState("verified");
      setOptimizeSignal((value) => value + 1);
    }, 320 + solverStages.length * 330);
    return () => {
      timers.forEach(clearTimeout);
      clearTimeout(done);
    };
  }, [runState]);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1600);
    return () => clearTimeout(timer);
  }, [copied]);

  const loadPlan = useCallback((key: string, json: Record<string, unknown>, label: string) => {
    setPlanKey(key);
    setRawPlan(json);
    setPlanLabel(label);
    setSelected(null);
    setStage(-1);
    setRunState("solving");
  }, []);

  const runSolver = () => {
    setSelected(null);
    setStage(-1);
    setResultDismissed(true);
    setRunState("solving");
    setShowZones(true);
    setShowRoutes(true);
  };

  const requirePlan = (): PlanModel | null => {
    if (plan) return plan;
    setPlanError("Load a floor plan (preset or upload) before loading a backend result");
    return null;
  };

  const loadOfficial = async (file: File) => {
    try {
      const text = await file.text();
      const data = JSON.parse(text) as Record<string, unknown>;
      if (!requirePlan()) return;
      if (!Array.isArray(data.hubs)) throw new Error("missing hubs[] — expected the official output file");
      setOfficial({ text, data, name: file.name });
      setResultDismissed(false);
      setShowRoutes(true);
      setPlanError(null);
    } catch (error) {
      setPlanError(error instanceof Error ? `Result file invalid — ${error.message}` : "Result file invalid");
    }
  };

  const loadOverlayFile = async (file: File) => {
    try {
      const data = JSON.parse(await file.text()) as Record<string, unknown>;
      const target = requirePlan();
      if (!target) return;
      const parsed = parseOverlay(data, target);
      setOverlayDraw(parsed);
      setWarnings((current) => [...current, ...parsed.warnings]);
      if (official) setResultDismissed(false);
      setPlanError(null);
    } catch (error) {
      setPlanError(error instanceof Error ? `Overlay file invalid — ${error.message}` : "Overlay file invalid");
    }
  };

  const clearResult = () => {
    setOfficial(null);
    setOverlayDraw(null);
    setResultDismissed(true);
  };

  const ingestFile = async (file: File) => {
    try {
      const json = JSON.parse(await file.text());
      loadPlan(file.name.replace(/\.json$/i, "").toUpperCase().slice(0, 16), json, file.name);
    } catch (error) {
      setPlanError(error instanceof Error ? `Invalid JSON — ${error.message}` : "Invalid JSON file");
    }
  };

  const download = () => {
    if (!activeSolution) return;
    const blob = new Blob([jsonText], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = isBackend ? (official?.name ?? "result.json") : `${activeSolution.plan_id.toLowerCase()}-solution.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const copyJson = async () => {
    try {
      await navigator.clipboard.writeText(jsonText);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  const metrics = activeSolution?.metrics;
  const ruleChecks = activeSolution?.checks ?? [];
  const knownChecks = ruleChecks.filter((check) => !check.na);
  const feasible = knownChecks.length > 0 && knownChecks.every((check) => check.pass);
  const hasFailures = knownChecks.some((check) => !check.pass);
  const badgeLabel =
    runState === "solving"
      ? "RUNNING"
      : isBackend
        ? hasFailures
          ? "VIOLATIONS"
          : feasible
            ? "FEASIBLE"
            : "RECEIVED"
        : feasible
          ? "FEASIBLE"
          : "IDLE";

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <span className="brand-mark"><span /></span>
          <span className="brand-name">HONEYWELL</span>
          <span className="brand-divider" />
          <span className="product-name">RESPLAN · HUB &amp; RING OPTIMIZER</span>
        </div>
        <div className="topbar-center" aria-label="Workflow phase">
          {phases.map((label, index) => (
            <span key={label} className={index === (runState === "solving" ? 1 : phase) ? "phase active" : "phase"}>
              <i>{String(index + 1).padStart(2, "0")}</i>{label}
            </span>
          ))}
        </div>
        <div className="topbar-actions">
          <input
            ref={fileInput}
            type="file"
            accept="application/json,.json"
            className="hidden-input"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void ingestFile(file);
              event.target.value = "";
            }}
          />
          <button type="button" className="ghost-action" onClick={() => fileInput.current?.click()}>
            <Upload size={14} /> UPLOAD RESPLAN JSON
          </button>
          <div className="system-state"><span className="led" /> {feasible ? "FEASIBLE" : "SOLVER ONLINE"}</div>
        </div>
      </header>

      <main id="story" className="story">
        <div className="scene-sticky">
          {plan && activeSolution ? (
            <TwinScene
              plan={plan}
              solution={activeSolution}
              overlay={isBackend ? overlayDraw : null}
              showZones={showZones}
              showRoutes={showRoutes}
              wallHeight={wallSlice / 2.2}
              optimizeSignal={optimizeSignal}
              preset={preset}
              focusHub={selected?.hub ?? null}
              onInspect={setSelected}
              onPhaseChange={setPhase}
            />
          ) : (
            <div className="scene-fallback">
              <TriangleAlert size={26} />
              <span>{planError ?? "No valid plan loaded"}</span>
            </div>
          )}
          <div className="scene-vignette" />
          <div className="cad-frame" aria-hidden="true">
            <span className="corner corner-tl" />
            <span className="corner corner-tr" />
            <span className="corner corner-bl" />
            <span className="corner corner-br" />
          </div>
          <div className="view-readout" aria-hidden="true">
            <span>SCALE {plan ? plan.unitsPerM.toFixed(2) : "—"} UNITS/M</span>
            <span>SECTION CUT {wallSlice.toFixed(2)} M</span>
          </div>
        </div>

        <section className="story-panel intro-panel">
          <div className="intro-copy">
            <p className="overline"><span>UR HACKATHON 2026H2</span> DIGITAL TWIN · CLOSED-RING HAMILTONIAN NETWORK</p>
            <h1><span>HONEYWELL</span>RESPLAN</h1>
            <p className="hero-line">Any floor plan in. One optimal ring per hub out.</p>
            <p className="hero-support">
              Upload a raw ResPlan JSON — drawing units are normalised through the plan's own area ratio, hub districts are grown over the door
              graph, and every cable run is rectilinear, door-constrained, and scored with J = L + 5X + O + 0.5B.
            </p>
            <div className="plan-picker">
              {PRESET_IDS.map((id) => (
                <button
                  key={id}
                  type="button"
                  className={planKey === id ? "plan-chip active" : "plan-chip"}
                  onClick={() => loadPlan(id, PRESET_JSON[id], presetCards[id]?.title ?? id)}
                >
                  <SquareStack size={13} />
                  <span>{presetCards[id]?.title ?? id}</span>
                  <small>{presetCards[id]?.note}</small>
                </button>
              ))}
              <button
                type="button"
                className="plan-chip dropzone"
                onDragOver={(event) => {
                  event.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragging(false);
                  const file = event.dataTransfer.files?.[0];
                  if (file) void ingestFile(file);
                }}
                onClick={() => fileInput.current?.click()}
              >
                <FileJson2 size={13} />
                <span>{dragging ? "RELEASE TO INGEST" : "DROP PLAN.JSON"}</span>
                <small>or click to browse</small>
              </button>
            </div>
            <div className="result-loader">
              <span className="loader-title">
                BACKEND RESULT
                <small>v1 · FILE-BASED</small>
              </span>
              <button type="button" className={official ? "plan-chip active" : "plan-chip"} onClick={() => resultInput.current?.click()}>
                <FileJson2 size={13} />
                <span>{official ? official.name : "LOAD <PLAN_ID>.JSON"}</span>
                <small>official output · slide 8/8</small>
              </button>
              <button type="button" className={overlayDraw ? "plan-chip active" : "plan-chip"} onClick={() => overlayInput.current?.click()}>
                <ScanLine size={13} />
                <span>{overlayDraw ? "OVERLAY LOADED" : "LOAD <PLAN_ID>.OVERLAY.JSON"}</span>
                <small>
                  {overlayDraw ? `schema v${overlayDraw.schemaVersion ?? "?"} · ${overlayDraw.violations.length} violations` : "walls · windows · markers · rules"}
                </small>
              </button>
              {(official || overlayDraw) && (
                <button type="button" className="loader-clear" onClick={clearResult}>
                  <X size={12} /> CLEAR
                </button>
              )}
            </div>
            <input
              ref={resultInput}
              type="file"
              accept="application/json,.json"
              className="hidden-input"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void loadOfficial(file);
                event.target.value = "";
              }}
            />
            <input
              ref={overlayInput}
              type="file"
              accept="application/json,.json"
              className="hidden-input"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void loadOverlayFile(file);
                event.target.value = "";
              }}
            />
            <div className="hero-actions">
              <button type="button" className="primary-action" onClick={runSolver} disabled={runState === "solving" || !plan}>
                {runState === "solving" ? <LoaderCircle size={16} className="spin" /> : <Play size={15} fill="currentColor" />}
                {runState === "solving" ? "SOLVING…" : "RUN OPTIMIZATION"}
              </button>
              <a href="#territories" className="scroll-action">TRACE THE RINGS <ChevronDown size={16} /></a>
            </div>
            {planError && (
              <p className="error-note"><TriangleAlert size={13} /> {planError}</p>
            )}
          </div>
          <div className="scroll-cue"><span>SCROLL TO TRACE</span><i /></div>
        </section>

        <section id="territories" className="story-panel narrative-panel">
          <div className="narrative-copy">
            <p className="chapter">01 / SCALE &amp; TERRITORY</p>
            <h2>Units become metres.<br />Rooms become districts.</h2>
            <p>
              {plan
                ? `${plan.rooms.length} spaces parsed · ${plan.eligibleRooms.length} eligible · ${plan.doors.length} door links · ` +
                  `${plan.eligibleAreaM2.toFixed(1)} m² of assigned-eligible area.`
                : "Load a plan to normalise coordinates."}
            </p>
            <div className="chapter-rule">
              <span>H_MIN {metrics?.hubLowerBound ?? "—"}</span>
              <span>USED {activeSolution?.hubs.length ?? "—"}</span>
              <span>NODES {metrics?.nodeCount ?? "—"}</span>
            </div>
          </div>
        </section>

        <section className="story-panel narrative-panel route-panel">
          <div className="narrative-copy">
            <p className="chapter">02 / MANHATTAN CONDUITS</p>
            <h2>A closed ring.<br />No shortcuts, no overlap.</h2>
            <p>
              Every hop is routed through the door graph with beveled elbow trays. Single-door rooms get laterally offset
              parallel lanes (Δ ≈ 0.22 m) plus stacked tiers, so O stays at zero while the ring re-enters the same threshold.
            </p>
            <div className="equation">H <b>→</b> N1 <b>→</b> N2 <b>→</b> … <b>→</b> Nk <b>→</b> H</div>
          </div>
        </section>

        <section className="story-panel narrative-panel audit-panel">
          <div className="narrative-copy">
            <p className="chapter">03 / PENALTY AUDIT</p>
            <h2>Ranked on hubs first,<br />then on cost J.</h2>
            <p>
              {metrics
                ? `L ${metrics.L.toFixed(2)} m · X ${metrics.X} · O ${metrics.O.toFixed(2)} m · B ${metrics.B} · area gap ${metrics.areaGap.toFixed(1)} m²`
                : "Solver idle."}
            </p>
            <button type="button" className="export-action" onClick={() => setDrawerOpen(true)}>
              <Braces size={16} /> OPEN SLIDE 8/8 OUTPUT
            </button>
          </div>
        </section>
      </main>

      {dragging && (
        <div
          className="drop-overlay"
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            const file = event.dataTransfer.files?.[0];
            if (file) void ingestFile(file);
          }}
        >
          <ArrowDownToLine size={30} />
          <span>DROP RAW RESPLAN .JSON</span>
        </div>
      )}

      <nav className="preset-bar" aria-label="Camera presets">
        <span className="preset-title">VIEWPORT</span>
        {presetButtons.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            className={activePreset === id ? "active" : ""}
            aria-pressed={activePreset === id}
            onClick={() => {
              setActivePreset(id);
              setPreset((current) => ({ id, token: current.token + 1 }));
            }}
          >
            <Icon size={14} /><span>{label}</span>
          </button>
        ))}
      </nav>

      <aside className="validation-panel" aria-label="Live scoring and constraint validation">
        <div className="panel-head">
          <div>
            <span className="panel-kicker">OFFICIAL SCORING ENGINE</span>
            <h2>VALIDATION MATRIX</h2>
          </div>
          <span
            className={
              runState === "solving"
                ? "validation-badge solving"
                : hasFailures
                  ? "validation-badge fail"
                  : feasible || isBackend
                    ? "validation-badge"
                    : "validation-badge warn"
            }
          >
            <i className="led" />
            {badgeLabel}
          </span>
        </div>
        <div className="source-line">
          {isBackend ? "BACKEND RESULT · METRICS FROM CHECKER SUMMARY" : "DEMO HEURISTIC · OFFICIAL CHECKER RECOMPUTES J FROM EXPORTED POLYLINES"}
        </div>

        <div className="cost-card">
          <div className="cost-formula">
            <span>SCORE</span>
            <em>J = L + 5X + O + 0.5B</em>
          </div>
          <strong>{metrics ? metrics.J.toFixed(2) : "—"}</strong>
          <div className="cost-grid">
            {[
              { k: "L", v: metrics ? `${metrics.L.toFixed(2)} m` : "—", n: "cable length" },
              { k: "X", v: metrics ? `${metrics.X}` : "—", n: "crossings ×5" },
              { k: "O", v: metrics ? `${metrics.O.toFixed(2)} m` : "—", n: "overlap / m" },
              { k: "B", v: metrics ? `${metrics.B}` : "—", n: "bends ×0.5" },
            ].map((item) => (
              <div key={item.k} className="cost-cell">
                <span>{item.k}</span>
                <strong>{item.v}</strong>
                <small>{item.n}</small>
              </div>
            ))}
          </div>
          <div className="cost-extra">
            <span>HUBS <strong>{activeSolution?.hubs.length ?? 0}</strong> / LOWER BOUND <strong>{metrics?.hubLowerBound ?? 0}</strong></span>
            <span>AREA GAP |A₁−A₂| <strong>{metrics ? `${metrics.areaGap.toFixed(1)} m²` : "—"}</strong></span>
          </div>
        </div>

        <div className="solver-status">
          <div className="solver-status-head">
            <span>{runState === "solving" ? "SOLVER PASS" : isBackend ? "BACKEND RESULT" : runState === "verified" ? "LAST SOLVE" : "AWAITING RUN"}</span>
            <strong>{runState === "solving" ? `${Math.min(stage + 1, solverStages.length)}/${solverStages.length}` : isBackend ? (official?.name ?? "RESULT") : `ITER ${activeSolution?.iterations ?? 0}`}</strong>
          </div>
          <ul className={runState === "solving" ? "stage-log live" : "stage-log"}>
            {solverStages.map((label, index) => (
              <li key={label} className={runState === "solving" && index === stage ? "active" : runState !== "solving" ? "done" : index < stage ? "done" : ""}>
                <i /> {label}
              </li>
            ))}
          </ul>
        </div>

        <div className="checks">
          {(activeSolution?.checks ?? []).map((check) => (
            <div key={check.id} className={check.na ? "check-row na" : check.pass ? "check-row pass" : "check-row fail"}>
              {check.na ? <Minus size={14} /> : check.pass ? <Check size={14} /> : <X size={14} />}
              <div><span>{check.label}</span><small>{check.detail}</small></div>
              <strong>{check.value}</strong>
            </div>
          ))}
        </div>

        <div className="load-section">
          <div className="load-head"><span>HUB LOAD / AREA</span><span>≤5 · ≤100 M²</span></div>
          {(activeSolution?.hubs ?? []).map((hub, index) => (
            <div key={hub.hub_id} className="load-row" style={{ ["--lane" as string]: hub.color }}>
              <span>{hub.hub_id}</span>
              <i>
                <b style={{ width: `${(hub.assigned_nodes.length / 5) * 100}%` }} />
                <u style={{ width: `${Math.min(100, (hub.area_m2 / 100) * 100)}%` }} />
              </i>
              <strong>{hub.assigned_nodes.length}·{hub.area_m2.toFixed(0)}</strong>
              <em className={`load-zone z${index % 4}`}>{hub.assigned_rooms.length} RM</em>
            </div>
          ))}
        </div>

        <div className="slice-control">
          <label htmlFor="wall-slice">
            <SlidersHorizontal size={13} /> SECTIONAL X-RAY
            <strong>{wallSlice.toFixed(2)} M</strong>
          </label>
          <input
            id="wall-slice"
            className="slice-slider"
            type="range"
            min={0}
            max={2.2}
            step={0.01}
            value={wallSlice}
            onChange={(event) => setWallSlice(Number(event.target.value))}
          />
          <div className="slice-scale"><span>0.00</span><span>1.10</span><span>2.20</span></div>
        </div>

        <div className="panel-foot">
          <DoorOpen size={14} />
          <span>{plan ? `${plan.doors.length} DOOR LINKS` : "NO PLAN"}</span>
          <strong>{planLabel.slice(0, 18)}</strong>
        </div>
        {warnings.length > 0 && (
          <p className="warn-note"><TriangleAlert size={11} /> {warnings[0]}{warnings.length > 1 ? ` (+${warnings.length - 1})` : ""}</p>
        )}
      </aside>

      {selected && (
        <aside className="inspector" aria-live="polite">
          <button type="button" aria-label="Close inspector" onClick={() => setSelected(null)}><X size={15} /></button>
          <span className="inspector-type">{selected.type.toUpperCase()} · {selected.id}</span>
          <h3>{selected.title}</h3>
          <p>{selected.detail}</p>
          <p className="inspector-coords">{selected.coords}</p>
          <div><span>{selected.zone}</span><strong><Check size={12} /> VERIFIED</strong></div>
        </aside>
      )}

      <div className="view-toolbar" aria-label="Model visibility controls">
        <div className="toolbar-hint"><MousePointer2 size={13} /> DRAG TO ORBIT</div>
        <button type="button" className={showZones ? "active" : ""} aria-pressed={showZones} onClick={() => setShowZones((value) => !value)}>
          <Layers3 size={16} /><span>ZONES</span>
        </button>
        <button type="button" className={showRoutes ? "active" : ""} aria-pressed={showRoutes} onClick={() => setShowRoutes((value) => !value)}>
          <Route size={16} /><span>CONDUITS</span>
        </button>
        <button type="button" onClick={runSolver} disabled={runState === "solving"}>
          {runState === "solving" ? <LoaderCircle size={16} className="spin" /> : <Play size={14} fill="currentColor" />}<span>RESOLVE</span>
        </button>
        <button type="button" className={drawerOpen ? "active" : ""} aria-pressed={drawerOpen} onClick={() => setDrawerOpen((value) => !value)}>
          <Braces size={16} /><span>OUTPUT JSON</span>
        </button>
        <button type="button" onClick={() => setSelected(null)}>
          <ScanLine size={16} /><span>RESET PICK</span>
        </button>
      </div>

      <div className="mobile-status">
        <span><i className="led" /> {feasible ? "FEASIBLE" : "CHECKING"}</span>
        <input
          className="slice-slider mobile-slice"
          type="range"
          aria-label="Sectional x-ray wall height"
          min={0}
          max={2.2}
          step={0.01}
          value={wallSlice}
          onChange={(event) => setWallSlice(Number(event.target.value))}
        />
        <strong>J {metrics ? metrics.J.toFixed(1) : "—"}</strong>
      </div>

      <section className={drawerOpen ? "json-drawer open" : "json-drawer"} aria-hidden={!drawerOpen}>
        <header>
          <div className="drawer-title">
            <Braces size={15} />
            <span>{isBackend ? "BACKEND OUTPUT · VERBATIM FILE" : "DEMO OUTPUT · SLIDE 8/8 SHAPE"}</span>
            <code>{isBackend ? (official?.name ?? "result.json") : activeSolution ? `${activeSolution.plan_id.toLowerCase()}-solution.json` : "awaiting-plan"}</code>
          </div>
          <div className="drawer-scores">
            <span className={isBackend ? "pill ok" : "pill"}>{isBackend ? "CHECKER-AUTHORITATIVE" : "DEMO ONLY"}</span>
            <span className={hasFailures ? "pill bad" : knownChecks.length ? "pill ok" : "pill"}>
              <i className="led" />{hasFailures ? `${knownChecks.filter((c) => !c.pass).length} RULE(S) FAIL` : knownChecks.length ? "ALL RULES PASS" : "AWAITING CHECKER"}
            </span>
            <span className="pill">H {activeSolution?.hubs.length ?? 0}</span>
            <span className="pill">L {metrics ? metrics.L.toFixed(2) : "—"} M</span>
            <span className="pill ok">J {metrics ? metrics.J.toFixed(2) : "—"}</span>
          </div>
          <div className="drawer-actions">
            <button type="button" onClick={copyJson}>{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? "COPIED" : "COPY JSON"}</button>
            <button type="button" onClick={download}><ShieldCheck size={14} />DOWNLOAD .JSON</button>
            <button type="button" aria-label="Close output drawer" onClick={() => setDrawerOpen(false)}><X size={15} /></button>
          </div>
        </header>
        <pre><code>{jsonText}</code></pre>
      </section>
    </div>
  );
}
