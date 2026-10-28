/**
 * Typed API client.
 *
 * - Talks to the FastAPI backend at NEXT_PUBLIC_API_URL (default `/v1` through
 *   the same origin proxy).
 * - Sends the session cookie automatically (`credentials: "include"`) and the
 *   double-submit CSRF header read from the `dl_csrf` cookie (D-07).
 * - Normalises every failure into `ApiError` so components have one shape.
 *
 * This module is safe to import from client components only; server components
 * should use `serverFetch` (same code path, cookie forwarded by Next).
 */

import type {
  AgentEvent,
  BudgetOut,
  AgentProposal,
  AgentRun,
  ApiError,
  ApiKey,
  ApiKeyCreated,
  AuditLog,
  Dataset,
  DatasetPreview,
  InstallationAdminGrant,
  InstallationLlmStatus,
  InstallationLlmTestResult,
  InstallationLlmUpdate,
  InstallationLlmUpdateResponse,
  LineageGraph,
  LlmCall,
  Membership,
  Pipeline,
  PipelineDAGDTO,
  PipelineVersion,
  Project,
  ProjectSettings,
  ProjectSummary,
  ProfileReport,
  Run,
  RunCreateResponse,
  RunDetail,
  SessionInfo,
  SqlAnswer,
  Source,
  UsageSummary,
  ValidationIssue,
} from "@/lib/types";
import { t } from "@/lib/i18n/id";
import { DemoHttpError, demoRequest } from "@/lib/demo/backend";
import { isDemoMode } from "@/lib/demo/mode";

export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "/v1";

/**
 * Sentinel prefix for routes that are never fetched over HTTP in demo mode
 * (the SSE event stream). `useRunStream` recognises it and replays a local
 * simulation instead of opening an `EventSource`.
 */
export const DEMO_STREAM_PREFIX = "demo-stream://";

export class ApiClientError extends Error implements ApiError {
  readonly code: string;
  readonly request_id: string;
  readonly status: number;
  readonly details?: unknown;

  constructor(message: string, code: string, status: number, requestId: string, details?: unknown) {
    super(message);
    this.name = "ApiClientError";
    this.code = code;
    this.status = status;
    this.request_id = requestId;
    this.details = details;
  }
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${name}=`));
  if (!match) return null;
  return decodeURIComponent(match.slice(name.length + 1));
}

type RequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
  query?: Record<string, string | number | boolean | undefined | null>;
  signal?: AbortSignal;
  /** Skip JSON parsing (204 responses). */
  raw?: boolean;
};

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const base = API_BASE.endsWith("/") ? API_BASE.slice(0, -1) : API_BASE;
  const url = `${base}${path.startsWith("/") ? path : `/${path}`}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";

  /*
   * Demo mode short-circuit.
   *
   * This must be the FIRST thing `apiFetch` does. Everything below builds a
   * real request; running any of it in demo mode would put the network back in
   * play and could reach the production backend. The demo backend is a pure
   * in-memory router, so a demo session structurally cannot mutate real data.
   */
  if (isDemoMode()) {
    try {
      return await demoRequest<T>(path, {
        method,
        body: options.body,
        query: options.query,
      });
    } catch (error) {
      if (error instanceof DemoHttpError) {
        throw new ApiClientError(error.message, error.code, error.status, error.request_id);
      }
      throw error;
    }
  }

  const headers: Record<string, string> = {
    Accept: "application/json",
    ...options.headers,
  };

  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  // CSRF double-submit for state-changing verbs.
  if (method !== "GET") {
    const csrf = readCookie("dl_csrf");
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers,
      credentials: "include",
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
      cache: "no-store",
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiClientError(
      "Tidak dapat terhubung ke server.",
      "network_error",
      0,
      "unknown",
    );
  }

  if (response.status === 204 || options.raw) {
    return undefined as T;
  }

  const text = await response.text();
  const payload = text ? (safeJson(text) as unknown) : undefined;

  if (!response.ok) {
    const envelope = payload as Partial<ApiError> | undefined;
    throw new ApiClientError(
      envelope?.message ?? "Terjadi kesalahan.",
      envelope?.code ?? "http_error",
      response.status,
      envelope?.request_id ?? response.headers.get("X-Request-ID") ?? "unknown",
      envelope?.details,
    );
  }

  return payload as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export type ErrorKind = "network" | "unauthorized" | "forbidden" | "not_found" | "generic";

/**
 * Classify an error for UI presentation. Pages use this to pick the right
 * state: a 404 is not "an error happened", it is "this thing does not exist
 * (or belongs to another tenant)". Cross-tenant access intentionally returns
 * 404 (FR-003), so the two share one message.
 */
export function classifyError(error: unknown): { kind: ErrorKind; message: string; requestId: string | null } {
  if (error instanceof ApiClientError) {
    if (error.status === 0 || error.code === "network_error") {
      return { kind: "network", message: t.errors.network, requestId: error.request_id };
    }
    if (error.status === 401) {
      return { kind: "unauthorized", message: t.errors.unauthorized, requestId: error.request_id };
    }
    if (error.status === 403) {
      return { kind: "forbidden", message: t.errors.forbidden, requestId: error.request_id };
    }
    if (error.status === 404 || error.code === "not_found") {
      return { kind: "not_found", message: t.errors.notFound, requestId: error.request_id };
    }
    return { kind: "generic", message: error.message, requestId: error.request_id };
  }
  if (error instanceof Error) {
    return { kind: "generic", message: error.message, requestId: null };
  }
  return { kind: "generic", message: t.errors.generic, requestId: null };
}

/** Typed endpoint map. One function per backend route (PRD 9.7). */
export const api = {
  auth: {
    register: (body: {
      email: string;
      password: string;
      name: string;
      org_name: string;
    }) => apiFetch<SessionInfo>("/auth/register", { method: "POST", body }),
    login: (body: { email: string; password: string }) =>
      apiFetch<SessionInfo>("/auth/login", { method: "POST", body }),
    logout: () => apiFetch<void>("/auth/logout", { method: "POST" }),
    me: () => apiFetch<SessionInfo>("/auth/me"),
  },

  projects: {
    list: () => apiFetch<ProjectSummary[]>("/projects"),
    get: (projectId: string) => apiFetch<Project>(`/projects/${projectId}`),
    create: (body: { name: string; description?: string | null }) =>
      apiFetch<Project>("/projects", { method: "POST", body }),
    update: (projectId: string, body: { name?: string; description?: string | null }) =>
      apiFetch<Project>(`/projects/${projectId}`, { method: "PATCH", body }),
    remove: (projectId: string, confirmName: string) =>
      apiFetch<void>(`/projects/${projectId}`, {
        method: "DELETE",
        query: { confirm_name: confirmName },
      }),
    settings: (projectId: string) => apiFetch<ProjectSettings>(`/projects/${projectId}/settings`),
    updateSettings: (projectId: string, body: ProjectSettings) =>
      apiFetch<ProjectSettings>(`/projects/${projectId}/settings`, { method: "PUT", body }),
  },

  sources: {
    list: (projectId: string) => apiFetch<Source[]>(`/projects/${projectId}/sources`),
    create: (
      projectId: string,
      body: { name: string; slug?: string; config: Record<string, unknown> },
    ) => apiFetch<Source>(`/projects/${projectId}/sources`, { method: "POST", body }),
    remove: (projectId: string, sourceId: string) =>
      apiFetch<void>(`/projects/${projectId}/sources/${sourceId}`, { method: "DELETE" }),
  },

  keys: {
    list: (projectId: string) => apiFetch<ApiKey[]>(`/projects/${projectId}/api-keys`),
    create: (projectId: string, name: string) =>
      apiFetch<ApiKeyCreated>(`/projects/${projectId}/api-keys`, {
        method: "POST",
        body: { name },
      }),
    rotate: (projectId: string, keyId: string) =>
      apiFetch<ApiKeyCreated>(`/projects/${projectId}/api-keys/${keyId}/rotate`, {
        method: "POST",
        body: { confirm: true },
      }),
    revoke: (projectId: string, keyId: string) =>
      apiFetch<void>(`/projects/${projectId}/api-keys/${keyId}`, { method: "DELETE" }),
  },

  uploads: {
    init: (
      projectId: string,
      body: { filename: string; content_type: string; size_bytes: number; source_id?: string },
    ) =>
      apiFetch<{
        upload_id: string;
        dataset_id: string;
        storage_key: string;
        parts: { part_number: number; url: string; headers: Record<string, string> }[];
        expires_at: string;
        max_bytes: number;
      }>(`/projects/${projectId}/uploads`, { method: "POST", body }),
    complete: (projectId: string, uploadId: string, checksum?: string) =>
      apiFetch<{ upload_id: string; status: string; storage_uri: string; size_bytes: number }>(
        `/projects/${projectId}/uploads/${uploadId}/complete`,
        { method: "POST", body: { etags: [], checksum_sha256: checksum ?? null } },
      ),
  },

  pipelines: {
    list: (projectId: string) => apiFetch<Pipeline[]>(`/projects/${projectId}/pipelines`),
    get: (pipelineId: string) => apiFetch<Pipeline>(`/pipelines/${pipelineId}`),
    create: (projectId: string, body: { name: string; description?: string; dag: PipelineDAGDTO }) =>
      apiFetch<Pipeline>(`/projects/${projectId}/pipelines`, { method: "POST", body }),
    updateDag: (pipelineId: string, body: { dag: PipelineDAGDTO; change_note?: string }) =>
      apiFetch<PipelineVersion>(`/pipelines/${pipelineId}`, { method: "PATCH", body }),
    versions: (pipelineId: string) =>
      apiFetch<PipelineVersion[]>(`/pipelines/${pipelineId}/versions`),
    validate: (dag: PipelineDAGDTO) =>
      apiFetch<{ valid: boolean; issues: ValidationIssue[]; topological_order: string[] }>(
        "/pipelines/validate",
        { method: "POST", body: { dag } },
      ),
    run: (pipelineId: string, body: { trigger?: string; idempotency_key?: string } = {}) =>
      apiFetch<RunCreateResponse>(`/pipelines/${pipelineId}/runs`, {
        method: "POST",
        body: { trigger: "manual", ...body },
      }),
    lineage: (projectId: string) =>
      apiFetch<LineageGraph>(`/projects/${projectId}/lineage`),
  },

  runs: {
    list: (projectId: string, status?: string) =>
      apiFetch<Run[]>(`/projects/${projectId}/runs`, { query: { status } }),
    get: (runId: string) => apiFetch<RunDetail>(`/runs/${runId}`),
    cancel: (runId: string) => apiFetch<{ run_id: string; status: string }>(`/runs/${runId}`, {
      method: "DELETE",
    }),
    eventsUrl: (runId: string) =>
      isDemoMode() ? `${DEMO_STREAM_PREFIX}${runId}` : buildUrl(`/runs/${runId}/events`),
  },

  datasets: {
    list: (projectId: string) => apiFetch<Dataset[]>(`/projects/${projectId}/datasets`),
    preview: (projectId: string, datasetId: string, limit = 1000) =>
      apiFetch<DatasetPreview>(`/projects/${projectId}/datasets/${datasetId}/preview`, {
        query: { limit },
      }),
    quality: (projectId: string, datasetId: string) =>
      apiFetch<{
        columns: {
          name: string;
          dtype: string;
          null_ratio: number;
          distinct_count: number;
          histogram: { bucket: string; count: number }[];
        }[];
        reasons: Record<string, number>;
      }>(`/projects/${projectId}/datasets/${datasetId}/quality`),
  },

  agents: {
    startProfile: (projectId: string, body: { dataset_id: string }) =>
      apiFetch<{ agent_run: AgentRun }>("/agents/profile", {
        method: "POST",
        body: { project_id: projectId, ...body },
      }),
    startBuilder: (projectId: string, body: { description: string; dataset_id?: string }) =>
      apiFetch<{ agent_run: AgentRun }>("/agents/build-pipeline", {
        method: "POST",
        body: { project_id: projectId, ...body },
      }),
    ask: (projectId: string, body: { question: string }) =>
      apiFetch<{ agent_run: AgentRun; answer: SqlAnswer }>("/agents/ask", {
        method: "POST",
        body: { project_id: projectId, ...body },
      }),
    getRun: (agentRunId: string) => apiFetch<AgentRun>(`/agent-runs/${agentRunId}`),
    events: (agentRunId: string) => apiFetch<AgentEvent[]>(`/agent-runs/${agentRunId}/events`),
    proposals: (agentRunId: string) =>
      apiFetch<AgentProposal[]>(`/agent-runs/${agentRunId}/proposals`),
profile: (agentRunId: string) =>
      apiFetch<ProfileReport>(`/agent-runs/${agentRunId}/profile`),
    apply: (agentRunId: string, pipelineId: string) =>
      apiFetch<{
        pipeline_id: string;
        pipeline_version_id: string;
        version: number;
        applied_proposals: number;
      }>(`/agent-runs/${agentRunId}/apply`, {
        method: "POST",
        body: { pipeline_id: pipelineId },
      }),
    decide: (
      proposalId: string,
      body: { decision: "approve" | "reject"; note?: string },
    ) =>
      apiFetch<AgentProposal>(`/proposals/${proposalId}/decision`, {
        method: "POST",
        body,
      }),
  },

  usage: {
    summary: (projectId?: string) =>
      apiFetch<UsageSummary>("/usage", { query: { project_id: projectId } }),
    calls: (projectId?: string) =>
      apiFetch<LlmCall[]>("/usage/llm-calls", { query: { project_id: projectId } }),
    /** Contract `BudgetUpdateRequest`: optional fields, PATCH semantics. */
    updateBudget: (body: { monthly_budget_usd?: number; per_run_token_limit?: number }) =>
      apiFetch<BudgetOut>("/usage/budget", { method: "PATCH", body }),
    queue: () =>
      apiFetch<
        { source_id: string; stream: string; length: number; pending: number; consumers: number; measured_at: string }[]
      >("/usage/queue"),
  },

  settings: {
    members: () => apiFetch<Membership[]>("/settings/members"),
    updateRole: (membershipId: string, role: string) =>
      apiFetch<Membership>(`/settings/members/${membershipId}`, {
        method: "PATCH",
        body: { role },
      }),
    removeMember: (membershipId: string) =>
      apiFetch<void>(`/settings/members/${membershipId}`, { method: "DELETE" }),
    audit: () => apiFetch<AuditLog[]>("/settings/audit-logs"),
  },

  /**
   * Installation scoped AI model configuration (PRD 9.7).
   *
   * Unlike every other group here these routes are NOT tenant scoped: one
   * configuration serves the whole deployment. The status payload therefore
   * never contains the API key, and `update` only sends the key when the
   * operator actually typed a new one.
   */
  installation: {
    status: () => apiFetch<InstallationLlmStatus>("/installation/llm"),
    update: (body: InstallationLlmUpdate) =>
      apiFetch<InstallationLlmUpdateResponse>("/installation/llm", { method: "PUT", body }),
    test: () =>
      apiFetch<InstallationLlmTestResult>("/installation/llm/test", {
        method: "POST",
        body: {},
      }),
    grantAdmin: (body: InstallationAdminGrant) =>
      apiFetch<void>("/installation/admins", { method: "POST", body }),
  },
};


