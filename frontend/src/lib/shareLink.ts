import type { Solution } from "./optimizer";
import type { OverlayDraw } from "./overlay";
import type { CloudEvent } from "./cloudApi";

export type SharedView = {
  v: 1;
  key: string;
  label: string;
  plan: Record<string, unknown>;
  official: { data: Record<string, unknown>; name: string } | null;
  overlay: OverlayDraw | null;
  baseline: Record<string, unknown> | null;
  events: CloudEvent[];
  draft: Solution | null;
  draftStatus: "valid" | "invalid" | "preview" | null;
  view: { zones: boolean; routes: boolean; heatmap: boolean; wallSlice: number; zoom: number };
};

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  for (let index = 0; index < bytes.length; index += 8192) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 8192));
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64Url(text: string): Uint8Array {
  const binary = atob(text.replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

export async function createShareLink(view: SharedView): Promise<string> {
  const json = new TextEncoder().encode(JSON.stringify(view));
  const compressed = typeof CompressionStream === "undefined" ? json :
    new Uint8Array(await new Response(new Blob([json]).stream().pipeThrough(new CompressionStream("gzip"))).arrayBuffer());
  const prefix = typeof CompressionStream === "undefined" ? "plain" : "gzip";
  const fragment = `share=${prefix}.${base64Url(compressed)}`;
  if (fragment.length > 80000) throw new Error("This result is too large for a reliable URL. Export the JSON or PDF instead.");
  return `${window.location.origin}${window.location.pathname}${window.location.search}#${fragment}`;
}

export async function readShareLink(hash: string): Promise<SharedView | null> {
  if (!hash.startsWith("#share=")) return null;
  const match = /^#share=(gzip|plain)\.([A-Za-z0-9_-]+)$/.exec(hash);
  if (!match || match[2].length > 80000) throw new Error("The share link is invalid or too large.");
  const bytes = fromBase64Url(match[2]);
  const buffer = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(buffer).set(bytes);
  const decoded = match[1] === "gzip"
    ? await new Response(new Blob([buffer]).stream().pipeThrough(new DecompressionStream("gzip"))).text()
    : new TextDecoder().decode(bytes);
  if (decoded.length > 4_000_000) throw new Error("The shared result is too large.");
  const value = JSON.parse(decoded) as SharedView;
  if (value.v !== 1 || typeof value.key !== "string" || !value.plan || typeof value.plan !== "object" || Array.isArray(value.plan) ||
      !Array.isArray(value.events) || !value.view || typeof value.view.zones !== "boolean" || typeof value.view.routes !== "boolean" ||
      typeof value.view.heatmap !== "boolean" || !Number.isFinite(value.view.wallSlice) || !Number.isFinite(value.view.zoom) ||
      (value.official && (!value.official.data || !Array.isArray(value.official.data.hubs))) ||
      (value.draft && !Array.isArray(value.draft.hubs))) throw new Error("The share link does not contain a valid NodeRithm result.");
  return value;
}
