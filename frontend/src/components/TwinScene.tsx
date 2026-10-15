import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import {
  CSS2DObject,
  CSS2DRenderer,
  EffectComposer,
  OrbitControls,
  RenderPass,
  UnrealBloomPass,
} from "three-stdlib";
import type { PlanModel, Vec2 } from "../lib/resplan";
import type { Solution } from "../lib/optimizer";

gsap.registerPlugin(ScrollTrigger);

export type Inspection = {
  id: string;
  title: string;
  type: "room" | "node" | "hub" | "door";
  zone: string;
  detail: string;
  coords: string;
  hub?: string;
};

export type CameraPresetId = "top" | "iso" | "zone1" | "zone2";
export type CameraPresetRequest = { id: CameraPresetId; token: number };

type TwinSceneProps = {
  plan: PlanModel;
  solution: Solution;
  showZones: boolean;
  showRoutes: boolean;
  wallHeight: number;
  optimizeSignal: number;
  preset: CameraPresetRequest;
  focusHub: string | null;
  onInspect: (item: Inspection | null) => void;
  onPhaseChange: (phase: number) => void;
};

const WALL_H = 2.2;
const HUB_COLORS = ["#ff3b30", "#ff9f0a", "#30d158", "#64d2ff", "#bf5af2"];
const CONDUIT_COLORS = ["#4cc9f0", "#ff9f0a", "#30d158", "#64d2ff", "#bf5af2"];

const zoneFill = (index: number) => ["#2563eb", "#f59e0b", "#10b981", "#8b5cf6", "#f43f5e"][index % 5];

function makeLabel(text: string, className: string) {
  const element = document.createElement("div");
  element.className = className;
  element.textContent = text;
  const label = new CSS2DObject(element);
  label.center.set(0.5, 0.5);
  return label;
}

function hazardTexture() {
  const canvas = document.createElement("canvas");
  canvas.width = 96;
  canvas.height = 96;
  const ctx = canvas.getContext("2d");
    if (ctx) {
    ctx.fillStyle = "#1a2430";
    ctx.fillRect(0, 0, 96, 96);
    ctx.strokeStyle = "#2a3848";
    ctx.lineWidth = 12;
    for (let x = -96; x < 192; x += 34) {
      ctx.beginPath();
      ctx.moveTo(x, 96);
      ctx.lineTo(x + 96, 0);
      ctx.stroke();
    }
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(2, 2);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function laserGrid(extent: number, step: number, color: string, opacity: number) {
  const positions: number[] = [];
  for (let value = -extent; value <= extent + 1e-6; value += step) {
    positions.push(-extent, 0, value, extent, 0, value, value, 0, -extent, value, 0, extent);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  const material = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    uniforms: {
      uColor: { value: new THREE.Color(color) },
      uOpacity: { value: opacity },
      uRadius: { value: extent },
    },
    vertexShader: `varying float vDist; void main(){ vDist = length(position.xz); gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
    fragmentShader: `uniform vec3 uColor; uniform float uOpacity; uniform float uRadius; varying float vDist;
      void main(){ float fade = 1.0 - smoothstep(uRadius * 0.16, uRadius * 0.9, vDist); float a = uOpacity * fade; if (a < 0.004) discard; gl_FragColor = vec4(uColor, a); }`,
  });
  return new THREE.LineSegments(geometry, material);
}

type Pulse = { meshes: THREE.Mesh[]; points: THREE.Vector3[]; lengths: number[]; total: number; speed: number };

export default function TwinScene({
  plan,
  solution,
  showZones,
  showRoutes,
  wallHeight,
  optimizeSignal,
  preset,
  focusHub,
  onInspect,
  onPhaseChange,
}: TwinSceneProps) {
  const mountRef = useRef<HTMLDivElement>(null);
  const zoneGroupRef = useRef<THREE.Group | null>(null);
  const routeGroupRef = useRef<THREE.Group | null>(null);
  const wallPartsRef = useRef<THREE.Object3D[]>([]);
  const hubGroupsRef = useRef<THREE.Group[]>([]);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const bloomRef = useRef<UnrealBloomPass | null>(null);
  const zoneMaterialsRef = useRef<Record<string, THREE.Material[]>>({});

  const frame = useMemo(() => {
    const span = Math.max(plan.bounds.size[0], plan.bounds.size[1]);
    const center = new THREE.Vector3(0, 0, 0);
    const toWorld = (p: Vec2, y = 0.16) => new THREE.Vector3(p[0] - plan.bounds.center[0], y, p[1] - plan.bounds.center[1]);
    return { span, center, toWorld };
  }, [plan]);

  const presets = useMemo(() => {
    const d = frame.span;
    const hubOne = solution.hubs[0]?.location;
    const hubTwo = solution.hubs[1]?.location ?? solution.hubs[0]?.location;
    const focus = (point: Vec2 | undefined, fallback: Vec2): { position: [number, number, number]; target: [number, number, number] } => {
      const world = frame.toWorld(point ?? fallback, 0);
      return {
        position: [world.x * 0.55, 6.6, world.z * 0.55 + 6.4],
        target: [world.x, 0.15, world.z],
      };
    };
    return {
      top: { position: [0, d * 1.55, 0.001] as [number, number, number], target: [0, 0, 0] as [number, number, number] },
      iso: { position: [d * 0.85, d * 0.8, d * 0.95] as [number, number, number], target: [0, 0.2, 0] as [number, number, number] },
      zone1: focus(hubOne, plan.bounds.center),
      zone2: focus(hubTwo, plan.bounds.center),
    };
  }, [frame, plan.bounds.center, solution.hubs]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#02040a");
    scene.fog = new THREE.FogExp2("#02040a", 0.72 / Math.max(frame.span * 1.6, 18));

    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, frame.span * 12);
    camera.position.set(...presets.iso.position);
    cameraRef.current = camera;

    const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.28;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.domElement.className = "twin-canvas";
    mount.appendChild(renderer.domElement);

    const labelRenderer = new CSS2DRenderer();
    labelRenderer.domElement.className = "label-layer";
    labelRenderer.domElement.style.pointerEvents = "none";
    mount.appendChild(labelRenderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.055;
    controls.minDistance = 3;
    controls.maxDistance = frame.span * 3.4;
    controls.maxPolarAngle = Math.PI * 0.495;
    controls.target.set(...presets.iso.target);
    controlsRef.current = controls;

    const composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.72, 0.38, 0.88);
    composer.addPass(bloom);
    bloomRef.current = bloom;

    /* ------------------------------------------------------- lighting rig */
    scene.add(new THREE.HemisphereLight("#c8d9f0", "#0a1420", 1.35));
    const sun = new THREE.DirectionalLight("#ffffff", 3.4);
    sun.position.set(frame.span * 0.55, frame.span * 1.55, frame.span * 0.7);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.radius = 6;
    sun.shadow.bias = -0.0006;
    const shadowSpan = frame.span * 1.3;
    sun.shadow.camera.left = -shadowSpan;
    sun.shadow.camera.right = shadowSpan;
    sun.shadow.camera.top = shadowSpan;
    sun.shadow.camera.bottom = -shadowSpan;
    sun.shadow.camera.near = 1;
    sun.shadow.camera.far = frame.span * 4;
    scene.add(sun);

    const fill = new THREE.DirectionalLight("#a8c4e8", 1.4);
    fill.position.set(-frame.span * 0.8, frame.span * 0.9, -frame.span * 0.4);
    scene.add(fill);

    solution.hubs.forEach((hub, index) => {
      const light = new THREE.PointLight(
        index === 0 ? "#60a5fa" : index === 1 ? "#fbbf24" : "#34d399",
        10 / (1 + index * 0.3),
        frame.span * 1.6,
        2,
      );
      light.position.copy(frame.toWorld(hub.location, 2.8));
      scene.add(light);
    });
    const kicker = new THREE.PointLight("#7dd3fc", 5, frame.span * 2.8, 2);
    kicker.position.set(-frame.span * 0.5, 5.2, frame.span * 0.6);
    scene.add(kicker);

    /* -------------------------------------------------- reflective ground */
    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(frame.span * 9, frame.span * 9),
      new THREE.MeshStandardMaterial({ color: "#0a1018", roughness: 0.55, metalness: 0.22 }),
    );
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -0.12;
    ground.receiveShadow = true;
    scene.add(ground);
    const minor = laserGrid(frame.span * 1.5, 0.5, "#1e3a4f", 0.18);
    const major = laserGrid(frame.span * 1.5, 2, "#2d5a78", 0.32);
    minor.position.y = -0.105;
    major.position.y = -0.1;
    scene.add(minor, major);

    const planGroup = new THREE.Group();
    planGroup.rotation.y = -0.035;
    scene.add(planGroup);
    const zoneGroup = new THREE.Group();
    const architectureGroup = new THREE.Group();
    const hardwareGroup = new THREE.Group();
    const routeGroup = new THREE.Group();
    planGroup.add(zoneGroup, architectureGroup, hardwareGroup, routeGroup);
    zoneGroupRef.current = zoneGroup;
    routeGroupRef.current = routeGroup;

    /* ------------------------------------------------- procedural slabs */
    const hatch = hazardTexture();
    const pickables: THREE.Object3D[] = [];
    const zoneMaterials: Record<string, THREE.Material[]> = {};
    const hubIndexById = new Map(solution.hubs.map((hub, index) => [hub.hub_id, index]));

    plan.rooms.forEach((room) => {
      const shape = new THREE.Shape(room.polygon.map(([x, y]) => new THREE.Vector2(x, y)));
      const geometry = new THREE.ExtrudeGeometry(shape, { depth: 0.1, bevelEnabled: false });
      geometry.rotateX(Math.PI / 2);
      const zoneIndex = room.hubId ? hubIndexById.get(room.hubId) ?? 0 : undefined;
      const color = room.eligible ? zoneFill(zoneIndex ?? 0) : "#182029";
      const material = room.eligible
        ? new THREE.MeshPhysicalMaterial({
            color: new THREE.Color(color).lerp(new THREE.Color("#dbeafe"), 0.18),
            emissive: new THREE.Color(color).multiplyScalar(0.35),
            emissiveIntensity: 0.55,
            transparent: true,
            opacity: 0.42,
            roughness: 0.28,
            metalness: 0.08,
            transmission: 0.55,
            thickness: 0.35,
            clearcoat: 0.9,
            clearcoatRoughness: 0.18,
            depthWrite: false,
          })
        : new THREE.MeshStandardMaterial({ color: "#1a2430", map: hatch, roughness: 0.8, metalness: 0.08, transparent: true, opacity: 0.55 });

      const slab = new THREE.Mesh(geometry, material);
      slab.position.copy(frame.toWorld([0, 0], 0));
      slab.receiveShadow = true;
      const zoneKey = room.hubId ?? "EXCLUDED";
      const detail = room.eligible
        ? `${room.areaM2.toFixed(1)} m² · node ${room.id.replace("R", "N")}`
        : `${room.areaM2.toFixed(1)} m² · ineligible type "${room.type}"`;
      slab.userData.inspection = {
        id: room.id,
        title: room.label.toUpperCase(),
        type: "room",
        zone: room.hubId ? `ZONE ${room.hubId.slice(1).padStart(2, "0")}` : "EXCLUDED",
        detail,
        coords: `X ${room.node[0].toFixed(2)} M / Y ${room.node[1].toFixed(2)} M`,
        hub: room.hubId,
      } satisfies Inspection;
      zoneGroup.add(slab);
      pickables.push(slab);
      (zoneMaterials[zoneKey] ??= []).push(material);

      const outlinePoints = room.polygon.map(([x, y]) => new THREE.Vector3(x, 0.105, y));
      outlinePoints.push(outlinePoints[0].clone());
      const outlineGeometry = new THREE.BufferGeometry().setFromPoints(outlinePoints);
      const outlineMaterial = new THREE.LineBasicMaterial({
        color: room.eligible ? new THREE.Color(color).lerp(new THREE.Color("#e0f2fe"), 0.45) : "#4b5d6e",
        transparent: true,
        opacity: room.eligible ? 0.95 : 0.45,
      });
      const outline = new THREE.Line(outlineGeometry, outlineMaterial);
      outline.position.copy(slab.position);
      zoneGroup.add(outline);
      (zoneMaterials[zoneKey] ??= []).push(outlineMaterial);

      const label = makeLabel(
        room.label.toUpperCase(),
        room.eligible ? "room-label" : "room-label room-label--inactive",
      );
      label.position.copy(frame.toWorld(room.node, 0.12));
      zoneGroup.add(label);
    });
    zoneMaterialsRef.current = zoneMaterials;

    /* ------------------------------------------- frosted BIM wall shell */
    const wallMaterial = new THREE.MeshPhysicalMaterial({
      color: "#7eb8d8",
      emissive: "#1a3a52",
      emissiveIntensity: 0.15,
      roughness: 0.12,
      metalness: 0.05,
      transmission: 0.82,
      thickness: 0.45,
      ior: 1.4,
      transparent: true,
      opacity: 0.28,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    const wallEdgeMaterial = new THREE.LineBasicMaterial({ color: "#7dd3fc", transparent: true, opacity: 0.85 });
    const capMaterial = new THREE.LineBasicMaterial({ color: "#bae6fd", transparent: true, opacity: 0.7 });
    const thresholdMaterial = new THREE.MeshBasicMaterial({ color: new THREE.Color(0.45, 2.2, 3.6), toneMapped: false });
    const clearanceMaterial = new THREE.MeshBasicMaterial({ color: "#7dd3fc", transparent: true, opacity: 0.12, side: THREE.DoubleSide, toneMapped: false });
    const wallParts: THREE.Object3D[] = [];

    const edges = new Map<string, { a: Vec2; b: Vec2 }>();
    plan.rooms.forEach((room) => {
      room.polygon.forEach((p, i) => {
        const q = room.polygon[(i + 1) % room.polygon.length];
        const key = [p, q]
          .map((pt) => `${pt[0].toFixed(2)},${pt[1].toFixed(2)}`)
          .sort()
          .join("|");
        if (!edges.has(key)) edges.set(key, { a: p, b: q });
      });
    });

    const addWallRun = (from: Vec2, to: Vec2) => {
      const length = Math.hypot(to[0] - from[0], to[1] - from[1]);
      if (length < 0.06) return;
      const horizontal = Math.abs(to[0] - from[0]) >= Math.abs(to[1] - from[1]);
      const center: Vec2 = [(from[0] + to[0]) / 2, (from[1] + to[1]) / 2];
      const geometry = new THREE.BoxGeometry(horizontal ? length : 0.12, WALL_H, horizontal ? 0.12 : length);
      const wall = new THREE.Mesh(geometry, wallMaterial);
      wall.position.copy(frame.toWorld(center, WALL_H / 2));
      wall.castShadow = false;
      wall.receiveShadow = false;
      wall.userData.baseY = WALL_H / 2;
      architectureGroup.add(wall);
      wallParts.push(wall);

      /* cyan edge outline matching reference glass walls */
      const edgeGeo = new THREE.EdgesGeometry(geometry);
      const edgeLines = new THREE.LineSegments(edgeGeo, wallEdgeMaterial);
      edgeLines.position.copy(wall.position);
      edgeLines.userData.baseY = WALL_H / 2;
      edgeLines.userData.isCap = true;
      architectureGroup.add(edgeLines);
      wallParts.push(edgeLines);

      const cap = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(from[0], WALL_H, from[1]),
          new THREE.Vector3(to[0], WALL_H, to[1]),
        ]),
        capMaterial,
      );
      cap.position.copy(frame.toWorld([0, 0], 0));
      cap.userData.baseY = WALL_H;
      cap.userData.isCap = true;
      architectureGroup.add(cap);
      wallParts.push(cap);
    };

    const addThreshold = (a: Vec2, b: Vec2) => {
      const center: Vec2 = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
      const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
      const horizontal = Math.abs(b[0] - a[0]) >= Math.abs(b[1] - a[1]);
      const plate = new THREE.Mesh(
        new THREE.BoxGeometry(horizontal ? length : 0.34, 0.022, horizontal ? 0.34 : length),
        thresholdMaterial,
      );
      plate.position.copy(frame.toWorld(center, 0.08));
      architectureGroup.add(plate);
      const clearance = new THREE.Mesh(new THREE.PlaneGeometry(length + 0.5, 0.9), clearanceMaterial);
      clearance.position.copy(frame.toWorld(center, 0.45));
      if (!horizontal) clearance.rotation.y = Math.PI / 2;
      clearance.userData.baseY = 0.45;
      clearance.userData.isClearance = true;
      architectureGroup.add(clearance);
      wallParts.push(clearance);
    };

    edges.forEach(({ a, b }) => {
      const doorSpans = plan.doors
        .map((door) => {
          const doorHalf = door.widthM / 2;
          const along = ((door.center[0] - a[0]) * (b[0] - a[0]) + (door.center[1] - a[1]) * (b[1] - a[1])) / (length2(a, b) || 1);
          if (along < -0.01 || along > 1.01) return null;
          const perp = Math.abs(distancePointSegment(door.center, a, b));
          if (perp > 0.4) return null;
          const margin = Math.min(doorHalf + 0.08, 0.7);
          return [Math.max(0, along - margin / Math.max(Math.hypot(b[0] - a[0], b[1] - a[1]), 0.001)), Math.min(1, along + margin / Math.max(Math.hypot(b[0] - a[0], b[1] - a[1]), 0.001))] as [number, number];
        })
        .filter(Boolean) as Array<[number, number]>;

      if (!doorSpans.length) {
        addWallRun(a, b);
        return;
      }
      doorSpans.sort((p, q) => p[0] - q[0]);
      let cursor = 0;
      doorSpans.forEach(([start, end]) => {
        if (start > cursor) addWallRun(lerpVec(a, b, cursor), lerpVec(a, b, start));
        addThreshold(lerpVec(a, b, start), lerpVec(a, b, end));
        cursor = end;
      });
      if (cursor < 1) addWallRun(lerpVec(a, b, cursor), b);
    });
    wallPartsRef.current = wallParts;

    /* --------------------------------------------------- hardware props */
    const hubMaterial = new THREE.MeshPhysicalMaterial({ color: "#8a9aab", metalness: 0.88, roughness: 0.22, clearcoat: 0.9, clearcoatRoughness: 0.1 });
    const darkMetal = new THREE.MeshStandardMaterial({ color: "#1a2430", metalness: 0.7, roughness: 0.35 });
    const hubGroups: THREE.Group[] = [];

    solution.hubs.forEach((hub, index) => {
      const group = new THREE.Group();
      group.position.copy(frame.toWorld(hub.location, 0.13));
      const led = new THREE.Color(HUB_COLORS[index % HUB_COLORS.length]).multiplyScalar(2.2);

      const housing = new THREE.Mesh(new THREE.BoxGeometry(0.72, 0.3, 0.72), hubMaterial);
      housing.position.y = 0.15;
      housing.castShadow = true;
      housing.userData.inspection = {
        id: hub.hub_id,
        title: `HUB ${hub.hub_id}`,
        type: "hub",
        zone: `ZONE ${String(index + 1).padStart(2, "0")}`,
        detail: `${hub.assigned_nodes.length} nodes · ${hub.area_m2.toFixed(1)} m² · ring ${hub.ring_length_m.toFixed(2)} m`,
        coords: `X ${hub.location[0].toFixed(2)} M / Y ${hub.location[1].toFixed(2)} M`,
        hub: hub.hub_id,
      } satisfies Inspection;
      group.add(housing);
      pickables.push(housing);

      const inset = new THREE.Mesh(new THREE.BoxGeometry(0.46, 0.32, 0.46), darkMetal);
      inset.position.y = 0.21;
      group.add(inset);
      const vent = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.03, 0.1), new THREE.MeshBasicMaterial({ color: led, toneMapped: false }));
      vent.position.set(0, 0.38, 0);
      group.add(vent);

      const ring = new THREE.Mesh(new THREE.TorusGeometry(0.46, 0.03, 10, 52), new THREE.MeshBasicMaterial({ color: led, toneMapped: false }));
      ring.rotation.x = Math.PI / 2;
      ring.position.y = 0.03;
      ring.userData.pulseRing = true;
      group.add(ring);

      const hubLabel = makeLabel(hub.hub_id, "entity-label entity-label--hub");
      hubLabel.position.y = 0.86;
      group.add(hubLabel);
      hardwareGroup.add(group);
      hubGroups.push(group);
    });
    hubGroupsRef.current = hubGroups;

    plan.eligibleRooms.forEach((room, index) => {
      const nodeId = `N${index + 1}`;
      const hubId = room.hubId ?? "H1";
      const zoneIndex = hubIndexById.get(hubId) ?? 0;
      const group = new THREE.Group();
      group.position.copy(frame.toWorld(room.node, 0.13));
      const base = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.24, 0.13, 18), darkMetal);
      base.position.y = 0.07;
      base.castShadow = true;
      group.add(base);
      const accent = new THREE.Color(zoneFill(zoneIndex)).multiplyScalar(2.4);
      const signal = new THREE.Mesh(
        new THREE.CylinderGeometry(0.1, 0.11, 0.22, 16),
        new THREE.MeshBasicMaterial({ color: accent, toneMapped: false }),
      );
      signal.position.y = 0.2;
      signal.userData.inspection = {
        id: nodeId,
        title: `NODE ${nodeId}`,
        type: "node",
        zone: `ZONE ${String(zoneIndex + 1).padStart(2, "0")}`,
        detail: `${room.label} · fixed input point · hub ${hubId}`,
        coords: `X ${room.node[0].toFixed(2)} M / Y ${room.node[1].toFixed(2)} M`,
        hub: hubId,
      } satisfies Inspection;
      group.add(signal);
      pickables.push(signal);
      const halo = new THREE.Mesh(
        new THREE.RingGeometry(0.26, 0.31, 32),
        new THREE.MeshBasicMaterial({ color: zoneFill(zoneIndex), transparent: true, opacity: 0.7, side: THREE.DoubleSide, toneMapped: false }),
      );
      halo.rotation.x = -Math.PI / 2;
      halo.position.y = 0.03;
      group.add(halo);
      const label = makeLabel(nodeId, "entity-label entity-label--node");
      label.position.y = 0.66;
      group.add(label);
      hardwareGroup.add(group);
    });

    /* --------------------------------------------- rectilinear conduits */
    const routeMaterials: THREE.Material[] = [];
    const pulses: Pulse[] = [];
    const bracketMaterial = new THREE.MeshStandardMaterial({ color: "#3b4857", metalness: 0.85, roughness: 0.32 });

    solution.hubs.forEach((hub, hubIdx) => {
      const conduitHex = CONDUIT_COLORS[hubIdx % CONDUIT_COLORS.length];
      const trayMaterial = new THREE.MeshPhysicalMaterial({
        color: new THREE.Color(conduitHex).lerp(new THREE.Color("#1e293b"), 0.55),
        metalness: 0.55,
        roughness: 0.35,
        clearcoat: 0.4,
        transparent: true,
        opacity: 0.72,
      });
      const coreColor = new THREE.Color(conduitHex).multiplyScalar(2.1);
      const coreMaterial = new THREE.MeshBasicMaterial({ color: coreColor, transparent: true, opacity: 1, toneMapped: false });
      const glowMaterial = new THREE.MeshBasicMaterial({ color: coreColor, transparent: true, opacity: 0.22, toneMapped: false });
      routeMaterials.push(trayMaterial, coreMaterial, glowMaterial);

      hub.route_segments.forEach((segment) => {
        const group = new THREE.Group();
        const points = segment.polyline.map(([x, y]) => frame.toWorld([x, y], segment.laneY));
        for (let i = 0; i < points.length - 1; i += 1) {
          const start = points[i];
          const end = points[i + 1];
          const length = start.distanceTo(end);
          if (length < 1e-3) continue;
          const horizontal = Math.abs(end.x - start.x) >= Math.abs(end.z - start.z);
          const mid = start.clone().add(end).multiplyScalar(0.5);
          const tray = new THREE.Mesh(new THREE.BoxGeometry(horizontal ? length : 0.1, 0.055, horizontal ? 0.1 : length), trayMaterial);
          tray.position.copy(mid);
          tray.castShadow = false;
          group.add(tray);
          const core = new THREE.Mesh(new THREE.BoxGeometry(horizontal ? length : 0.042, 0.028, horizontal ? 0.042 : length), coreMaterial);
          core.position.copy(mid).setY(mid.y + 0.04);
          group.add(core);
          const halo = new THREE.Mesh(new THREE.BoxGeometry(horizontal ? length : 0.14, 0.11, horizontal ? 0.14 : length), glowMaterial);
          halo.position.copy(core.position);
          group.add(halo);
        }
        for (let i = 1; i < points.length - 1; i += 1) {
          const elbow = new THREE.Mesh(new THREE.CylinderGeometry(0.065, 0.065, 0.06, 8), trayMaterial);
          elbow.rotation.y = Math.PI / 6;
          elbow.position.copy(points[i]);
          elbow.castShadow = false;
          group.add(elbow);
          const elbowCore = new THREE.Mesh(new THREE.SphereGeometry(0.035, 8, 8), coreMaterial);
          elbowCore.position.copy(points[i]).setY(points[i].y + 0.04);
          group.add(elbowCore);
        }
        routeGroup.add(group);
      });

      /* single-door dead ends get visible dual-slot brackets */
      plan.rooms
        .filter((room) => room.eligible && (plan.doorsOfRoom[room.id]?.length ?? 0) === 1)
        .forEach((room) => {
          const door = plan.doorsOfRoom[room.id][0];
          const bracket = new THREE.Group();
          bracket.position.copy(frame.toWorld(door.center, 0));
          bracket.rotation.y = Math.atan2(door.tangent[0], door.tangent[1]);
          const post = new THREE.Mesh(new THREE.BoxGeometry(0.07, 0.44, 0.07), bracketMaterial);
          post.position.y = 0.22;
          bracket.add(post);
          [0.16, 0.26, 0.36].forEach((slot) => {
            const arm = new THREE.Mesh(new THREE.BoxGeometry(0.36, 0.024, 0.075), bracketMaterial);
            arm.position.set(0.16, slot, 0);
            bracket.add(arm);
          });
          hardwareGroup.add(bracket);
        });

      /* ring-wide laser pulse with motion trail */
      const loop = hub.route_segments.flatMap((segment) => segment.polyline);
      if (loop.length > 2) {
        const points = loop.map(([x, y]) => frame.toWorld([x, y], 0.21));
        points.push(points[0].clone());
        const lengths: number[] = [];
        let total = 0;
        for (let i = 0; i < points.length - 1; i += 1) {
          const length = points[i].distanceTo(points[i + 1]);
          lengths.push(length);
          total += length;
        }
        const meshes: THREE.Mesh[] = [];
        for (let i = 0; i < 8; i += 1) {
          const intensity = 1 - i / 8;
          const material = new THREE.MeshBasicMaterial({
            color: new THREE.Color("#ffffff").lerp(new THREE.Color(CONDUIT_COLORS[hubIdx % CONDUIT_COLORS.length]), 0.45).multiplyScalar(0.9 + intensity * 1.4),
            transparent: true,
            opacity: 0.18 + intensity * 0.82,
            toneMapped: false,
          });
          const mesh = new THREE.Mesh(new THREE.BoxGeometry(0.13, 0.055, 0.13), material);
          routeGroup.add(mesh);
          meshes.push(mesh);
          routeMaterials.push(material);
        }
        pulses.push({ meshes, points, lengths, total, speed: 2.3 + hubIdx * 0.2 });
      }
    });

    /* -------------------------------------------------------- raycasting */
    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let hovered: THREE.Object3D | null = null;

    const updatePointer = (event: PointerEvent) => {
      const bounds = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - bounds.left) / bounds.width) * 2 - 1;
      pointer.y = -((event.clientY - bounds.top) / bounds.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const next = raycaster.intersectObjects(pickables, false)[0]?.object ?? null;
      if (hovered !== next) {
        hovered = next;
        renderer.domElement.style.cursor = hovered ? "crosshair" : "grab";
        mount.classList.toggle("has-pick", Boolean(hovered));
      }
    };
    const handlePick = () => {
      if (hovered?.userData.inspection) onInspect(hovered.userData.inspection as Inspection);
    };
    renderer.domElement.addEventListener("pointermove", updatePointer);
    renderer.domElement.addEventListener("pointerdown", handlePick);

    /* ------------------------------------------- scroll choreography */
    let currentPhase = -1;
    const timeline = gsap.timeline({
      scrollTrigger: {
        trigger: "#story",
        start: "top top",
        end: "bottom bottom",
        scrub: 1.1,
        onUpdate: (self) => {
          const next = Math.min(3, Math.floor(self.progress * 4));
          if (next !== currentPhase) {
            currentPhase = next;
            onPhaseChange(next);
          }
        },
      },
    });
    timeline
      .to(camera.position, { x: 0, y: presets.top.position[1], z: 0.001, duration: 0.9, ease: "power1.inOut" }, 0.45)
      .to(controls.target, { x: 0, y: 0, z: 0, duration: 0.9, ease: "power1.inOut" }, 0.45)
      .to(solution.hubs[0] ? {} : {}, { duration: 0.01 }, 0.5)
      .to(camera.position, { x: presets.zone1.position[0] + 3, y: 7.6, z: presets.zone1.position[2] + 5, duration: 1.05, ease: "power2.inOut" }, 1.42)
      .to(controls.target, { x: presets.zone1.target[0], y: 0.2, z: presets.zone1.target[2], duration: 1.05, ease: "power2.inOut" }, 1.42)
      .to(camera.position, { x: presets.iso.position[0] * 0.82, y: presets.iso.position[1] * 0.8, z: presets.iso.position[2] * 0.86, duration: 0.8, ease: "power1.inOut" }, 2.3);

    /* -------------------------------------------------------- run loop */
    const clock = new THREE.Clock();
    let frameId = 0;
    const animate = () => {
      const elapsed = clock.getElapsedTime();
      controls.update();
      hubGroups.forEach((group, index) => {
        const ring = group.children.find((child) => child.userData.pulseRing);
        if (ring) ring.scale.setScalar(1 + Math.sin(elapsed * 2.6 + index) * 0.085);
      });
      pulses.forEach((pulse) => {
        pulse.meshes.forEach((mesh, index) => {
          const position = pointOnPath(pulse, elapsed * pulse.speed - index * 0.16);
          mesh.position.copy(position);
          mesh.scale.setScalar(1 - index * 0.075);
        });
      });
      composer.render();
      labelRenderer.render(scene, camera);
      frameId = requestAnimationFrame(animate);
    };

    const resize = () => {
      const width = mount.clientWidth;
      const height = mount.clientHeight;
      camera.aspect = width / Math.max(height, 1);
      camera.updateProjectionMatrix();
      renderer.setSize(width, height, false);
      labelRenderer.setSize(width, height);
      composer.setSize(width, height);
    };
    resize();
    window.addEventListener("resize", resize);
    animate();
    ScrollTrigger.refresh();

    return () => {
      cancelAnimationFrame(frameId);
      window.removeEventListener("resize", resize);
      renderer.domElement.removeEventListener("pointermove", updatePointer);
      renderer.domElement.removeEventListener("pointerdown", handlePick);
      timeline.scrollTrigger?.kill();
      timeline.kill();
      controls.dispose();
      composer.dispose();
      renderer.dispose();
      hatch.dispose();
      scene.traverse((object) => {
        const mesh = object as THREE.Mesh;
        if (mesh.geometry) mesh.geometry.dispose();
        const material = (mesh as unknown as { material?: THREE.Material | THREE.Material[] }).material;
        if (material) (Array.isArray(material) ? material : [material]).forEach((item) => item.dispose());
      });
      mount.replaceChildren();
      zoneGroupRef.current = null;
      routeGroupRef.current = null;
      wallPartsRef.current = [];
      hubGroupsRef.current = [];
      zoneMaterialsRef.current = {};
    };
  }, [plan, solution, frame, presets, onInspect, onPhaseChange]);

  useEffect(() => {
    if (zoneGroupRef.current) zoneGroupRef.current.visible = showZones;
  }, [showZones]);

  useEffect(() => {
    if (routeGroupRef.current) routeGroupRef.current.visible = showRoutes;
  }, [showRoutes]);

  useEffect(() => {
    const slice = Math.min(1, Math.max(0.02, wallHeight));
    wallPartsRef.current.forEach((part) => {
      const baseY = (part.userData.baseY as number) ?? 0;
      if (part.userData.isCap || part.userData.isClearance) {
        gsap.to(part.position, { y: baseY * slice, duration: 0.22, overwrite: true });
      } else {
        gsap.to(part.scale, { y: slice, duration: 0.22, overwrite: true });
        gsap.to(part.position, { y: baseY * slice, duration: 0.22, overwrite: true });
      }
    });
  }, [wallHeight]);

  useEffect(() => {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls || preset.token === 0) return;
    const target = presets[preset.id];
    gsap.to(camera.position, { x: target.position[0], y: target.position[1], z: target.position[2], duration: 0.8, ease: "power3.inOut", overwrite: true });
    gsap.to(controls.target, { x: target.target[0], y: target.target[1], z: target.target[2], duration: 0.8, ease: "power3.inOut", overwrite: true });
  }, [preset, presets]);

  useEffect(() => {
    const zones = zoneMaterialsRef.current;
    const bloom = bloomRef.current;
    const focusIndex = focusHub ? Number(focusHub.replace(/\D/g, "")) - 1 : -1;
    Object.entries(zones).forEach(([zoneKey, materials]) => {
      const zoneIndex = zoneKey === "EXCLUDED" ? 99 : Number(zoneKey.replace(/\D/g, "")) - 1;
      const dim = focusIndex >= 0 && zoneIndex !== focusIndex;
      materials.forEach((material) => {
        const base = (material.userData.baseOpacity as number | undefined) ?? material.opacity;
        material.userData.baseOpacity = base;
        gsap.to(material, { opacity: dim ? base * 0.16 : base, duration: 0.45, overwrite: true });
      });
    });
    if (bloom) gsap.to(bloom, { strength: focusIndex >= 0 ? 0.95 : 0.72, duration: 0.45, overwrite: true });
  }, [focusHub]);

  useEffect(() => {
    if (optimizeSignal === 0 || !hubGroupsRef.current.length) return;
    hubGroupsRef.current.forEach((group, index) => {
      gsap.fromTo(group.scale, { x: 1, y: 1, z: 1 }, { x: 1.2, y: 1.2, z: 1.2, duration: 0.22, repeat: 3, yoyo: true, delay: index * 0.1 });
    });
  }, [optimizeSignal]);

  return <div ref={mountRef} className="scene-mount" aria-label="ResPlan procedural BIM digital twin" />;
}

function length2(a: Vec2, b: Vec2) {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  return dx * dx + dy * dy;
}

const lerpVec = (a: Vec2, b: Vec2, t: number): Vec2 => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];

function distancePointSegment(p: Vec2, a: Vec2, b: Vec2) {
  const len2 = length2(a, b) || 1e-9;
  let t = ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(p[0] - (a[0] + (b[0] - a[0]) * t), p[1] - (a[1] + (b[1] - a[1]) * t));
}

function pointOnPath(pulse: Pulse, distance: number) {
  let remaining = ((distance % pulse.total) + pulse.total) % pulse.total;
  for (let i = 0; i < pulse.lengths.length; i += 1) {
    if (remaining <= pulse.lengths[i]) {
      const ratio = pulse.lengths[i] === 0 ? 0 : remaining / pulse.lengths[i];
      return pulse.points[i].clone().lerp(pulse.points[i + 1], ratio);
    }
    remaining -= pulse.lengths[i];
  }
  return pulse.points[pulse.points.length - 1].clone();
}
