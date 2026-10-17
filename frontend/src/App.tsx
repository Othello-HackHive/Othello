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
  FileDown,
  Share2,
  Activity,
  MoreHorizontal,
} from "lucide-react";
import TwinScene, { type CameraPresetId, type Inspection } from "./components/TwinScene";
import CostHistory from "./components/CostHistory";
import SolutionReport from "./components/SolutionReport";
import { normalizePlan, pointInPolygon, PRESET_IDS, PRESET_JSON, type PlanModel } from "./lib/resplan";
import { optimize, type Solution } from "./lib/optimizer";
import { parseOverlay, solutionFromOfficial, type OverlayDraw } from "./lib/overlay";
import { cloudAvailable, isCloudPlan, rerouteCloudHub, solveCloudPlan, type CloudEvent } from "./lib/cloudApi";
import { viewerPlanFromCloud } from "./lib/cloudPlan";
import { createShareLink, readShareLink, type SharedView } from "./lib/shareLink";

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

const ignoreInspection = (_item: Inspection | null) => undefined;
const ignorePhase = (_phase: number) => undefined;
const ignoreScrollView = () => undefined;

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
  const [showHeatmap, setShowHeatmap] = useState(false);
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
  const toolbarMenuRef = useRef<HTMLDetailsElement>(null);
  const [copied, setCopied] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [official, setOfficial] = useState<{ text: string; data: Record<string, unknown>; name: string } | null>(null);
  const [overlayDraw, setOverlayDraw] = useState<OverlayDraw | null>(null);
  const [draftSolution, setDraftSolution] = useState<Solution | null>(null);
  const [draftStatus, setDraftStatus] = useState<"checking" | "valid" | "invalid" | "preview" | null>(null);
  const [draftMessage, setDraftMessage] = useState("");
  const draftControllerRef = useRef<AbortController | null>(null);
  const [shareStatus, setShareStatus] = useState("");
  const [shareUrl, setShareUrl] = useState("");
  const [baselineData, setBaselineData] = useState<Record<string, unknown> | null>(null);
  const [comparisonOpen, setComparisonOpen] = useState(false);
  const [comparePosition, setComparePosition] = useState(50);
  const [morphing, setMorphing] = useState(false);
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

  const activeSolution = draftSolution ?? resultSolution ?? solution;
  const isBackend = draftSolution ? draftStatus === "valid" || draftStatus === "invalid" : resultSolution !== null;
  const baselineMetrics = cloudState?.events.find((event) => event.candidate_kind === "baseline")?.metrics;
  const baselineView = useMemo<{ solution: Solution | null; error: string | null }>(() => {
    if (!plan || !baselineData || !baselineMetrics || !resultSolution) return { solution: null, error: null };
    try {
      const result = solutionFromOfficial(baselineData, JSON.stringify(baselineData), plan, null);
      return { solution: { ...result, metrics: { ...result.metrics, ...baselineMetrics } }, error: null };
    } catch (error) {
      return { solution: null, error: error instanceof Error ? error.message : "Could not read the F0 design" };
    }
  }, [plan, baselineData, baselineMetrics, resultSolution]);
  const baselineSolution = baselineView.solution;

  const jsonText = useMemo(
    () => {
      if (!activeSolution) return "{}";
      if (activeSolution.rawText) return activeSolution.rawText;
      if (!draftSolution) return JSON.stringify(activeSolution.output, null, 2);
      return JSON.stringify({
        ...activeSolution.output,
        reported_metrics: { hub_count: activeSolution.hubs.length, total_length_m: activeSolution.metrics.L },
      }, null, 2);
    },
    [activeSolution, draftSolution],
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
  useEffect(() => () => draftControllerRef.current?.abort(), []);
  useEffect(() => {
    let cancelled = false;
    void readShareLink(window.location.hash).then((shared) => {
      if (!shared || cancelled) return;
      setPlanKey(shared.key);
      setRawPlan(shared.plan);
      setPlanLabel(shared.label);
      setOfficial(shared.official ? { ...shared.official, text: JSON.stringify(shared.official.data, null, 2) } : null);
      setOverlayDraw(shared.overlay);
      setBaselineData(shared.baseline);
      setCloudState(shared.events.length ? { status: "shared", events: shared.events } : null);
      setDraftSolution(shared.draft);
      setDraftStatus(shared.draftStatus);
      setShowZones(shared.view.zones);
      setShowRoutes(shared.view.routes);
      setShowHeatmap(shared.view.heatmap);
      setWallSlice(shared.view.wallSlice);
      setZoomLevel(shared.view.zoom);
      setRunState("verified");
      setPlanError(null);
      setShareStatus("Shared result loaded.");
    }).catch((error) => {
      if (!cancelled) setShareStatus(error instanceof Error ? error.message : "Could not open the shared result.");
    });
    return () => { cancelled = true; };
  }, []);

  const loadPlan = useCallback((key: string, json: Record<string, unknown>, label: string) => {
    cloudControllerRef.current?.abort();
    draftControllerRef.current?.abort();
    setDraftSolution(null);
    setDraftStatus(null);
    cloudControllerRef.current = null;
    cloudPayloadRef.current = null;
    setCloudState(null);
    setBaselineData(null);
    setComparisonOpen(false);
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
    draftControllerRef.current?.abort();
    setDraftSolution(null);
    setDraftStatus(null);
    const controller = new AbortController();
    cloudControllerRef.current = controller;
    cloudPayloadRef.current = payload;
    setOfficial(null);
    setOverlayDraw(null);
    setBaselineData(null);
    setComparisonOpen(false);
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
      setBaselineData(result.baseline);
      setOfficial({ text: result.officialText, data: result.official, name: `${payload.key}.json` });
      setWarnings((current) => [...current, ...draw.warnings]);
      setCloudState((current) => {
        const events = current?.events ?? [];
        const recovered = result.baselineMetrics && !events.some((event) => event.candidate_kind === "baseline")
          ? [{ stage: "candidate", message: "Verified F0 baseline", candidate_kind: "baseline" as const, metrics: result.baselineMetrics }, ...events]
          : events;
        return { status: "succeeded", jobId: result.jobId, events: recovered };
      });
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
    draftControllerRef.current?.abort();
    setDraftSolution(null);
    setDraftStatus(null);
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
      draftControllerRef.current?.abort();
      setDraftSolution(null);
      setDraftStatus(null);
      setCloudState(null);
      setBaselineData(null);
      setComparisonOpen(false);
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
      draftControllerRef.current?.abort();
      setDraftSolution(null);
      setDraftStatus(null);
      setCloudState(null);
      setBaselineData(null);
      setComparisonOpen(false);
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
    draftControllerRef.current?.abort();
    setDraftSolution(null);
    setDraftStatus(null);
    setCloudState(null);
    setBaselineData(null);
    setComparisonOpen(false);
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
    anchor.download = draftSolution ? `${activeSolution.plan_id.toLowerCase()}-edited-candidate.json` : isBackend ? (official?.name ?? "result.json") : `${activeSolution.plan_id.toLowerCase()}-solution.json`;
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

  const shareResult = async () => {
    if (!planKey || !activeSolution) return;
    try {
      const shared: SharedView = {
        v: 1, key: planKey, label: planLabel, plan: rawPlan,
        official: official ? { data: official.data, name: official.name } : null,
        overlay: overlayDraw, baseline: baselineData,
        events: cloudState?.events.filter((event) => event.stage === "candidate") ?? [],
        draft: draftSolution, draftStatus: draftStatus === "checking" ? "preview" : draftStatus,
        view: { zones: showZones, routes: showRoutes, heatmap: showHeatmap, wallSlice, zoom: zoomLevel },
      };
      const url = await createShareLink(shared);
      setShareUrl(url);
      await navigator.clipboard.writeText(url);
      setShareStatus("Share link copied. Anyone with the link can view this result.");
    } catch (error) {
      setShareStatus(error instanceof Error ? error.message : "Could not create a share link.");
    }
  };

  const handleHubMove = useCallback((hubId: string, position: [number, number]) => {
    if (!plan || !activeSolution) return;
    draftControllerRef.current?.abort();
    const jobId = cloudState?.status === "succeeded" ? cloudState.jobId : undefined;
    let candidate: Record<string, unknown>;
    try {
      const edited = optimize(plan, undefined, { base: activeSolution, hubId, position });
      setDraftSolution(edited);
      candidate = edited.output;
      setDraftMessage("");
      setSelected(null);
    } catch (error) {
      if (!jobId || !resultSolution) {
        setDraftMessage(error instanceof Error ? error.message : "Could not reroute this hub");
        return;
      }
      const host = activeSolution.hubs.find((hub) => hub.hub_id === hubId);
      const room = plan.rooms.find((item) => host?.assigned_rooms.includes(item.id) && pointInPolygon(position, item.polygon));
      if (!room) {
        setDraftMessage("The moved hub is outside its assigned rooms.");
        return;
      }
      candidate = structuredClone(activeSolution.output);
      const hub = (candidate.hubs as Array<Record<string, unknown>>).find((item) => item.hub_id === hubId);
      if (!hub) return;
      hub.room_id = room.id;
      hub.location = { x: position[0], y: position[1] };
      setSelected(null);
    }
    if (!jobId || !resultSolution) {
      setDraftStatus("preview");
      return;
    }
    const controller = new AbortController();
    draftControllerRef.current = controller;
    setDraftMessage("");
    setDraftStatus("checking");
    void rerouteCloudHub(jobId, hubId, candidate, controller.signal).then(({ official: rerouted, validation }) => {
      if (controller.signal.aborted) return;
      const solved = solutionFromOfficial(rerouted, JSON.stringify(rerouted, null, 2), plan, overlayDraw);
      setDraftSolution({
        ...solved,
        source: "backend",
        metrics: validation.metrics ? {
          ...solved.metrics, L: validation.metrics.L, X: validation.metrics.X,
          O: validation.metrics.O, B: validation.metrics.B, J: validation.metrics.J,
          areaGap: validation.metrics.area_gap_m2,
        } : solved.metrics,
        checks: validation.checks.map((check) => ({
          id: check.rule_id, label: check.label, pass: check.passed,
          value: check.value, detail: check.detail,
        })),
      });
      setDraftStatus(validation.valid ? "valid" : "invalid");
      setDraftMessage(validation.valid ? "AWS rerouted this hub and verified the new score." :
        validation.violations.slice(0, 2).map((item) => `${item.type}: ${item.detail}`).join(" · ") || "AWS reroute failed the independent checker.");
    }).catch((error) => {
      if (controller.signal.aborted) return;
      setDraftStatus("preview");
      setDraftMessage(error instanceof Error ? error.message : "Could not reroute this hub on AWS");
    });
  }, [plan, activeSolution, cloudState?.status, cloudState?.jobId, resultSolution, overlayDraw]);

  const animateMorph = () => {
    setMorphing(false);
    setComparePosition(5);
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
      setMorphing(true);
      setComparePosition(95);
      window.setTimeout(() => setMorphing(false), 1650);
    }));
  };

  const hasSelectedPlan = planKey !== "";
  const cloudRunning = cloudState?.status === "queued" || cloudState?.status === "running";
  const hubEditRunning = draftStatus === "checking";
  const sceneOverlay = useMemo(() => draftSolution && overlayDraw
    ? { ...overlayDraw, legs: [], violations: [], crossings: [], overlaps: [], bends: [] }
    : isBackend ? overlayDraw : null, [draftSolution, overlayDraw, isBackend]);
  const panelSolution = cloudRunning ? null : activeSolution;
  const metrics = hasSelectedPlan ? panelSolution?.metrics : null;
  const scoreWeights = cloudState?.events.find((event) => event.weights)?.weights;
  const scoredMetrics = isBackend && !overlayDraw?.metrics ? null : metrics;
  const ruleChecks = hasSelectedPlan ? panelSolution?.checks ?? [] : [];
  const knownChecks = ruleChecks.filter((check) => !check.na);
  const feasible = knownChecks.length > 0 && knownChecks.every((check) => check.pass);
  const hasFailures = knownChecks.some((check) => !check.pass);
  const minimumProof = isBackend && overlayDraw?.provenMinimum === true;
  const displayedStages = isBackend ? backendStages : solverStages;
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
      : draftStatus === "checking" ? "AWS REROUTING"
      : draftStatus === "valid" ? "EDIT CHECKED"
      : draftStatus === "invalid" ? "EDIT VIOLATIONS"
      : draftStatus === "preview" ? "EDIT PREVIEW"
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
    <div className={`app-shell${panelCollapsed ? " panel-collapsed" : ""}${modelView ? " model-view" : ""}${comparisonOpen ? " comparing" : ""}${morphing ? " morphing" : ""}`} style={{ "--compare-position": `${comparePosition}%` } as CSSProperties}>
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
          <button type="button" className="ghost-action share-action" onClick={() => void shareResult()} disabled={!planKey || !activeSolution} title="Copy a link to this result"><Share2 size={14} /> <span>SHARE RESULT</span></button>
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
      {shareStatus && <div className="share-popover" role="status"><span>{shareStatus}</span><button type="button" aria-label="Close share message" onClick={() => { setShareStatus(""); setShareUrl(""); }}><X size={13} /></button>{shareUrl && <input type="text" readOnly value={shareUrl} aria-label="Share result link" onFocus={(event) => event.target.select()} />}</div>}

      <main id="story" className="story">
        <div className="scene-sticky">
          {comparisonOpen && plan && baselineSolution && (
            <div className="comparison-layer baseline-layer">
              <TwinScene
                plan={plan} solution={baselineSolution} overlay={null}
                showZones={showZones} showRoutes={showRoutes} wallHeight={wallSlice / 2.2}
                zoomLevel={zoomLevel} optimizeSignal={optimizeSignal} preset={preset}
                focusHub={null} modelShift={0} modelView={modelView}
                showHeatmap={showHeatmap} verified={true}
                onInspect={ignoreInspection} onPhaseChange={ignorePhase} onScrollView={ignoreScrollView}
              />
            </div>
          )}
          <div className="comparison-layer optimized-layer">
          {plan && activeSolution ? (
            <TwinScene
              plan={plan}
              solution={activeSolution}
              overlay={sceneOverlay}
              showZones={showZones}
              showRoutes={showRoutes}
              showHeatmap={showHeatmap}
              verified={draftSolution ? draftStatus === "valid" : isBackend && overlayDraw?.valid === true}
              onHubMove={hasSelectedPlan && !cloudRunning && !comparisonOpen ? handleHubMove : undefined}
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
          </div>
          {comparisonOpen && baselineSolution && activeSolution && (
            <div className="comparison-divider" aria-hidden="true"><span>F0</span><span>OPTIMIZED</span></div>
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
          {showHeatmap && <div className="heatmap-legend" aria-label="Cable density colors"><span>LOW DENSITY</span><i /><span>HIGH DENSITY</span></div>}
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
              <button type="button" className="primary-action" onClick={runSolver} disabled={runState === "solving" || cloudRunning || hubEditRunning || !plan || !hasSelectedPlan} aria-busy={cloudRunning || hubEditRunning}>
                {runState === "solving" || cloudRunning || hubEditRunning ? <LoaderCircle size={16} className="spin" /> : <Play size={15} fill="currentColor" />}
                {hubEditRunning ? "REROUTING HUB ON AWS…" : cloudRunning ? "SOLVING ON AWS…" : cloudPayloadRef.current && cloudAvailable ? "RERUN ON AWS" : runState === "solving" ? "REPLAYING…" : "REPLAY SOLUTION STEPS"}
              </button>
              <a href="#territories" className="scroll-action">SEE HOW IT WAS DERIVED <ChevronDown size={16} /></a>
            </div>
            {cloudState && (
              <p className={cloudState.status === "failed" || draftStatus === "invalid" || (draftStatus === "preview" && draftMessage) ? "cloud-message cloud-error" : "cloud-message"} role="status">
                {hubEditRunning
                  ? "AWS is rerouting the moved hub and checking the new score…"
                  : draftStatus && draftMessage
                    ? draftMessage
                    : cloudState.status === "succeeded"
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
                ? `${scoredMetrics.L.toFixed(2)} m of cable × ${scoreWeights?.L ?? 1} + ${scoredMetrics.X} crossings × ${scoreWeights?.X ?? 5} + ${scoredMetrics.O.toFixed(2)} m overlap × ${scoreWeights?.O ?? 1} + ${scoredMetrics.B} bends × ${scoreWeights?.B ?? 0.5} = J ${scoredMetrics.J.toFixed(2)}. ` +
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

      {comparisonOpen && baselineSolution && activeSolution && (
        <div className="comparison-controls" aria-label="F0 and optimized comparison">
          <div><strong>F0 {baselineSolution.metrics.J.toFixed(2)}</strong><span>→</span><strong>OPTIMIZED {activeSolution.metrics.J.toFixed(2)}</strong></div>
          <p>{baselineSolution.metrics.J > activeSolution.metrics.J
            ? `${(baselineSolution.metrics.J - activeSolution.metrics.J).toFixed(2)} lower J · ${((baselineSolution.metrics.J - activeSolution.metrics.J) / baselineSolution.metrics.J * 100).toFixed(1)}% improvement`
            : "The best verified design matches the F0 score."}</p>
          <div className="comparison-range"><span>F0</span><input type="range" min="5" max="95" value={comparePosition} aria-label="Reveal baseline or optimized model" onChange={(event) => { setMorphing(false); setComparePosition(Number(event.target.value)); }} /><span>RESULT</span></div>
          <button type="button" onClick={animateMorph}><Play size={12} /> PLAY TRANSITION</button>
        </div>
      )}

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
                runState === "solving" || draftStatus === "checking"
                  ? "validation-badge solving"
                  : hasFailures || draftStatus === "invalid" || cloudState?.status === "failed"
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
            : draftStatus === "checking" ? "Hub moved · AWS rerouting and checking the ring"
            : draftStatus === "valid" ? "Edited candidate · AWS reroute and checker passed"
            : draftStatus === "invalid" ? "Edited candidate · independent checker found violations"
            : draftStatus === "preview" ? "Edited candidate · browser preview checks only"
            : cloudState?.status === "failed"
              ? `AWS solver error · ${cloudState.error}`
              : isBackend
                ? (overlayDraw?.metrics ? "Score and checks from the backend overlay" : "Load the matching overlay for the full score and checks")
                : "Browser estimate · load an overlay for independent checks"}
        </div>
        {draftMessage && <p className={draftStatus === "invalid" ? "edit-feedback invalid" : "edit-feedback"} role="status">{draftMessage}</p>}

        {cloudRunning ? <div className="solver-skeleton" role="status" aria-label="Solver working"><i /><i /><div><i /><i /><i /><i /></div><i /></div> :
        <div className="cost-card">
          <div className="cost-formula">
            <span>SCORE</span>
            <em>J = {scoreWeights?.L ?? 1}L + {scoreWeights?.X ?? 5}X + {scoreWeights?.O ?? 1}O + {scoreWeights?.B ?? 0.5}B</em>
          </div>
          <strong>{scoredMetrics ? scoredMetrics.J.toFixed(2) : "—"}</strong>
          <div className="cost-grid">
            {[
              { k: "L", v: metrics ? `${metrics.L.toFixed(2)} m` : "—", n: "cable length" },
              { k: "X", v: scoredMetrics ? `${scoredMetrics.X}` : "—", n: `crossings · ${scoreWeights?.X ?? 5} each` },
              { k: "O", v: scoredMetrics ? `${scoredMetrics.O.toFixed(2)} m` : "—", n: "shared cable length" },
              { k: "B", v: scoredMetrics ? `${scoredMetrics.B}` : "—", n: `bends · ${scoreWeights?.B ?? 0.5} each` },
            ].map((item) => (
              <div key={item.k} className="cost-cell">
                <span>{item.k}</span>
                <strong>{item.v}</strong>
                <small>{item.n}</small>
              </div>
            ))}
          </div>
          <div className="cost-extra">
            <span>HUBS <strong>{panelSolution?.hubs.length ?? "—"}</strong> / LOWER BOUND <strong>{metrics?.hubLowerBound ?? "—"}</strong></span>
            <span>AREA GAP MAX−MIN <strong>{scoredMetrics ? `${scoredMetrics.areaGap.toFixed(1)} m²` : "—"}</strong></span>
          </div>
        </div>}

        {cloudState && <CostHistory events={cloudState.events} />}

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
          {ruleChecks.map((check) => (
            <div key={check.id} className={check.na ? "check-row na" : check.pass ? "check-row pass" : "check-row fail"}>
              {check.na ? <Minus size={14} /> : check.pass ? <Check size={14} /> : <X size={14} />}
              <div><span>{check.label}</span><small>{check.detail}</small></div>
              <strong>{check.value}</strong>
            </div>
          ))}
        </div>

        <div className="load-section">
          <div className="load-head"><span>HUB LOAD / AREA</span><span>MAX 5 NODES · 100 M²</span></div>
          {(panelSolution?.hubs ?? []).map((hub, index) => (
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
          <div><span>{selected.zone}</span><strong>{draftStatus === "invalid" ? "VIOLATIONS" : draftStatus === "checking" ? "CHECKING" : draftStatus === "valid" || (!draftSolution && isBackend && overlayDraw?.valid === true) ? "CHECKED" : "PREVIEW"}</strong></div>
        </aside>
      )}
      <div className="view-toolbar" role="toolbar" aria-label="Model controls">
        <button
          type="button"
          className={modelView ? "model-view-toggle active" : "model-view-toggle"}
          aria-pressed={modelView}
          aria-label={modelView ? "Exit model view" : "Enter model view"}
          title={modelView ? "Exit model view" : "Model view"}
          onClick={() => {
            if (toolbarMenuRef.current) toolbarMenuRef.current.open = false;
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
        <button type="button" className={showHeatmap ? "active" : ""} aria-label="Toggle cable density heatmap" title="Cable density heatmap" aria-pressed={showHeatmap} onClick={() => setShowHeatmap((value) => !value)}>
          <Activity size={16} /><span>HEATMAP</span>
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
        <details className="toolbar-more" ref={toolbarMenuRef}>
          <summary aria-label="More model actions" title="More model actions"><MoreHorizontal size={17} /><span>MORE</span></summary>
          <div className="toolbar-menu" onClick={() => { if (toolbarMenuRef.current) toolbarMenuRef.current.open = false; }}>
            {draftSolution && <button type="button" onClick={() => { draftControllerRef.current?.abort(); setDraftSolution(null); setDraftStatus(null); setDraftMessage(""); }}><RotateCcw size={15} /><span>Reset hub edit</span></button>}
            {hasSelectedPlan && activeSolution && <button type="button" onClick={() => window.print()} disabled={isBackend && !overlayDraw?.metrics} title={isBackend && !overlayDraw?.metrics ? "Load the matching checker overlay for a complete report" : "Export PDF report"}><FileDown size={15} /><span>PDF report</span></button>}
            <button type="button" onClick={() => setDrawerOpen((value) => !value)}><Braces size={15} /><span>{drawerOpen ? "Close output JSON" : "View output JSON"}</span></button>
            {selected && <button type="button" onClick={() => setSelected(null)}><ScanLine size={15} /><span>Clear selection</span></button>}
          </div>
        </details>
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
      {hasSelectedPlan && plan && activeSolution && (!isBackend || overlayDraw?.metrics) && <SolutionReport plan={plan} solution={activeSolution} source={draftStatus === "invalid" ? "Edited candidate with rule violations" : draftStatus === "valid" ? "AWS rerouted and checked hub edit" : draftSolution ? "Edited browser preview" : isBackend ? "Backend checked result" : "Browser preview"} weights={scoreWeights} />}
    </div>
  );
}
