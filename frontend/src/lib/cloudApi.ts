/** Browser client for the SAM HTTP API in docs/API.md. */

const apiUrl = (import.meta.env.VITE_OTHELLO_API_URL ?? "").trim().replace(/\/+$/, "");
const apiKey = (import.meta.env.VITE_OTHELLO_API_KEY ?? "").trim();

export const cloudAvailable = /^https?:\/\/[^/]+/.test(apiUrl);

export type CloudEvent = {
  stage: string;
  message: string;
  elapsed_s?: number;
  candidate_kind?: "baseline" | "incumbent";
  metrics?: { L: number; X: number; O: number; B: number; J: number };
  weights?: { L: number; X: number; O: number; B: number };
};

export type CloudProgress = {
  jobId: string;
  status: string;
  events: CloudEvent[];
};

export type CandidateValidation = {
  valid: boolean;
  metrics: { L: number; X: number; O: number; B: number; J: number; area_gap_m2: number } | null;
  checks: Array<{ rule_id: string; label: string; passed: boolean; value: string; detail: string }>;
  violations: Array<{ type: string; detail: string; hub_id?: string | null }>;
};

type JobReply = {
  job_id: string;
  status: string;
  error?: { code?: string; message?: string; hint?: string } | null;
  result?: { notes?: string[] } | null;
  files?: Record<string, string>;
};

type EventsReply = {
  status: string;
  done: boolean;
  events: CloudEvent[];
  next: number;
};

const headers = (): HeadersInit => ({
  ...(apiKey ? { "x-api-key": apiKey } : {}),
});

async function getJson<T>(path: string, signal: AbortSignal, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiUrl}${path}`, {
    ...init,
    headers: { ...headers(), ...init?.headers },
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string; hint?: string } } | null;
    const problem = body?.error;
    throw new Error([problem?.message ?? `API request failed (${response.status})`, problem?.hint].filter(Boolean).join(" — "));
  }
  return response.json() as Promise<T>;
}

function waitForPoll(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason);
      return;
    }
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, 1000);
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(signal.reason);
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export function isCloudPlan(plan: Record<string, unknown>): boolean {
  const inner = plan.inner;
  const geometry = inner && typeof inner === "object" && !Array.isArray(inner)
    ? inner as Record<string, unknown>
    : null;
  return typeof plan.area === "number" && plan.area > 0 && (
    typeof inner === "string" ||
    (geometry !== null && typeof geometry.type === "string" &&
      ["Polygon", "MultiPolygon", "GeometryCollection"].includes(geometry.type))
  );
}

export async function validateCloudEdit(jobId: string, output: Record<string, unknown>, signal: AbortSignal): Promise<CandidateValidation> {
  const candidate = { ...output, reported_metrics: {
    hub_count: (output.hubs as unknown[]).length,
    total_length_m: (output.reported_metrics as { total_length_m?: number })?.total_length_m ?? 0,
  } };
  return getJson<CandidateValidation>(`/api/jobs/${jobId}/validate`, signal, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(candidate),
  });
}

export async function solveCloudPlan(
  plan: Record<string, unknown>,
  settings: Record<string, unknown> | null,
  signal: AbortSignal,
  onProgress: (progress: CloudProgress) => void,
): Promise<{ official: Record<string, unknown>; officialText: string; overlay: Record<string, unknown>; baseline: Record<string, unknown> | null; baselineMetrics: CloudEvent["metrics"] | null; jobId: string }> {
  if (!cloudAvailable) throw new Error("Set VITE_OTHELLO_API_URL before building the website.");
  const payload = settings ? { plan, settings } : plan;
  const started = await getJson<JobReply>("/api/jobs", signal, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const jobId = started.job_id;
  onProgress({ jobId, status: started.status, events: [] });

  const cancel = () => {
    void fetch(`${apiUrl}/api/jobs/${jobId}/cancel`, {
      method: "POST",
      headers: headers(),
      keepalive: true,
    }).catch(() => undefined);
  };
  if (signal.aborted) cancel();
  else signal.addEventListener("abort", cancel, { once: true });

  try {
    let next = 0;
    let done = false;
    let baselineMetrics: CloudEvent["metrics"] | null = null;
    while (!done) {
      const page = await getJson<EventsReply>(`/api/jobs/${jobId}/events?after=${next}`, signal);
      next = page.next;
      done = page.done;
      baselineMetrics ??= page.events.find((event) => event.candidate_kind === "baseline")?.metrics ?? null;
      onProgress({ jobId, status: page.status, events: page.events });
      if (!done) await waitForPoll(signal);
    }

    const job = await getJson<JobReply>(`/api/jobs/${jobId}`, signal);
    if (job.status !== "succeeded") {
      throw new Error(job.error?.message ?? job.result?.notes?.join(" ") ?? `Solver ended with status: ${job.status}`);
    }
    if (!job.files?.official || !job.files?.overlay) {
      throw new Error("The solver finished without its official and overlay result files.");
    }
    const [official, overlay, baseline] = await Promise.all([
      getJson<Record<string, unknown>>(`/api/jobs/${jobId}/result/official`, signal),
      getJson<Record<string, unknown>>(`/api/jobs/${jobId}/result/overlay`, signal),
      getJson<Record<string, unknown>>(`/api/jobs/${jobId}/result/baseline`, signal).catch(() => null),
    ]);
    if (baseline && !baselineMetrics && !signal.aborted) {
      try {
        const checked = await validateCloudEdit(jobId, baseline, signal);
        if (checked.valid && checked.metrics) {
          const { L, X, O, B, J } = checked.metrics;
          baselineMetrics = { L, X, O, B, J };
        }
      } catch {
        // Older API deployments may not support candidate validation.
      }
    }
    return { official, officialText: JSON.stringify(official, null, 2), overlay, baseline, baselineMetrics, jobId };
  } finally {
    signal.removeEventListener("abort", cancel);
  }
}
