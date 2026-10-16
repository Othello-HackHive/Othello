import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import {
  ArrowDownToLine,
  Axis3d,
  Braces,
  Check,
  ChevronDown,
  ChevronsLeft,
  ChevronsRight,
  Copy,
  DoorOpen,
  FileJson2,
  Grid3x3,
  Layers3,
  LoaderCircle,
  Maximize2,
  Minimize2,
  Minus,
  Play,
  Route,
  RotateCcw,
  ScanLine,
  ShieldCheck,
  SlidersHorizontal,
  SquareStack,
  Target,
  TriangleAlert,
  Upload,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import TwinScene, { type CameraPresetId, type Inspection } from "./components/TwinScene";
import { normalizePlan, PRESET_IDS, PRESET_JSON, type PlanModel } from "./lib/resplan";
import { optimize, type Solution } from "./lib/optimizer";
import { parseOverlay, solutionFromOfficial, type OverlayDraw } from "./lib/overlay";
import { cloudAvailable, isCloudPlan, solveCloudPlan, type CloudEvent } from "./lib/cloudApi";
import { viewerPlanFromCloud } from "./lib/cloudPlan";

const phases = ["IMPORT", "ZONES", "ROUTE", "AUDIT"];

const presetButtons: Array<{ id: CameraPresetId; label: string; icon: typeof Grid3x3 }> = [
  { id: "top", label: "TOP 2D", icon: Grid3x3 },
  { id: "iso", label: "ISOMETRIC BIM", icon: Maximize2 },
  { id: "zone1", label: "ZONE 1 MACRO", icon: Target },
  { id: "zone2", label: "ZONE 2 MACRO", icon: Axis3d },
];

const presetCards: Record<string, { title: string; note: string }> = {
  BENCHMARK_6R: { title: "6-ROOM EXAMPLE", note: "door-linked rooms · 2 hubs" },
  RESPLAN_8R: { title: "8-ROOM EXAMPLE", note: "area and node capacity" },
  COMPLEX_12R: { title: "12-ROOM EXAMPLE", note: "larger connected floor plan" },
};

const solverStages = [
  "convert drawing coordinates to metres",
  "calculate the hub lower bound from nodes and area",
  "group connected rooms within both hub limits",
  "place hubs and visit each node in a closed ring",
  "measure length, crossings, overlap and bends",
];

const backendStages = [
  "scale coordinates with the plan's area ratio",
  "enumerate connected room groups; enforce 5 nodes / 100 m²",
  "try legal hub points at least 0.5 m from walls",
  "enumerate visit orders and search rectilinear routes",
  "score complete rings and run the independent checker",
];

type RunState = "ready" | "solving" | "verified";
type CloudState = { status: string; jobId?: string; events: CloudEvent[]; error?: string };
type CloudPayload = { raw: Record<string, unknown>; viewer: Record<string, unknown>; settings: Record<string, unknown> | null; key: string };

export default function App() {
  const [planKey, setPlanKey] = useState<string>("");
  const [rawPlan, setRawPlan] = useState<Record<string, unknown>>(() => PRESET_JSON.BENCHMARK_6R);
  const [planLabel, setPlanLabel] = useState("");
  const [planError, setPlanError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [showZones, setShowZones] = useState(true);
  const [showRoutes, setShowRoutes] = useState(true);
  const [panelCollapsed, setPanelCollapsed] = useState(true);
  const [modelView, setModelView] = useState(false);
  const modelViewRef = useRef(modelView);
  modelViewRef.current = modelView;
  const [wallSlice, setWallSlice] = useState(2.2);
  const [zoomLevel, setZoomLevel] = useState(1);
  const [phase, setPhase] = useState(0);
  const [selected, setSelected] = useState<Inspection | null>(null);
  const [runState, setRunState] = useState<RunState>("ready");
  const [optimizeSignal, setOptimizeSignal] = useState(0);
  const [preset, setPreset] = useState<{ id: CameraPresetId | "reset"; token: number }>({ id: "iso", token: 0 });
  const [activePreset, setActivePreset] = useState<CameraPresetId | null>(null);
  const handleScrollView = useCallback(() => setActivePreset(null), []);
  const handleInspect = useCallback((item: Inspection | null) => {
    if (!modelViewRef.current) setSelected(item);
  }, []);
  const sliceSliderStyle = { "--range-progress": `${(wallSlice / 2.2) * 100}%` } as CSSProperties;
  const zoomSliderStyle = { "--range-progress": `${((zoomLevel - 0.65) / 1.15) * 100}%` } as CSSProperties;
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [official, setOfficial] = useState<{ text: string; data: Record<string, unknown>; name: string } | null>(null);
  const [overlayDraw, setOverlayDraw] = useState<OverlayDraw | null>(null);
  const [cloudState, setCloudState] = useState<CloudState | null>(null);
  const cloudControllerRef = useRef<AbortController | null>(null);
  const cloudPayloadRef = useRef<CloudPayload | null>(null);
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
    const done = window.setTimeout(() => {
      setRunState("verified");
      setOptimizeSignal((value) => value + 1);
    }, 320 + solverStages.length * 330);
    return () => {
      clearTimeout(done);
    };
  }, [runState]);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1600);
    return () => clearTimeout(timer);
  }, [copied]);

  useEffect(() => {
    if (!modelView) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [modelView]);

  useEffect(() => () => cloudControllerRef.current?.abort(), []);

  const loadPlan = useCallback((key: string, json: Record<string, unknown>, label: string) => {
    cloudControllerRef.current?.abort();
    cloudControllerRef.current = null;
    cloudPayloadRef.current = null;
    setCloudState(null);
    setPlanKey(key);
    setRawPlan(json);
    setPlanLabel(label);
    setSelected(null);
    setOfficial(null);
    setOverlayDraw(null);
    setResultDismissed(false);
    setRunState("solving");
  }, []);

  const startCloud = async (payload: CloudPayload) => {
    cloudControllerRef.current?.abort();
    const controller = new AbortController();
    cloudControllerRef.current = controller;
    cloudPayloadRef.current = payload;
    setOfficial(null);
    setOverlayDraw(null);
    setResultDismissed(false);
    setRunState("ready");
    setCloudState({ status: "queued", events: [] });
    try {
      const result = await solveCloudPlan(payload.raw, payload.settings, controller.signal, (progress) => {
        if (controller.signal.aborted) return;
        setCloudState((current) => ({
          status: progress.status,
          jobId: progress.jobId,
          events: [...(current?.events ?? []), ...progress.events],
        }));
      });
      if (controller.signal.aborted) return;
      const viewer = normalizePlan(payload.viewer, payload.key).plan;
      const draw = parseOverlay(result.overlay, viewer);
      setOverlayDraw(draw);
      setOfficial({ text: result.officialText, data: result.official, name: `${payload.key}.json` });
      setWarnings((current) => [...current, ...draw.warnings]);
      setCloudState((current) => ({ status: "succeeded", jobId: result.jobId, events: current?.events ?? [] }));
      setRunState("verified");
      setOptimizeSignal((value) => value + 1);
      setPlanError(null);
    } catch (error) {
      if (!controller.signal.aborted) {
        setCloudState((current) => ({
          status: "failed",
          jobId: current?.jobId,
          events: current?.events ?? [],
          error: error instanceof TypeError
            ? "Could not reach the AWS API. Check VITE_OTHELLO_API_URL and the API's AllowedOrigins setting."
            : error instanceof Error ? error.message : "Could not run the cloud solver",
        }));
      }
    } finally {
      if (cloudControllerRef.current === controller) cloudControllerRef.current = null;
    }
  };

  const runSolver = () => {
    if (cloudAvailable && cloudPayloadRef.current) {
      void startCloud(cloudPayloadRef.current);
      return;
    }
    setSelected(null);
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
      cloudControllerRef.current?.abort();
      setCloudState(null);
      const text = await file.text();
      const data = JSON.parse(text) as Record<string, unknown>;
      const target = requirePlan();
      if (!target) return;
      if (!Array.isArray(data.hubs)) throw new Error("missing hubs[] — expected the official output file");
      if (typeof data.plan_id === "string" && data.plan_id !== target.plan_id) throw new Error(`plan_id ${data.plan_id} does not match ${target.plan_id}`);
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
      cloudControllerRef.current?.abort();
      setCloudState(null);
      const data = JSON.parse(await file.text()) as Record<string, unknown>;
      const target = requirePlan();
      if (!target) return;
      const parsed = parseOverlay(data, target);
      if (parsed.planId && parsed.planId !== target.plan_id) throw new Error(`plan_id ${parsed.planId} does not match ${target.plan_id}`);
      setOverlayDraw(parsed);
      setWarnings((current) => [...current, ...parsed.warnings]);
      if (official) setResultDismissed(false);
      setPlanError(null);
    } catch (error) {
      setPlanError(error instanceof Error ? `Overlay file invalid — ${error.message}` : "Overlay file invalid");
    }
  };

  const clearResult = () => {
    cloudControllerRef.current?.abort();
    setCloudState(null);
    setOfficial(null);
    setOverlayDraw(null);
    setResultDismissed(true);
  };

  const ingestFile = async (file: File) => {
    try {
      const json = JSON.parse(await file.text()) as Record<string, unknown>;
      if (!json || typeof json !== "object" || Array.isArray(json)) throw new Error("expected a JSON object");
      const wrapped = json.plan && typeof json.plan === "object" && !Array.isArray(json.plan);
      const raw = wrapped ? json.plan as Record<string, unknown> : json;
      const settings = wrapped && json.settings && typeof json.settings === "object" && !Array.isArray(json.settings)
        ? json.settings as Record<string, unknown> : null;
      const viewer = isCloudPlan(raw) ? viewerPlanFromCloud(raw) : raw;
      const key = file.name.replace(/\.json$/i, "").toUpperCase().slice(0, 16);
      loadPlan(key, viewer, file.name);
      if (cloudAvailable && isCloudPlan(raw)) {
        void startCloud({ raw, viewer, settings, key });
      } else if (cloudAvailable) {
        setCloudState({ status: "preview", events: [], error: "This file uses the browser preview format. The AWS solver accepts ResPlan GeoJSON or WKT plans." });
      }
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

  const hasSelectedPlan = planKey !== "";
  const metrics = hasSelectedPlan ? activeSolution?.metrics : null;
  const scoredMetrics = isBackend && !overlayDraw?.metrics ? null : metrics;
  const ruleChecks = hasSelectedPlan ? activeSolution?.checks ?? [] : [];
  const knownChecks = ruleChecks.filter((check) => !check.na);
  const feasible = knownChecks.length > 0 && knownChecks.every((check) => check.pass);
  const hasFailures = knownChecks.some((check) => !check.pass);
  const minimumProof = isBackend && overlayDraw?.provenMinimum === true;
  const displayedStages = isBackend ? backendStages : solverStages;
  const cloudRunning = cloudState?.status === "queued" || cloudState?.status === "running";
  const scaleSource = rawPlan.m_per_unit != null || rawPlan.metres_per_unit != null || rawPlan.meters_per_unit != null
    ? "declared metres per unit"
    : rawPlan.scale != null || rawPlan.units_per_metre != null
      ? "declared units per metre"
      : typeof rawPlan.area === "number" && typeof (rawPlan.inner as { area?: unknown } | undefined)?.area === "number"
        ? Number(rawPlan.area) > Number((rawPlan.inner as { area: number }).area)
          ? "√(area ÷ inner.area) units/m"
          : "√(inner.area ÷ area) units/m"
        : "fallback scale; check source JSON";
  const lowerByNodes = plan ? Math.ceil(plan.eligibleRooms.length / 5) : 0;
  const lowerByArea = plan ? Math.ceil(plan.eligibleAreaM2 / 100) : 0;
  const badgeLabel =
    cloudRunning
      ? `AWS ${cloudState?.status.toUpperCase()}`
      : cloudState?.status === "failed"
        ? "AWS ERROR"
        : cloudState?.status === "succeeded"
          ? "AWS CHECKED"
          : runState === "solving"
      ? "REPLAYING"
      : isBackend
        ? hasFailures
          ? "VIOLATIONS"
          : feasible
            ? "CHECKED"
            : "RECEIVED"
        : feasible
          ? "DEMO CHECKS PASS"
          : "IDLE";

  return (
    <div className={`app-shell${panelCollapsed ? " panel-collapsed" : ""}${modelView ? " model-view" : ""}`}>
      <header className="topbar">
        <div className="brand-lockup">
          <span className="brand-mark"><span /></span>
          <span className="brand-name">NodeRithm</span>
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
              zoomLevel={zoomLevel}
              optimizeSignal={optimizeSignal}
              preset={preset}
              focusHub={selected?.hub ?? null}
              modelShift={modelView ? 0 : panelCollapsed ? 0.11 : -0.12}
              modelView={modelView}
              onInspect={handleInspect}
              onPhaseChange={setPhase}
              onScrollView={handleScrollView}
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
            <h1><span>PLAN TO PATH</span>NodeRithm</h1>
            <p className="hero-line">Follow every decision from floor plan to cable route.</p>
            <p className="hero-support">
              Choose an example or upload a plan JSON to explore its hubs, routes and score.
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
                <FileJson2 size={24} />
                <span>{dragging ? "RELEASE TO INGEST" : "DROP PLAN.JSON"}</span>
                <small>Drag a plan here or click to browse</small>
              </button>
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
              <button type="button" className="primary-action" onClick={runSolver} disabled={runState === "solving" || cloudRunning || !plan || !hasSelectedPlan}>
                {runState === "solving" || cloudRunning ? <LoaderCircle size={16} className="spin" /> : <Play size={15} fill="currentColor" />}
                {cloudRunning ? "SOLVING ON AWS…" : cloudPayloadRef.current && cloudAvailable ? "RERUN ON AWS" : runState === "solving" ? "REPLAYING…" : "REPLAY SOLUTION STEPS"}
              </button>
              <a href="#territories" className="scroll-action">SEE HOW IT WAS DERIVED <ChevronDown size={16} /></a>
            </div>
            {cloudState && (
              <p className={cloudState.status === "failed" ? "cloud-message cloud-error" : "cloud-message"} role="status">
                {cloudState.status === "succeeded"
                  ? "AWS solution and checker overlay loaded."
                  : cloudState.error ?? cloudState.events.at(-1)?.message ?? "Submitting plan to the AWS solver…"}
              </p>
            )}
            {planError && (
              <p className="error-note"><TriangleAlert size={13} /> {planError}</p>
            )}
          </div>
        </section>

        <section id="territories" className="story-panel narrative-panel">
          <div className="narrative-copy">
            <p className="chapter">01 / SCALE &amp; HUB COUNT</p>
            <h2>First, measure<br />the plan.</h2>
            <p>
              {plan
                ? `${plan.plan_id}: ${plan.rooms.length} rooms, ${plan.eligibleRooms.length} eligible nodes and ${plan.doors.length} door links. ` +
                  `The eligible room polygons total ${plan.eligibleAreaM2.toFixed(1)} m². ` +
                  `Scale: ${plan.unitsPerM.toFixed(2)} drawing units/m (${scaleSource}).`
                : "Load a plan to see its scale and room counts."}
            </p>
            {plan && (
              <div className="derivation">
                <strong>H lower bound = max(⌈nodes ÷ 5⌉, ⌈area ÷ 100⌉)</strong>
                <span>max(⌈{plan.eligibleRooms.length} ÷ 5⌉ = {lowerByNodes}, ⌈{plan.eligibleAreaM2.toFixed(1)} ÷ 100⌉ = {lowerByArea}) = {Math.max(lowerByNodes, lowerByArea)}</span>
                <small>{minimumProof ? "The backend proved the displayed hub count is minimal." : "This bound alone does not prove the displayed design is optimal."}</small>
              </div>
            )}
            <div className="chapter-rule">
              <span>LOWER BOUND {metrics?.hubLowerBound ?? "—"}</span>
              <span>USED {activeSolution?.hubs.length ?? "—"}</span>
              <span>NODES {metrics?.nodeCount ?? "—"}</span>
            </div>
          </div>
        </section>

        <section className="story-panel narrative-panel route-panel">
          <div className="narrative-copy">
            <p className="chapter">02 / GROUPS &amp; RINGS</p>
            <h2>Every node gets<br />one return path.</h2>
            <p>
              {isBackend
                ? "The backend enumerates connected room groups, tries legal hub spots, visit orders and rectilinear routes, then checks the full design. Each ring starts at its hub, visits every assigned node once and returns to that hub."
                : "The browser preview grows connected room groups, places a hub in each group, orders nodes with a nearest-neighbour seed and 2-opt, and routes between them through doors. Load backend output and overlay files for the full solver and independent checks."}
            </p>
            <div className="hub-derivations">
              {(activeSolution?.hubs ?? []).map((hub) => (
                <div className="hub-derivation" key={hub.hub_id}>
                  <strong>{hub.hub_id} in {hub.room_id} at ({hub.location[0].toFixed(2)}, {hub.location[1].toFixed(2)}) m</strong>
                  <span>{hub.assigned_nodes.length}/5 nodes · {hub.area_m2.toFixed(1)}/100 m²</span>
                  <span>Rooms: {hub.assigned_rooms.join(", ")}</span>
                  <span>Visit: {hub.closed_sequence.join(" → ")}</span>
                  <span>{hub.route_segments.length} legs including return · {hub.ring_length_m.toFixed(2)} m</span>
                  <span>Legs: {hub.route_segments.map((leg) => `${leg.from}→${leg.to} ${leg.length_m.toFixed(1)}m`).join(" · ")}</span>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="story-panel narrative-panel audit-panel">
          <div className="narrative-copy">
            <p className="chapter">03 / SCORE &amp; CHECKS</p>
            <h2>Ranked on hubs first,<br />then on cost J.</h2>
            <p>
              {scoredMetrics
                ? `${scoredMetrics.L.toFixed(2)} m of cable + 5 × ${scoredMetrics.X} crossings + ${scoredMetrics.O.toFixed(2)} m overlap + 0.5 × ${scoredMetrics.B} bends = J ${scoredMetrics.J.toFixed(2)}. ` +
                  `Area gap: ${scoredMetrics.areaGap.toFixed(1)} m². ${isBackend && overlayDraw?.checks.length ? `${overlayDraw.checks.filter((check) => check.pass).length}/${overlayDraw.checks.length} backend checks pass.` : "Browser checks are a preview; load the backend overlay for its independent checker results."}`
                : isBackend ? "Load this result's overlay JSON to see the checker score breakdown." : "Load a plan to see the measured score."}
            </p>
            <div className="derivation">
              <strong>What the score counts</strong>
              <span>L includes every leg and the return trip. X counts cable crossings away from hubs and nodes. O measures shared cable length. B counts direction changes, including at stops.</span>
              <small>Compare hub count first, then J, then the gap between the largest and smallest assigned hub areas.</small>
            </div>
            {isBackend && overlayDraw?.notes.length ? <p>{overlayDraw.notes.join(" ")}</p> : null}
            <button type="button" className="export-action" onClick={() => setDrawerOpen(true)}>
              <Braces size={16} /> OPEN SOLUTION JSON
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
            aria-label={label}
            title={label}
            aria-pressed={activePreset === id}
            onClick={() => {
              setActivePreset(id);
              setPreset((current) => ({ id, token: current.token + 1 }));
            }}
          >
            <Icon size={14} /><span>{label}</span>
          </button>
        ))}
        <button
          type="button"
          className="reset-view"
          aria-label="Reset to scroll view"
          title="Reset to scroll view"
          onClick={() => {
            setActivePreset(null);
            setSelected(null);
            setPreset((current) => ({ id: "reset", token: current.token + 1 }));
          }}
        >
          <RotateCcw size={14} /><span>RESET VIEW</span>
        </button>
      </nav>

      {panelCollapsed && (
        <button type="button" className="panel-reopen" onClick={() => setPanelCollapsed(false)} aria-label="Show rules and score panel" aria-controls="rules-score-panel" aria-expanded={false}>
          <ChevronsLeft size={16} /> <span>RULES &amp; SCORE</span> <strong>J {scoredMetrics ? scoredMetrics.J.toFixed(1) : "—"}</strong>
        </button>
      )}
      <aside id="rules-score-panel" className={panelCollapsed ? "validation-panel collapsed" : "validation-panel"} aria-label="Rules and score" aria-hidden={panelCollapsed} inert={panelCollapsed}>
        <div className="panel-head">
          <div>
            <h2>RULES &amp; SCORE</h2>
          </div>
          <div className="panel-head-actions">
            {hasSelectedPlan && <span
              className={
                runState === "solving"
                  ? "validation-badge solving"
                  : hasFailures || cloudState?.status === "failed"
                    ? "validation-badge fail"
                    : feasible || isBackend
                      ? "validation-badge"
                      : "validation-badge warn"
              }
            >
              <i className="led" />
              {badgeLabel}
            </span>}
            <button type="button" className="panel-collapse" onClick={() => setPanelCollapsed(true)} aria-label="Collapse rules and score panel" aria-controls="rules-score-panel" aria-expanded={true} title="Collapse panel">
              <ChevronsRight size={16} />
            </button>
          </div>
        </div>
        {!hasSelectedPlan ? (
          <div className="panel-empty" role="status">
            Select an example or upload a plan JSON to see its score and rule checks.
          </div>
        ) : (
          <>
        <div className="source-line">
          {cloudRunning
            ? `AWS solver ${cloudState?.status} · ${cloudState?.events.at(-1)?.message ?? "waiting for progress"}`
            : cloudState?.status === "failed"
              ? `AWS solver error · ${cloudState.error}`
              : isBackend
                ? (overlayDraw?.metrics ? "Score and checks from the backend overlay" : "Load the matching overlay for the full score and checks")
                : "Browser estimate · load an overlay for independent checks"}
        </div>

        <div className="cost-card">
          <div className="cost-formula">
            <span>SCORE</span>
            <em>J = L + 5X + O + 0.5B</em>
          </div>
          <strong>{scoredMetrics ? scoredMetrics.J.toFixed(2) : "—"}</strong>
          <div className="cost-grid">
            {[
              { k: "L", v: metrics ? `${metrics.L.toFixed(2)} m` : "—", n: "cable length" },
              { k: "X", v: scoredMetrics ? `${scoredMetrics.X}` : "—", n: "crossings · 5 each" },
              { k: "O", v: scoredMetrics ? `${scoredMetrics.O.toFixed(2)} m` : "—", n: "shared cable length" },
              { k: "B", v: scoredMetrics ? `${scoredMetrics.B}` : "—", n: "bends · 0.5 each" },
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
            <span>AREA GAP MAX−MIN <strong>{scoredMetrics ? `${scoredMetrics.areaGap.toFixed(1)} m²` : "—"}</strong></span>
          </div>
        </div>

        <details className="solver-status">
          <summary className="solver-status-head">
            <span>HOW THE SOLUTION IS DERIVED</span>
            <ChevronDown size={15} />
          </summary>
          <ol className="stage-log">
            {cloudState?.events.length
              ? cloudState.events.map((event, index) => <li key={`${index}-${event.stage}`}>{event.stage}: {event.message}</li>)
              : displayedStages.map((label) => <li key={label}>{label}</li>)}
          </ol>
        </details>

        <div className="checks">
          <h3 className="panel-section-title">RULE CHECKS <span>{knownChecks.filter((check) => check.pass).length}/{knownChecks.length} PASS</span></h3>
          {(activeSolution?.checks ?? []).map((check) => (
            <div key={check.id} className={check.na ? "check-row na" : check.pass ? "check-row pass" : "check-row fail"}>
              {check.na ? <Minus size={14} /> : check.pass ? <Check size={14} /> : <X size={14} />}
              <div><span>{check.label}</span><small>{check.detail}</small></div>
              <strong>{check.value}</strong>
            </div>
          ))}
        </div>

        <div className="load-section">
          <div className="load-head"><span>HUB LOAD / AREA</span><span>MAX 5 NODES · 100 M²</span></div>
          {(activeSolution?.hubs ?? []).map((hub, index) => (
            <div key={hub.hub_id} className="load-row" style={{ ["--lane" as string]: hub.color }}>
              <span>{hub.hub_id}</span>
              <i>
                <b style={{ width: `${(hub.assigned_nodes.length / 5) * 100}%` }} />
                <u style={{ width: `${Math.min(100, (hub.area_m2 / 100) * 100)}%` }} />
              </i>
              <strong>{hub.assigned_nodes.length} N · {hub.area_m2.toFixed(0)} m²</strong>
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
            style={sliceSliderStyle}
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
          </>
        )}
      </aside>

      {selected && (
        <aside className="inspector" aria-live="polite">
          <button type="button" aria-label="Close inspector" onClick={() => setSelected(null)}><X size={15} /></button>
          <span className="inspector-type">{selected.type.toUpperCase()} · {selected.id}</span>
          <h3>{selected.title}</h3>
          <p>{selected.detail}</p>
          <p className="inspector-coords">{selected.coords}</p>
          <div><span>{selected.zone}</span><strong>{isBackend && overlayDraw?.valid === true ? "CHECKED" : "PREVIEW"}</strong></div>
        </aside>
      )}

      <div className="view-toolbar" aria-label="Model visibility controls">
        <button
          type="button"
          className={modelView ? "model-view-toggle active" : "model-view-toggle"}
          aria-pressed={modelView}
          aria-label={modelView ? "Exit model view" : "Enter model view"}
          title={modelView ? "Exit model view" : "Model view"}
          onClick={() => {
            setModelView((value) => !value);
            setSelected(null);
            setDrawerOpen(false);
          }}
        >
          {modelView ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
          <span>{modelView ? "EXIT MODEL VIEW" : "MODEL VIEW"}</span>
        </button>
        <button type="button" className={showZones ? "active" : ""} aria-label="Toggle zones" title="Toggle zones" aria-pressed={showZones} onClick={() => setShowZones((value) => !value)}>
          <Layers3 size={16} /><span>ZONES</span>
        </button>
        <button type="button" className={showRoutes ? "active" : ""} aria-label="Toggle conduits" title="Toggle conduits" aria-pressed={showRoutes} onClick={() => setShowRoutes((value) => !value)}>
          <Route size={16} /><span>CONDUITS</span>
        </button>
        <button type="button" className={drawerOpen ? "toolbar-output active" : "toolbar-output"} aria-label="Open output JSON" title="Open output JSON" aria-pressed={drawerOpen} onClick={() => setDrawerOpen((value) => !value)}>
          <Braces size={16} /><span>OUTPUT JSON</span>
        </button>
        <button type="button" className="toolbar-reset-pick" aria-label="Clear selection" title="Clear selection" onClick={() => setSelected(null)}>
          <ScanLine size={16} /><span>RESET PICK</span>
        </button>
        <div className="toolbar-zoom" aria-label="Model zoom">
          <ZoomOut size={13} aria-hidden="true" />
          <input
            type="range"
            min={0.65}
            max={1.8}
            step={0.01}
            value={zoomLevel}
            style={zoomSliderStyle}
            aria-label="Zoom 3D model"
            aria-valuetext={`${Math.round(zoomLevel * 100)}%`}
            onChange={(event) => setZoomLevel(Number(event.target.value))}
          />
          <ZoomIn size={13} aria-hidden="true" />
        </div>
      </div>

      <div className="mobile-status">
        <span><i className="led" /> {isBackend && overlayDraw?.valid === true ? "CHECKED" : "PREVIEW"}</span>
        <input
          className="slice-slider mobile-slice"
          type="range"
          aria-label="Sectional x-ray wall height"
          min={0}
          max={2.2}
          step={0.01}
          value={wallSlice}
          style={sliceSliderStyle}
          onChange={(event) => setWallSlice(Number(event.target.value))}
        />
        <strong>J {scoredMetrics ? scoredMetrics.J.toFixed(1) : "—"}</strong>
      </div>

      <section className={drawerOpen ? "json-drawer open" : "json-drawer"} aria-hidden={!drawerOpen}>
        <header>
          <div className="drawer-title">
            <Braces size={15} />
            <span>SOLUTION JSON</span>
            <code>{isBackend ? (official?.name ?? "result.json") : activeSolution ? `${activeSolution.plan_id.toLowerCase()}-solution.json` : "awaiting-plan"}</code>
          </div>
          <div className="drawer-scores">
            <span className={isBackend ? "pill ok" : "pill"}>{isBackend ? "CHECKER-AUTHORITATIVE" : "DEMO ONLY"}</span>
            <span className={hasFailures ? "pill bad" : knownChecks.length ? "pill ok" : "pill"}>
              <i className="led" />{hasFailures ? `${knownChecks.filter((c) => !c.pass).length} RULE(S) FAIL` : knownChecks.length ? "ALL RULES PASS" : "AWAITING CHECKER"}
            </span>
            <span className="pill">H {activeSolution?.hubs.length ?? 0}</span>
            <span className="pill">L {metrics ? metrics.L.toFixed(2) : "—"} M</span>
            <span className="pill ok">J {scoredMetrics ? scoredMetrics.J.toFixed(2) : "—"}</span>
          </div>
          <div className="drawer-actions">
            <button type="button" onClick={copyJson}>{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? "COPIED" : "COPY JSON"}</button>
            <button type="button" onClick={download}><ShieldCheck size={14} />DOWNLOAD .JSON</button>
            <button type="button" aria-label="Close output drawer" onClick={() => setDrawerOpen(false)}><X size={15} /></button>
          </div>
        </header>
        <details className="drawer-import">
          <summary>Use solver files <ChevronDown size={14} /></summary>
          <div>
            <button type="button" onClick={() => resultInput.current?.click()}><FileJson2 size={14} /> {official ? `Solution: ${official.name}` : "Import solution JSON"}</button>
            <button type="button" onClick={() => overlayInput.current?.click()}><ScanLine size={14} /> {overlayDraw ? "Overlay loaded" : "Import overlay JSON"}</button>
            {(official || overlayDraw) && <button type="button" onClick={clearResult}><X size={14} /> Clear solver files</button>}
          </div>
        </details>
        <pre><code>{jsonText}</code></pre>
      </section>
    </div>
  );
}
