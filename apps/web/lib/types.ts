/**
 * Shared types mirrored from `packages/contracts/dataloom_contracts` (Pydantic).
 *
* The canonical source is the Python contract. These TypeScript mirrors keep
 * the UI typed; the generation command is documented in
 * packages/contracts/README.md and can replace these mirrors in CI.
 *
 * Rule: keep field names snake_case, identical to the wire format.
 */

export type Role = "owner" | "admin" | "editor" | "viewer";

export type SourceType = "webhook" | "csv" | "api" | "postgres";

export type Layer = "bronze" | "silver" | "gold" | "quarantine";

export type RunStatus =
  | "draft"
  | "validated"
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "retrying"
  | "needs_attention"
  | "cancelled";

export type RunTrigger = "manual" | "schedule" | "webhook" | "retry" | "self_heal";

export type NodeStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped"
  | "retrying"
  | "cancelled";

export type ProposalStatus = "pending" | "approved" | "rejected" | "superseded" | "expired";

export type RiskLevel = "low" | "medium" | "destructive";

export type NodeType = "source" | "validate" | "clean" | "transform" | "aggregate" | "sink";

export type CleaningOp =
  | "cast"
  | "trim"
  | "normalize_null"
  | "dedup"
  | "parse_date"
  | "lowercase"
  | "regex_replace"
  | "drop_if"
  | "fill_default";

export type AgentType =
  | "supervisor"
  | "schema_profiler"
  | "pipeline_builder"
  | "data_quality_guardian"
  | "self_healing"
  | "text_to_sql";

export type AgentRunStatus =
  | "pending"
  | "running"
  | "waiting_approval"
  | "succeeded"
  | "insufficient_evidence"
  | "failed"
  | "cancelled"
  | "budget_exceeded";

export type AgentOutputStatus = "ok" | "insufficient_evidence" | "needs_clarification" | "rejected";

/** Uniform error envelope (PRD 9.7). */
export interface ApiError {
  code: string;
  message: string;
  request_id: string;
  details?: unknown;
}

export interface User {
  id: string;
  email: string;
  name: string;
  created_at: string;
}

export interface Organization {
  id: string;
  name: string;
  plan: string;
  llm_budget_usd_month: number;
  created_at: string;
}

export interface Membership {
  id: string;
  org_id: string;
  user_id: string;
  role: Role;
  user_email: string;
  user_name: string;
  created_at: string;
}

export interface SessionInfo {
  user: User;
  org: Organization;
  membership: Membership;
  csrf_token: string;
  expires_at: string;
  /**
   * Deployment-wide installation admin flag. NOT an organization role: it gates
   * `/v1/installation/*` mutations. Defaults to false for older payloads.
   */
  is_install_admin?: boolean;
}

export interface ProjectSettings {
  max_quarantine_ratio: number;
  require_approval_for_destructive: boolean;
  freshness_threshold_minutes: number;
  batch_size_rows: number;
  node_timeout_seconds: number;
  run_timeout_seconds: number;
  max_retries_per_node: number;
  max_concurrent_runs: number;
  schedule_cron: string | null;
}

export interface Project {
  id: string;
  org_id: string;
  name: string;
  description: string | null;
  settings: ProjectSettings;
  created_at: string;
  updated_at: string;
}

export interface ProjectSummary {
  id: string;
  name: string;
  description: string | null;
  source_count: number;
  pipeline_count: number;
  last_run_status: RunStatus | null;
  updated_at: string;
}

export interface Source {
  id: string;
  org_id: string;
  project_id: string;
  name: string;
  slug: string;
  type: SourceType;
  status: string;
  config_public: Record<string, unknown>;
  ingest_path: string;
  last_received_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApiKey {
  id: string;
  org_id: string;
  project_id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface ApiKeyCreated {
  key: ApiKey;
  plaintext_key: string;
  warning: string;
}

/** DAG geometry kept 1:1 with PipelineNode / PipelineEdge. */
export interface PipelineNodeDTO {
  id: string;
  type: NodeType;
  label?: string | null;
  params: Record<string, unknown> & { kind: string };
  position?: { x: number; y: number } | null;
}

export interface PipelineEdgeDTO {
  id?: string | null;
  from: string;
  to: string;
}

export interface PipelineDAGDTO {
  schema_version: 1;
  nodes: PipelineNodeDTO[];
  edges: PipelineEdgeDTO[];
  metadata?: Record<string, unknown>;
}

export interface Pipeline {
  id: string;
  org_id: string;
  project_id: string;
  name: string;
  description: string | null;
  current_version_id: string | null;
  current_version: number | null;
  schedule_cron: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface ValidationIssue {
  loc: (string | number)[];
  msg: string;
  node_ref?: string | null;
}

export interface PipelineVersion {
  id: string;
  org_id: string;
  pipeline_id: string;
  version: number;
  dag: PipelineDAGDTO;
  validation_status: "draft" | "valid" | "invalid";
  validation_errors: ValidationIssue[];
  created_by_kind: string;
  created_by_user_id: string | null;
  created_by_agent_run_id: string | null;
  parent_version_id: string | null;
  change_note: string | null;
  created_at: string;
}

export interface Run {
  id: string;
  org_id: string;
  project_id: string;
  pipeline_id: string;
  pipeline_version_id: string;
  version: number;
  trigger: RunTrigger;
  status: RunStatus;
  current_node_id: string | null;
  queued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  rows_in: number;
  rows_out: number;
  rows_quarantined: number;
  quarantine_ratio: number | null;
  retry_total: number;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
}

export interface RunNode {
  id: string;
  run_id: string;
  node_id: string;
  node_type: string;
  status: NodeStatus;
  retry_count: number;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  rows_in: number;
  rows_out: number;
  rows_quarantined: number;
  error_code: string | null;
  error_message: string | null;
  error: Record<string, unknown> | null;
}

export interface GateCheckItem {
  rule: string;
  decision: "pass" | "fail";
  threshold: number | null;
  observed: number | null;
  evidence: string;
}

export interface GateChecklist {
  run_id: string;
  items: GateCheckItem[];
  promotable: boolean;
  evaluated_at: string;
}

export interface RunDetail extends Run {
  nodes: RunNode[];
  gate: GateChecklist | null;
}

export interface RunCreateResponse {
  run: Run;
  stream_url: string;
}

export interface DatasetColumn {
  name: string;
  dtype: string;
}

export interface Dataset {
  id: string;
  org_id: string;
  project_id: string;
  layer: Layer;
  name: string;
  storage_uri: string;
  row_count: number | null;
  schema: DatasetColumn[];
  updated_at: string;
}

/**
 * Mirrors `dataloom_contracts.datasets.DatasetPreview` EXACTLY.
 *
 * `rows` are POSITIONAL (`list[list[scalar]]`), not objects keyed by column
 * name. The preview used to be typed as `Record<string, unknown>[]`, which made
 * the table read `row[column.name]` and render empty cells against the real
 * backend while looking fine against the mock.
 */
export interface DatasetPreview {
  dataset_id: string;
  layer: Layer;
  columns: DatasetColumn[];
  rows: (string | number | boolean | null)[][];
  total_rows: number | null;
  truncated: boolean;
}

export interface LineageNode {
  id: string;
  label: string;
  kind: string;
  layer: string | null;
}

export interface LineageEdge {
  source: string;
  target: string;
}

export interface LineageGraph {
  nodes: LineageNode[];
  edges: LineageEdge[];
}

export interface Evidence {
  key: string;
  value: string;
  detail?: string | null;
}

/**
 * Mirrors `dataloom_contracts.agents.CleaningProposal` EXACTLY.
 *
 * Differences that used to be wrong here: `evidence` is a single sentence
 * (string), not a list; the sample fields are `before_samples`/`after_samples`
 * (plural); `rationale` is optional.
 */
export interface CleaningProposal {
  proposal_id: string;
  op: CleaningOp;
  column: string;
  params: Record<string, unknown>;
  evidence: string;
  risk: RiskLevel;
  confidence: number;
  rationale: string | null;
  before_samples: string[];
  after_samples: string[];
}

export interface ColumnProfile {
  name: string;
  dtype: string;
  nullable: boolean;
  semantic: string;
  null_ratio: number;
  distinct_count: number;
  samples: string[];
}

/** Mirrors `dataloom_contracts.agents.Anomaly` (severity is info|warning|error). */
export interface Anomaly {
  column: string;
  kind: string;
  severity: "info" | "warning" | "error";
  /** One Indonesian sentence with the number, e.g. "12.4% nilai kosong". */
  evidence: string;
}

/** Mirrors `dataloom_contracts.agents.ProfileReport` EXACTLY. */
export interface ProfileReport {
  status: AgentOutputStatus;
  dataset_id: string;
  sampled_rows: number;
  total_rows: number | null;
  columns: ColumnProfile[];
  anomalies: Anomaly[];
  cleaning_proposals: CleaningProposal[];
  open_questions: string[];
  summary: string | null;
  generated_at: string | null;
}

export interface AgentRun {
  id: string;
  org_id: string;
  project_id: string;
  agent_type: AgentType;
  thread_id: string;
  status: AgentRunStatus;
  budget_tokens: number;
  tokens_used: number;
  cost_usd: number;
  current_step: string | null;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  /** Not part of `AgentRunOut`; kept optional for older mock payloads. */
  error_message?: string | null;
  created_at: string;
}

export interface AgentProposal {
  id: string;
  agent_run_id: string;
  kind: "cleaning_rules" | "pipeline_draft" | "fix_proposal";
  title: string;
  payload: Record<string, unknown>;
  evidence: Record<string, unknown> | null;
  risk: RiskLevel;
  confidence: number | null;
  status: ProposalStatus;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  applied_pipeline_version_id: string | null;
  created_at: string;
}

export interface AgentEvent {
  id: string;
  agent_run_id: string;
  step: string;
  node_name: string;
  status: string;
  payload: Record<string, unknown> | null;
  at: string;
}

export interface ResultColumn {
  name: string;
  dtype: string;
}

/**
 * Mirrors `dataloom_contracts.agents.SqlAnswer`: rows arrive as positional
 * lists (not objects) because the contract keeps `list[list[scalar]]`.
 */
export interface SqlAnswer {
  status: AgentOutputStatus;
  question: string;
  sql: string | null;
  columns: ResultColumn[];
  rows: (string | number | boolean | null)[][];
  row_count: number;
  truncated: boolean;
  explanation: string | null;
  assumptions: string[];
  open_questions: string[];
  rejection_code: string | null;
}

/** Mirrors `dataloom_contracts.usage.LlmCallOut` (note: `at`, not `created_at`). */
export interface LlmCall {
  id: string;
  org_id: string;
  agent_run_id: string | null;
  agent_type: AgentType | null;
  node: string;
  provider: string;
  model: string;
  tokens_in: number;
  tokens_out: number;
  latency_ms: number;
  cost_usd: number;
  error: string | null;
  at: string;
}

/** Mirrors `dataloom_contracts.usage.BudgetOut`. */
export interface BudgetOut {
  org_id: string;
  period_start: string;
  period_end: string;
  monthly_budget_usd: number;
  spent_usd: number;
  remaining_usd: number;
  used_ratio: number;
  warn_threshold: number;
  is_exhausted: boolean;
  per_run_token_limit: number;
}

/** Mirrors `dataloom_contracts.usage.ModelUsage`. */
export interface ModelUsage {
  model: string;
  provider: string;
  calls: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
}

/** Mirrors `dataloom_contracts.usage.AgentUsage`. */
export interface AgentUsage {
  agent_type: string;
  calls: number;
  tokens: number;
  cost_usd: number;
}

/** Mirrors `dataloom_contracts.usage.DailyUsage`. */
export interface DailyUsage {
  day: string;
  cost_usd: number;
  tokens: number;
  calls: number;
}

/**
 * Mirrors `dataloom_contracts.usage.UsageSummary` EXACTLY.
 * Field names are snake_case to match the wire format; a mismatch here is a
 * real bug (it was caught by `tsc` once already).
 */
export interface UsageSummary {
  org_id: string;
  period_start: string;
  period_end: string;
  total_calls: number;
  total_tokens_in: number;
  total_tokens_out: number;
  total_cost_usd: number;
  average_latency_ms: number | null;
  by_model: ModelUsage[];
  by_agent: AgentUsage[];
  daily: DailyUsage[];
  budget: BudgetOut;
}

export interface AuditLog {
  id: string;
  org_id: string;
  actor_kind: string;
  actor_id: string | null;
  actor_email: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
  metadata: Record<string, unknown>;
  ip: string | null;
  at: string;
}

/** SSE event union payloads (contract `runs.py`). */
export interface SseNodeStatus {
  event: "node_status";
  run_id: string;
  seq: number;
  emitted_at: string;
  node_id: string;
  node_type: string;
  status: NodeStatus;
  retry_count: number;
  duration_ms: number | null;
  rows_in: number | null;
  rows_out: number | null;
  rows_quarantined: number | null;
  error_code: string | null;
  error_message: string | null;
}

export interface SseRunStatus {
  event: "run_status";
  run_id: string;
  seq: number;
  emitted_at: string;
  status: RunStatus;
  previous_status: RunStatus | null;
  rows_in: number | null;
  rows_out: number | null;
  rows_quarantined: number | null;
  error_code: string | null;
  error_message: string | null;
}

/**
 * Per-node log line. The contract emits `node_log`; the UI used to ignore it
 * entirely, so the live log panel never received real entries.
 */
export interface SseNodeLog {
  event: "node_log";
  run_id: string;
  seq: number;
  emitted_at: string;
  node_id: string;
  level: "debug" | "info" | "warning" | "error";
  message: string;
}

/** Gate checklist recomputed for the run (contract `GateEvent`). */
export interface SseGate {
  event: "gate";
  run_id: string;
  seq: number;
  emitted_at: string;
  checklist: GateChecklist;
}

export interface SseHeartbeat {
  event: "heartbeat";
  run_id: string;
  seq: number;
  emitted_at: string;
}

/**
 * NOTE: there is no `done` event in the contract. The stream ends when a
 * terminal `run_status` arrives; the client closes the connection itself.
 */
export type SseEvent = SseNodeStatus | SseNodeLog | SseGate | SseRunStatus | SseHeartbeat;

/**
 * Installation level AI model configuration (`GET /v1/installation/llm`).
 *
 * This is NOT tenant scoped: one row configures the whole deployment, so every
 * organisation on the instance shares it. The API never returns the API key
 * itself, only `api_key_configured` plus a short `api_key_hint`.
 *
 * ASSUMPTION (to be aligned with the backend contract): the fields marked
 * optional below are read from the status payload when present. The UI degrades
 * to "Belum diatur" instead of inventing a value when they are absent.
 */
export interface InstallationLlmStatus {
  provider: string;
  base_url_host: string;
  model_default: string;
  model_small: string;
  api_key_configured: boolean;
  api_key_hint: string | null;
  prices_configured: boolean;
  updated_at: string | null;
  updated_by: string | null;
  /** Raw price numbers, needed by the admin form. Optional by design. */
  price_in_per_1k?: number | null;
  price_out_per_1k?: number | null;
  /** Call timeout in seconds, prefilled by the admin form. */
  timeout_seconds?: number | null;
}

/** Body of `PUT /v1/installation/llm`. Absent fields stay unchanged. */
export interface InstallationLlmUpdate {
  base_url?: string;
  /** Omitted entirely means "keep the stored key"; never send an empty string. */
  api_key?: string;
  model_default?: string;
  model_small?: string;
  timeout_seconds?: number;
  price_in_per_1k?: number;
  price_out_per_1k?: number;
}

/**
 * `PUT /v1/installation/llm` answers with a status; the UI only needs to know
 * it succeeded and then refetches. Both shapes are accepted so a contract that
 * returns the full status object keeps working without a frontend change.
 */
export type InstallationLlmUpdateResponse = InstallationLlmStatus | { status: string };

/** Result of `POST /v1/installation/llm/test`. */
export interface InstallationLlmTestResult {
  ok: boolean;
  latency_ms: number | null;
  error_code: string | null;
  error_message: string | null;
}

/** Body of `POST /v1/installation/admins` (grants installation admin access). */
export interface InstallationAdminGrant {
  email: string;
  reason?: string;
}

