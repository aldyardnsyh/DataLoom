/**
 * Demo fixtures.
 *
 * Design rule (learned from a real demo complaint): EVERY project must have a
 * complete story. The first version concentrated all data on "Penjualan Ritel"
 * while the summary cards of "Pelanggan & CRM" and "Inventori Gudang" claimed
 * sources and pipelines that did not exist — switching projects showed empty
 * screens under numbers that promised otherwise.
 *
 * Each project now carries its own sources, pipelines, versions, runs, node
 * timelines, gate checklists, datasets, preview rows and lineage graph, and the
 * project summaries are derived from exactly that data.
 *
 * Everything here is synthetic (`Nusantara Retail`, `run-1`, ...): no figure is
 * a measurement of the real product. The builders are functions so every demo
 * session (and every reset) gets a deep-independent copy.
 */

import type {
  AgentEvent,
  AgentProposal,
  AgentRun,
  ApiKey,
  AuditLog,
  Dataset,
  GateChecklist,
  InstallationLlmStatus,
  LineageGraph,
  LlmCall,
  Membership,
  NodeStatus,
  Pipeline,
  PipelineDAGDTO,
  PipelineVersion,
  ProjectSettings,
  ProjectSummary,
  Run,
  RunNode,
  SessionInfo,
  Source,
  SourceType,
  SqlAnswer,
  UsageSummary,
  ValidationIssue,
} from "@/lib/types";

export type Dict = Record<string, unknown>;

export interface DemoDb {
  session: SessionInfo;
  projects: ProjectSummary[];
  settings: ProjectSettings;
  sources: Source[];
  keys: ApiKey[];
  pipelines: Pipeline[];
  /** Current version per pipeline id (the real API keeps them separate too). */
  versions: Record<string, PipelineVersion>;
  runs: Run[];
  /** Node timeline per run id; a queued run legitimately has none. */
  runNodes: Record<string, RunNode[]>;
  /** Gate checklist per run id; only evaluated once a run finishes. */
  gates: Record<string, GateChecklist>;
  datasets: Dataset[];
  /** Positional preview rows per dataset id, matching `DatasetPreview.rows`. */
  previewRows: Record<string, (string | number | boolean | null)[][]>;
  proposals: AgentProposal[];
  agentRun: AgentRun;
  /** The pipeline-builder run, kept separate from the profiler run. */
  builderRun: AgentRun;
  agentEvents: AgentEvent[];
  sqlAnswer: SqlAnswer;
  usage: UsageSummary;
  llmCalls: LlmCall[];
  audit: AuditLog[];
  members: Membership[];
  installation: InstallationLlmStatus;
  /** Lineage per project id. */
  lineage: Record<string, LineageGraph>;
  /** Plaintext of the LAST created/rotated demo key, returned exactly once. */
  lastPlaintextKey: string | null;
  /** Issues from the most recent validate call, kept so it stays consistent. */
  lastValidation: ValidationIssue[];
}

const ISO = (offsetMs = 0) => new Date(Date.now() - offsetMs).toISOString();

/**
 * Demo identity. The label is intentionally NOT a plausible employee name: a
 * visitor must never mistake demo data for a real tenant.
 */
export function createDemoSession(): SessionInfo {
  return {
    user: { id: "demo-user", email: "pengunjung@demo.dataloom", name: "Pengunjung Demo", created_at: ISO(9e8) },
    org: {
      id: "demo-org",
      name: "Demo Nusantara Retail",
      plan: "demo",
      llm_budget_usd_month: 5,
      created_at: ISO(9e8),
    },
    membership: {
      id: "demo-membership",
      org_id: "demo-org",
      user_id: "demo-user",
      role: "owner",
      user_email: "pengunjung@demo.dataloom",
      user_name: "Pengunjung Demo",
      created_at: ISO(9e8),
    },
    csrf_token: "demo-csrf",
    expires_at: new Date(Date.now() + 36e5).toISOString(),
  };
}

// ---------------------------------------------------------------------------
// Small builders (keep the per-project stories readable)
// ---------------------------------------------------------------------------
function makeSource(id: string, projectId: string, name: string, type: SourceType, status: string, config: Dict): Source {
  return {
    id,
    org_id: "demo-org",
    project_id: projectId,
    name,
    slug: name.toLowerCase().replace(/[^a-z0-9]+/g, "-"),
    type,
    status,
    config_public: config,
    ingest_path: `/v1/ingest/${id}`,
    last_received_at: status === "error" || status === "paused" ? null : ISO(6e5),
    created_at: ISO(9e8),
    updated_at: ISO(4e8),
  };
}

function makePipeline(id: string, projectId: string, name: string, description: string, version: number, cron: string | null, by: string): Pipeline {
  return {
    id,
    org_id: "demo-org",
    project_id: projectId,
    name,
    description,
    current_version_id: `pv-${id}`,
    current_version: version,
    schedule_cron: cron,
    created_by: by,
    created_at: ISO(9e8),
    updated_at: ISO(3 * 864e5),
  };
}

function makeVersion(id: string, pipelineId: string, version: number, dag: PipelineDAGDTO, note: string, by: "user" | "agent"): PipelineVersion {
  return {
    id,
    org_id: "demo-org",
    pipeline_id: pipelineId,
    version,
    dag,
    validation_status: "valid",
    validation_errors: [],
    created_by_kind: by,
    created_by_user_id: by === "user" ? "demo-user" : null,
    created_by_agent_run_id: null,
    parent_version_id: null,
    change_note: note,
    created_at: ISO(6 * 864e5),
  };
}

function makeRun(
  id: string,
  projectId: string,
  pipelineId: string,
  version: number,
  status: Run["status"],
  trigger: Run["trigger"],
  ageHours: number,
  rows: { in: number; out: number; quarantined: number },
  error?: { code: string; message: string } | null,
): Run {
  const finished = ["succeeded", "failed", "needs_attention", "cancelled"].includes(status);
  return {
    id,
    org_id: "demo-org",
    project_id: projectId,
    pipeline_id: pipelineId,
    pipeline_version_id: `pv-${pipelineId}`,
    version,
    trigger,
    status,
    current_node_id: status === "running" ? "n_agg" : null,
    queued_at: ISO(ageHours * 36e5),
    started_at: status === "queued" ? null : ISO(ageHours * 36e5),
    finished_at: finished ? ISO(ageHours * 36e5 - 45000) : null,
    duration_ms: finished ? 42000 : null,
    rows_in: rows.in,
    rows_out: rows.out,
    rows_quarantined: rows.quarantined,
    quarantine_ratio: rows.in > 0 ? Number((rows.quarantined / rows.in).toFixed(4)) : null,
    retry_total: status === "retrying" ? 1 : 0,
    error_code: error?.code ?? null,
    error_message: error?.message ?? null,
    created_at: ISO(ageHours * 36e5),
  };
}

interface NodeStep {
  nodeId: string;
  type: string;
  status: NodeStatus;
  durationMs: number;
  rowsIn: number;
  rowsOut: number;
  quarantined: number;
  errorCode?: string | null;
}

function makeRunNodes(runId: string, steps: NodeStep[]): RunNode[] {
  return steps.map((step, index) => ({
    id: `${runId}-n${index + 1}`,
    run_id: runId,
    node_id: step.nodeId,
    node_type: step.type,
    status: step.status,
    retry_count: step.status === "retrying" ? 1 : 0,
    started_at: ISO(9e4),
    finished_at: step.status === "pending" || step.status === "running" ? null : ISO(9e4 - step.durationMs),
    duration_ms: step.status === "pending" || step.status === "running" ? null : step.durationMs,
    rows_in: step.rowsIn,
    rows_out: step.rowsOut,
    rows_quarantined: step.quarantined,
    error_code: step.errorCode ?? null,
    error_message: step.errorCode ? "Node gagal (detail di log server)." : null,
    error: null,
  }));
}

function makeGate(runId: string, items: GateChecklist["items"]): GateChecklist {
  return {
    run_id: runId,
    promotable: items.every((item) => item.decision === "pass"),
    evaluated_at: ISO(),
    items,
  };
}

const PASS = (rule: string, evidence: string, threshold: number | null = null, observed: number | null = null) => ({
  rule,
  decision: "pass" as const,
  threshold,
  observed,
  evidence,
});
const FAIL = (rule: string, evidence: string, threshold: number | null = null, observed: number | null = null) => ({
  rule,
  decision: "fail" as const,
  threshold,
  observed,
  evidence,
});

function makeDataset(id: string, projectId: string, layer: Dataset["layer"], name: string, uri: string, rowCount: number, schema: Dataset["schema"], ageMinutes: number): Dataset {
  return {
    id,
    org_id: "demo-org",
    project_id: projectId,
    layer,
    name,
    storage_uri: uri,
    row_count: rowCount,
    schema,
    updated_at: ISO(ageMinutes * 6e4),
  };
}

function makeLineage(entries: Array<[string, string, string, string | null]>, edges: Array<[string, string]>): LineageGraph {
  return {
    nodes: entries.map(([id, label, kind, layer]) => ({ id, label, kind, layer })),
    edges: edges.map(([source, target]) => ({ source, target })),
  };
}

// ---------------------------------------------------------------------------
// Per-project DAGs
// ---------------------------------------------------------------------------
function dagSales(): PipelineDAGDTO {
  return {
    schema_version: 1,
    nodes: [
      { id: "n_source", type: "source", label: "Webhook Pesanan", params: { kind: "source", source_id: "s-1", batch_size_rows: 5000 }, position: { x: 0, y: 40 } },
      { id: "n_validate", type: "validate", label: "Validasi", params: { kind: "validate", rules: [{ rule_id: "r1", kind: "not_null", column: "order_id", params: {}, severity: "error" }, { rule_id: "r2", kind: "unique", column: "order_id", params: {}, severity: "error" }] }, position: { x: 280, y: 20 } },
      { id: "n_clean", type: "clean", label: "Bersihkan", params: { kind: "clean", operations: [{ op: "parse_date", column: "tgl", params: { format: "DD/MM/YYYY" }, risk: "low" }] }, position: { x: 560, y: 40 } },
      { id: "n_dedup", type: "transform", label: "Deduplikasi", params: { kind: "transform", select_columns: [], mappings: [], derived: [], filter: null }, position: { x: 840, y: 20 } },
      { id: "n_agg", type: "aggregate", label: "Agregasi", params: { kind: "aggregate", group_by: ["bulan"], metrics: [{ alias: "total_pendapatan", function: "sum", column: { column: "amount" } }] }, position: { x: 1120, y: 40 } },
      { id: "n_sink", type: "sink", label: "Gudang Data", params: { kind: "sink", layer: "gold", dataset_name: "gold_penjualan_bulanan", write_mode: "overwrite" }, position: { x: 1400, y: 20 } },
    ],
    edges: [
      { from: "n_source", to: "n_validate" },
      { from: "n_validate", to: "n_clean" },
      { from: "n_clean", to: "n_dedup" },
      { from: "n_dedup", to: "n_agg" },
      { from: "n_agg", to: "n_sink" },
    ],
    metadata: {},
  };
}

function dagOrdersSilver(): PipelineDAGDTO {
  return {
    schema_version: 1,
    nodes: [
      { id: "n_source", type: "source", label: "Ekspor Harian", params: { kind: "source", source_id: "s-2", batch_size_rows: 5000 }, position: { x: 0, y: 40 } },
      { id: "n_clean", type: "clean", label: "Bersihkan", params: { kind: "clean", operations: [{ op: "normalize_null", column: "amount", params: {}, risk: "low" }] }, position: { x: 280, y: 20 } },
      { id: "n_sink", type: "sink", label: "Gudang Data", params: { kind: "sink", layer: "silver", dataset_name: "silver_orders", write_mode: "merge" }, position: { x: 560, y: 40 } },
    ],
    edges: [
      { from: "n_source", to: "n_clean" },
      { from: "n_clean", to: "n_sink" },
    ],
    metadata: {},
  };
}

function dagCrm(): PipelineDAGDTO {
  return {
    schema_version: 1,
    nodes: [
      { id: "n_source", type: "source", label: "API CRM", params: { kind: "source", source_id: "s-5", batch_size_rows: 2000 }, position: { x: 0, y: 40 } },
      { id: "n_validate", type: "validate", label: "Validasi", params: { kind: "validate", rules: [{ rule_id: "r1", kind: "not_null", column: "email", params: {}, severity: "error" }] }, position: { x: 280, y: 20 } },
      { id: "n_clean", type: "clean", label: "Bersihkan", params: { kind: "clean", operations: [{ op: "lowercase", column: "email", params: {}, risk: "low" }] }, position: { x: 560, y: 40 } },
      { id: "n_sink", type: "sink", label: "Gudang Data", params: { kind: "sink", layer: "silver", dataset_name: "silver_customers", write_mode: "merge" }, position: { x: 840, y: 40 } },
    ],
    edges: [
      { from: "n_source", to: "n_validate" },
      { from: "n_validate", to: "n_clean" },
      { from: "n_clean", to: "n_sink" },
    ],
    metadata: {},
  };
}

function dagStock(): PipelineDAGDTO {
  return {
    schema_version: 1,
    nodes: [
      { id: "n_source", type: "source", label: "DB Inventori", params: { kind: "source", source_id: "s-7", batch_size_rows: 10000 }, position: { x: 0, y: 40 } },
      { id: "n_clean", type: "clean", label: "Bersihkan", params: { kind: "clean", operations: [{ op: "cast", column: "qty", params: { dtype: "int" }, risk: "low" }] }, position: { x: 280, y: 20 } },
      { id: "n_sink", type: "sink", label: "Gudang Data", params: { kind: "sink", layer: "silver", dataset_name: "silver_stok", write_mode: "overwrite" }, position: { x: 560, y: 40 } },
    ],
    edges: [
      { from: "n_source", to: "n_clean" },
      { from: "n_clean", to: "n_sink" },
    ],
    metadata: {},
  };
}

function dagRekap(): PipelineDAGDTO {
  return {
    schema_version: 1,
    nodes: [
      { id: "n_source", type: "source", label: "Silver Stok", params: { kind: "source", source_id: "s-7", batch_size_rows: 10000 }, position: { x: 0, y: 40 } },
      { id: "n_agg", type: "aggregate", label: "Agregasi", params: { kind: "aggregate", group_by: ["gudang"], metrics: [{ alias: "total_qty", function: "sum", column: { column: "qty" } }] }, position: { x: 280, y: 20 } },
      { id: "n_sink", type: "sink", label: "Gudang Data", params: { kind: "sink", layer: "gold", dataset_name: "gold_rekap_stok", write_mode: "overwrite" }, position: { x: 560, y: 40 } },
    ],
    edges: [
      { from: "n_source", to: "n_agg" },
      { from: "n_agg", to: "n_sink" },
    ],
    metadata: {},
  };
}

// ---------------------------------------------------------------------------
// Preview row generators (positional, one per dataset shape)
// ---------------------------------------------------------------------------
const pad = (value: number, width = 2) => String(value).padStart(width, "0");
const month = (index: number) => `2026-${pad((index % 12) + 1)}`;

function salesPreview(): (string | number | boolean | null)[][] {
  return Array.from({ length: 18 }, (_, i) => [month(i), 150000 + i * 7250]);
}

function ordersPreview(): (string | number | boolean | null)[][] {
  return Array.from({ length: 20 }, (_, i) => [`ORD-${month(i)}-${1000 + i * 7}`, month(i), 125000 + i * 3100]);
}

function quarantinePreview(): (string | number | boolean | null)[][] {
  return [
    ["ORD-2026-03-1042", "not_null"],
    ["ORD-2026-03-1087", "unique"],
    ["ORD-2026-04-1201", "not_null"],
    ["ORD-2026-04-1299", "type_mismatch"],
    ["ORD-2026-05-1310", "not_null"],
  ];
}

function quarantineCustomersPreview(): (string | number | boolean | null)[][] {
  return [
    ["C-0031", "missing_required_value"],
    ["C-0044", "duplicate_value"],
    ["C-0058", "missing_required_value"],
    ["C-0071", "pattern_mismatch"],
    ["C-0090", "duplicate_value"],
    ["C-0102", "missing_required_value"],
  ];
}

function quarantineStockPreview(): (string | number | boolean | null)[][] {
  return [
    ["SKU-011", "type_mismatch"],
    ["SKU-024", "out_of_range"],
    ["SKU-039", "type_mismatch"],
    ["SKU-052", "stale_data"],
  ];
}

function crmContactsPreview(): (string | number | boolean | null)[][] {
  return Array.from({ length: 20 }, (_, i) => [`C-${pad(i + 1, 4)}`, `kontak${i + 1}@contoh.co.id`, `Kontak ${i + 1}`, i % 2 === 0 ? "webinar" : "referral"]);
}

function customersPreview(): (string | number | boolean | null)[][] {
  return Array.from({ length: 20 }, (_, i) => [`CUS-${pad(i + 1, 4)}`, `pelanggan${i + 1}@contoh.co.id`, `Pelanggan ${i + 1}`, `2026-${pad((i % 9) + 1)}-1${i % 9}`]);
}

function activeCustomersPreview(): (string | number | boolean | null)[][] {
  return Array.from({ length: 9 }, (_, i) => [month(i), 180 + i * 23]);
}

function stockPreview(): (string | number | boolean | null)[][] {
  return Array.from({ length: 20 }, (_, i) => [`SKU-${pad(i + 1, 3)}`, i % 3 === 0 ? "Gudang A" : i % 3 === 1 ? "Gudang B" : "Gudang C", 12 + i * 4, `2026-10-${pad((i % 20) + 1)}T08:00:00Z`]);
}

function silverStockPreview(): (string | number | boolean | null)[][] {
  return Array.from({ length: 20 }, (_, i) => [`SKU-${pad(i + 1, 3)}`, i % 3 === 0 ? "Gudang A" : i % 3 === 1 ? "Gudang B" : "Gudang C", 12 + i * 4]);
}

function rekapStockPreview(): (string | number | boolean | null)[][] {
  return [
    ["Gudang A", 1040],
    ["Gudang B", 1216],
    ["Gudang C", 1320],
  ];
}

// ---------------------------------------------------------------------------
// The database
// ---------------------------------------------------------------------------
export function createDemoDb(): DemoDb {
  const dagPl1 = dagSales();
  const dagPl2 = dagOrdersSilver();
  const dagCrmDag = dagCrm();
  const dagStockDag = dagStock();
  const dagRekapDag = dagRekap();

  const datasets: Dataset[] = [
    makeDataset("d-1", "p-1", "bronze", "bronze_orders", "s3://demo/bronze/orders.parquet", 124000, [{ name: "order_id", dtype: "String" }, { name: "tgl", dtype: "String" }, { name: "amount", dtype: "String" }], 10),
    makeDataset("d-2", "p-1", "silver", "silver_orders", "s3://demo/silver/orders.parquet", 123100, [{ name: "order_id", dtype: "String" }, { name: "tgl", dtype: "Date" }, { name: "amount", dtype: "Float64" }], 9),
    makeDataset("d-3", "p-1", "gold", "gold_penjualan_bulanan", "duckdb://demo", 18, [{ name: "bulan", dtype: "String" }, { name: "total_pendapatan", dtype: "Float64" }], 8),
    makeDataset("d-4", "p-1", "quarantine", "quarantine_orders", "s3://demo/quarantine/orders.parquet", 47, [{ name: "order_id", dtype: "String" }, { name: "_reason_code", dtype: "String" }], 7),
    makeDataset("d-5", "p-2", "bronze", "bronze_crm_contacts", "s3://demo/bronze/crm_contacts.parquet", 8420, [{ name: "contact_id", dtype: "String" }, { name: "email", dtype: "String" }, { name: "nama", dtype: "String" }, { name: "sumber", dtype: "String" }], 12),
    makeDataset("d-6", "p-2", "silver", "silver_customers", "s3://demo/silver/customers.parquet", 8105, [{ name: "customer_id", dtype: "String" }, { name: "email", dtype: "String" }, { name: "nama", dtype: "String" }, { name: "terakhir_aktif", dtype: "Date" }], 11),
    makeDataset("d-7", "p-2", "gold", "gold_pelanggan_aktif", "duckdb://demo", 9, [{ name: "bulan", dtype: "String" }, { name: "pelanggan_aktif", dtype: "Int64" }], 10),
    makeDataset("d-8", "p-3", "bronze", "bronze_stock", "s3://demo/bronze/stock.parquet", 46000, [{ name: "sku", dtype: "String" }, { name: "gudang", dtype: "String" }, { name: "qty", dtype: "String" }, { name: "updated_at", dtype: "String" }], 15),
    makeDataset("d-9", "p-3", "silver", "silver_stok", "s3://demo/silver/stock.parquet", 45980, [{ name: "sku", dtype: "String" }, { name: "gudang", dtype: "String" }, { name: "qty", dtype: "Int64" }], 14),
    makeDataset("d-10", "p-3", "gold", "gold_rekap_stok", "duckdb://demo", 3, [{ name: "gudang", dtype: "String" }, { name: "total_qty", dtype: "Int64" }], 13),
    makeDataset("d-11", "p-2", "quarantine", "quarantine_customers", "s3://demo/quarantine/customers.parquet", 315, [{ name: "contact_id", dtype: "String" }, { name: "_reason_code", dtype: "String" }], 8),
    makeDataset("d-12", "p-3", "quarantine", "quarantine_stock", "s3://demo/quarantine/stock.parquet", 20, [{ name: "sku", dtype: "String" }, { name: "_reason_code", dtype: "String" }], 9),
  ];

  const runs: Run[] = [
    // p-1 — flagship story, deepest history
    makeRun("run-1", "p-1", "pl-1", 3, "succeeded", "schedule", 1, { in: 12400, out: 12341, quarantined: 59 }),
    makeRun("run-2", "p-1", "pl-1", 3, "needs_attention", "manual", 5, { in: 12180, out: 11280, quarantined: 950 }, { code: "gate_failed", message: "Rasio karantina 7,8% melewati ambang 5%." }),
    makeRun("run-3", "p-1", "pl-2", 1, "succeeded", "schedule", 7, { in: 12100, out: 12055, quarantined: 45 }),
    makeRun("run-4", "p-1", "pl-1", 3, "failed", "manual", 26, { in: 12390, out: 0, quarantined: 0 }, { code: "node_failed", message: "Node agregasi gagal (detail di log server)." }),
    makeRun("run-5", "p-1", "pl-2", 1, "succeeded", "schedule", 31, { in: 12050, out: 12002, quarantined: 48 }),
    makeRun("run-6", "p-1", "pl-1", 3, "succeeded", "schedule", 55, { in: 12310, out: 12254, quarantined: 56 }),
    makeRun("run-7", "p-1", "pl-1", 3, "queued", "manual", 0, { in: 0, out: 0, quarantined: 0 }),
    // p-2 — Pelanggan & CRM
    makeRun("run-c1", "p-2", "pl-crm", 2, "succeeded", "schedule", 3, { in: 8420, out: 8105, quarantined: 315 }),
    makeRun("run-c2", "p-2", "pl-crm", 2, "needs_attention", "manual", 20, { in: 8390, out: 7610, quarantined: 780 }, { code: "gate_failed", message: "Rasio karantina 9,3% melewati ambang 5%." }),
    makeRun("run-c3", "p-2", "pl-crm", 2, "queued", "schedule", 0, { in: 0, out: 0, quarantined: 0 }),
    // p-3 — Inventori Gudang
    makeRun("run-i1", "p-3", "pl-inv-1", 1, "succeeded", "schedule", 2, { in: 46000, out: 45980, quarantined: 20 }),
    makeRun("run-i2", "p-3", "pl-inv-2", 1, "running", "schedule", 0, { in: 45980, out: 0, quarantined: 0 }),
  ];

  return {
    session: createDemoSession(),

    // Summaries are derived from the data below, so a card can never claim
    // sources or pipelines that switching to the project will not show.
    projects: [
      { id: "p-1", name: "Penjualan Ritel", description: "Data pesanan harian dari webhook dan CSV.", source_count: 4, pipeline_count: 2, last_run_status: "succeeded", updated_at: ISO(36e5) },
      { id: "p-2", name: "Pelanggan & CRM", description: "Sinkronisasi pelanggan dari REST API.", source_count: 2, pipeline_count: 1, last_run_status: "needs_attention", updated_at: ISO(864e5) },
      { id: "p-3", name: "Inventori Gudang", description: "Stok harian dari Postgres read-only.", source_count: 1, pipeline_count: 2, last_run_status: "running", updated_at: ISO(72e5) },
    ],

    settings: {
      max_quarantine_ratio: 0.05,
      require_approval_for_destructive: true,
      freshness_threshold_minutes: 1440,
      batch_size_rows: 5000,
      node_timeout_seconds: 45,
      run_timeout_seconds: 300,
      max_retries_per_node: 2,
      max_concurrent_runs: 1,
      schedule_cron: null,
    },

    sources: [
      makeSource("s-1", "p-1", "Webhook Pesanan", "webhook", "connected", {}),
      makeSource("s-2", "p-1", "Ekspor Harian", "csv", "connected", { delimiter: "," }),
      makeSource("s-3", "p-1", "API Katalog", "api", "error", { url: "https://api.contoh.com/products" }),
      makeSource("s-4", "p-1", "DB Pesanan Lama", "postgres", "paused", { table: "orders_2025", host: "db.contoh.com" }),
      makeSource("s-5", "p-2", "API CRM", "api", "connected", { url: "https://crm.contoh.id/v1", interval_minutes: 15 }),
      makeSource("s-6", "p-2", "Ekspor Kontak", "csv", "connected", { delimiter: ";" }),
      makeSource("s-7", "p-3", "DB Inventori", "postgres", "connected", { table: "stock", host: "gudang.contoh.com", mode: "read-only" }),
    ],

    keys: [
      { id: "k-1", org_id: "demo-org", project_id: "p-1", name: "Default key", prefix: "dl_ab12cd34", created_at: ISO(9e8), last_used_at: ISO(6e4), revoked_at: null },
      { id: "k-0", org_id: "demo-org", project_id: "p-1", name: "Key lama", prefix: "dl_zz99yy88", created_at: ISO(9e8), last_used_at: null, revoked_at: ISO(1.2e9) },
      { id: "k-2", org_id: "demo-org", project_id: "p-2", name: "Key integrasi CRM", prefix: "dl_crm9911", created_at: ISO(9e8), last_used_at: ISO(36e5), revoked_at: null },
    ],

    pipelines: [
      makePipeline("pl-1", "p-1", "Penjualan Harian ke Gold", "Bersihkan pesanan lalu agregasi per bulan.", 3, "0 2 * * *", "demo-user"),
      makePipeline("pl-2", "p-1", "Pesanan ke Silver", "Normalisasi pesanan mentah dari ekspor CSV.", 1, null, "agent"),
      makePipeline("pl-crm", "p-2", "Sinkronisasi CRM ke Silver", "Ambil kontak CRM, validasi email, simpan ke silver.", 2, "*/15 * * * *", "demo-user"),
      makePipeline("pl-inv-1", "p-3", "Stok Harian ke Silver", "Tarik stok dari Postgres read-only, konversi qty.", 1, "0 1 * * *", "demo-user"),
      makePipeline("pl-inv-2", "p-3", "Rekap Stok ke Gold", "Rekapitulasi total stok per gudang.", 1, "0 4 * * *", "demo-user"),
    ],

    versions: {
      "pl-1": makeVersion("pv-pl-1", "pl-1", 3, dagPl1, "Tambah dedup", "user"),
      "pl-2": makeVersion("pv-pl-2", "pl-2", 1, dagPl2, "Versi awal dari usulan agent", "agent"),
      "pl-crm": makeVersion("pv-pl-crm", "pl-crm", 2, dagCrmDag, "Tambah validasi email", "user"),
      "pl-inv-1": makeVersion("pv-pl-inv-1", "pl-inv-1", 1, dagStockDag, "Versi awal", "user"),
      "pl-inv-2": makeVersion("pv-pl-inv-2", "pl-inv-2", 1, dagRekapDag, "Versi awal", "user"),
    },

    runs,

    runNodes: {
      "run-1": makeRunNodes("run-1", [
        { nodeId: "n_source", type: "source", status: "succeeded", durationMs: 3100, rowsIn: 12400, rowsOut: 12400, quarantined: 0 },
        { nodeId: "n_validate", type: "validate", status: "succeeded", durationMs: 9200, rowsIn: 12400, rowsOut: 12360, quarantined: 40 },
        { nodeId: "n_clean", type: "clean", status: "succeeded", durationMs: 14800, rowsIn: 12360, rowsOut: 12348, quarantined: 12 },
        { nodeId: "n_dedup", type: "transform", status: "succeeded", durationMs: 8100, rowsIn: 12348, rowsOut: 12341, quarantined: 7 },
        { nodeId: "n_agg", type: "aggregate", status: "succeeded", durationMs: 7200, rowsIn: 12341, rowsOut: 18, quarantined: 0 },
        { nodeId: "n_sink", type: "sink", status: "succeeded", durationMs: 2900, rowsIn: 18, rowsOut: 18, quarantined: 0 },
      ]),
      "run-2": makeRunNodes("run-2", [
        { nodeId: "n_source", type: "source", status: "succeeded", durationMs: 3200, rowsIn: 12180, rowsOut: 12180, quarantined: 0 },
        { nodeId: "n_validate", type: "validate", status: "succeeded", durationMs: 9400, rowsIn: 12180, rowsOut: 11500, quarantined: 680 },
        { nodeId: "n_clean", type: "clean", status: "succeeded", durationMs: 15100, rowsIn: 11500, rowsOut: 11460, quarantined: 40 },
        { nodeId: "n_dedup", type: "transform", status: "succeeded", durationMs: 8300, rowsIn: 11460, rowsOut: 11440, quarantined: 20 },
        { nodeId: "n_agg", type: "aggregate", status: "succeeded", durationMs: 7400, rowsIn: 11440, rowsOut: 18, quarantined: 0 },
        { nodeId: "n_sink", type: "sink", status: "succeeded", durationMs: 2900, rowsIn: 18, rowsOut: 18, quarantined: 0 },
      ]),
      "run-3": makeRunNodes("run-3", [
        { nodeId: "n_source", type: "source", status: "succeeded", durationMs: 2800, rowsIn: 12100, rowsOut: 12100, quarantined: 0 },
        { nodeId: "n_clean", type: "clean", status: "succeeded", durationMs: 11200, rowsIn: 12100, rowsOut: 12055, quarantined: 45 },
        { nodeId: "n_sink", type: "sink", status: "succeeded", durationMs: 2400, rowsIn: 12055, rowsOut: 12055, quarantined: 0 },
      ]),
      "run-4": makeRunNodes("run-4", [
        { nodeId: "n_source", type: "source", status: "succeeded", durationMs: 3100, rowsIn: 12390, rowsOut: 12390, quarantined: 0 },
        { nodeId: "n_validate", type: "validate", status: "succeeded", durationMs: 9100, rowsIn: 12390, rowsOut: 12350, quarantined: 40 },
        { nodeId: "n_clean", type: "clean", status: "succeeded", durationMs: 14600, rowsIn: 12350, rowsOut: 12338, quarantined: 12 },
        { nodeId: "n_dedup", type: "transform", status: "succeeded", durationMs: 8200, rowsIn: 12338, rowsOut: 12331, quarantined: 7 },
        { nodeId: "n_agg", type: "aggregate", status: "failed", durationMs: 6100, rowsIn: 12331, rowsOut: 0, quarantined: 0, errorCode: "aggregate_overflow" },
      ]),
      "run-c1": makeRunNodes("run-c1", [
        { nodeId: "n_source", type: "source", status: "succeeded", durationMs: 4200, rowsIn: 8420, rowsOut: 8420, quarantined: 0 },
        { nodeId: "n_validate", type: "validate", status: "succeeded", durationMs: 5600, rowsIn: 8420, rowsOut: 8210, quarantined: 210 },
        { nodeId: "n_clean", type: "clean", status: "succeeded", durationMs: 6400, rowsIn: 8210, rowsOut: 8105, quarantined: 105 },
        { nodeId: "n_sink", type: "sink", status: "succeeded", durationMs: 2100, rowsIn: 8105, rowsOut: 8105, quarantined: 0 },
      ]),
      "run-c2": makeRunNodes("run-c2", [
        { nodeId: "n_source", type: "source", status: "succeeded", durationMs: 4400, rowsIn: 8390, rowsOut: 8390, quarantined: 0 },
        { nodeId: "n_validate", type: "validate", status: "succeeded", durationMs: 5800, rowsIn: 8390, rowsOut: 7820, quarantined: 570 },
        { nodeId: "n_clean", type: "clean", status: "succeeded", durationMs: 6600, rowsIn: 7820, rowsOut: 7610, quarantined: 210 },
        { nodeId: "n_sink", type: "sink", status: "succeeded", durationMs: 2200, rowsIn: 7610, rowsOut: 7610, quarantined: 0 },
      ]),
      "run-i1": makeRunNodes("run-i1", [
        { nodeId: "n_source", type: "source", status: "succeeded", durationMs: 5200, rowsIn: 46000, rowsOut: 46000, quarantined: 0 },
        { nodeId: "n_clean", type: "clean", status: "succeeded", durationMs: 9800, rowsIn: 46000, rowsOut: 45980, quarantined: 20 },
        { nodeId: "n_sink", type: "sink", status: "succeeded", durationMs: 3400, rowsIn: 45980, rowsOut: 45980, quarantined: 0 },
      ]),
    },

    gates: {
      "run-1": makeGate("run-1", [
        PASS("schema_valid", "Skema silver lolos validasi."),
        PASS("quarantine_ratio", "Rasio quarantine 0,48% (ambang 5,00%).", 0.05, 59 / 12400),
        PASS("reconciliation", "Rekonsiliasi baris silver dan gold cocok."),
        PASS("pending_proposals", "Tidak ada usulan agent yang wajib diputuskan.", null, 0),
      ]),
      "run-2": makeGate("run-2", [
        PASS("schema_valid", "Skema silver lolos validasi."),
        FAIL("quarantine_ratio", "Rasio karantina 7,80% melewati ambang 5,00%.", 0.05, 950 / 12180),
        PASS("reconciliation", "Rekonsiliasi baris silver dan gold cocok."),
        PASS("pending_proposals", "Tidak ada usulan agent yang wajib diputuskan.", null, 0),
      ]),
      "run-3": makeGate("run-3", [
        PASS("schema_valid", "Skema silver lolos validasi."),
        PASS("quarantine_ratio", "Rasio quarantine 0,37% (ambang 5,00%).", 0.05, 45 / 12100),
        PASS("reconciliation", "Rekonsiliasi baris silver dan gold cocok."),
        PASS("pending_proposals", "Tidak ada usulan agent yang wajib diputuskan.", null, 0),
      ]),
      "run-4": makeGate("run-4", [
        FAIL("schema_valid", "Node agregasi gagal; skema gold tidak dibuat."),
        PASS("quarantine_ratio", "Tidak ada baris dikarantina.", 0.05, 0),
        FAIL("reconciliation", "Baris silver dan gold tidak dapat direkonsiliasi."),
        PASS("pending_proposals", "Tidak ada usulan agent yang wajib diputuskan.", null, 0),
      ]),
      "run-5": makeGate("run-5", [
        PASS("schema_valid", "Skema silver lolos validasi."),
        PASS("quarantine_ratio", "Rasio quarantine 0,40% (ambang 5,00%).", 0.05, 48 / 12050),
        PASS("reconciliation", "Rekonsiliasi baris silver dan gold cocok."),
        PASS("pending_proposals", "Tidak ada usulan agent yang wajib diputuskan.", null, 0),
      ]),
      "run-6": makeGate("run-6", [
        PASS("schema_valid", "Skema silver lolos validasi."),
        PASS("quarantine_ratio", "Rasio quarantine 0,45% (ambang 5,00%).", 0.05, 56 / 12310),
        PASS("reconciliation", "Rekonsiliasi baris silver dan gold cocok."),
        PASS("pending_proposals", "Tidak ada usulan agent yang wajib diputuskan.", null, 0),
      ]),
      "run-c1": makeGate("run-c1", [
        PASS("schema_valid", "Skema silver lolos validasi."),
        PASS("quarantine_ratio", "Rasio quarantine 3,74% (ambang 5,00%).", 0.05, 315 / 8420),
        PASS("reconciliation", "Rekonsiliasi baris silver dan gold cocok."),
        PASS("pending_proposals", "Tidak ada usulan agent yang wajib diputuskan.", null, 0),
      ]),
      "run-c2": makeGate("run-c2", [
        PASS("schema_valid", "Skema silver lolos validasi."),
        FAIL("quarantine_ratio", "Rasio karantina 9,30% melewati ambang 5,00%.", 0.05, 780 / 8390),
        PASS("reconciliation", "Rekonsiliasi baris silver dan gold cocok."),
        PASS("pending_proposals", "Tidak ada usulan agent yang wajib diputuskan.", null, 0),
      ]),
      "run-i1": makeGate("run-i1", [
        PASS("schema_valid", "Skema silver lolos validasi."),
        PASS("quarantine_ratio", "Rasio quarantine 0,04% (ambang 5,00%).", 0.05, 20 / 46000),
        PASS("reconciliation", "Rekonsiliasi baris silver dan gold cocok."),
        PASS("pending_proposals", "Tidak ada usulan agent yang wajib diputuskan.", null, 0),
      ]),
    },

    datasets,

    previewRows: {
      "d-1": ordersPreview(),
      "d-2": ordersPreview(),
      "d-3": salesPreview(),
      "d-4": quarantinePreview(),
      "d-5": crmContactsPreview(),
      "d-6": customersPreview(),
      "d-7": activeCustomersPreview(),
      "d-8": stockPreview(),
      "d-9": silverStockPreview(),
      "d-10": rekapStockPreview(),
      "d-11": quarantineCustomersPreview(),
      "d-12": quarantineStockPreview(),
    },

    proposals: createProposals(),
    agentRun: {
      id: "ar-1",
      org_id: "demo-org",
      project_id: "p-1",
      agent_type: "schema_profiler",
      thread_id: "schema_profiler:demo",
      status: "waiting_approval",
      budget_tokens: 20000,
      tokens_used: 4210,
      cost_usd: 0.0021,
      current_step: "waiting_approval",
      started_at: ISO(9e4),
      finished_at: null,
      error_code: null,
      error_message: null,
      created_at: ISO(9e4),
    },
    builderRun: {
      id: "ar-builder",
      org_id: "demo-org",
      project_id: "p-1",
      agent_type: "pipeline_builder",
      thread_id: "pipeline_builder:demo",
      status: "waiting_approval",
      budget_tokens: 20000,
      tokens_used: 3180,
      cost_usd: 0.0016,
      current_step: "waiting_approval",
      started_at: ISO(12e4),
      finished_at: null,
      error_code: null,
      error_message: null,
      created_at: ISO(12e4),
    },
    agentEvents: [
      { id: "e1", agent_run_id: "ar-1", step: "load_stats", node_name: "load_stats", status: "ok", payload: { rows_analysed: 1000 }, at: ISO(8.5e4) },
      { id: "e2", agent_run_id: "ar-1", step: "infer_schema", node_name: "infer_schema", status: "ok", payload: {}, at: ISO(7e4) },
      { id: "e3", agent_run_id: "ar-1", step: "propose_rules", node_name: "propose_rules", status: "ok", payload: { proposals_raw: 3 }, at: ISO(5e4) },
      { id: "e4", agent_run_id: "ar-1", step: "validate_proposals", node_name: "validate_proposals", status: "ok", payload: { proposals: 3 }, at: ISO(3e4) },
      { id: "e5", agent_run_id: "ar-1", step: "approval", node_name: "approval", status: "waiting", payload: {}, at: ISO(1e4) },
    ],
    sqlAnswer: {
      status: "ok",
      question: "Total pendapatan per bulan",
      sql: "SELECT bulan, SUM(total_pendapatan) AS total\nFROM gold_penjualan_bulanan\nGROUP BY bulan\nORDER BY bulan\nLIMIT 1000",
      columns: [{ name: "bulan", dtype: "string" }, { name: "total", dtype: "float" }],
      rows: salesPreview().slice(0, 12),
      row_count: 12,
      truncated: false,
      explanation: "Menjumlahkan kolom total_pendapatan per bulan dari tabel gold.",
      assumptions: ["Pendapatan dihitung dari kolom total_pendapatan.", "Hanya data yang lolos gate kualitas."],
      open_questions: [],
      rejection_code: null,
    },

    usage: createUsage(),
    llmCalls: Array.from({ length: 12 }, (_, index) => ({
      id: `lc-${index}`,
      org_id: "demo-org",
      agent_run_id: "ar-1",
      agent_type: index % 2 === 0 ? ("schema_profiler" as const) : ("text_to_sql" as const),
      node: index % 2 === 0 ? "propose_rules" : "draft_sql",
      provider: "openai_compatible",
      model: index % 3 === 0 ? "model-menengah" : "model-kecil",
      tokens_in: 1200 + index * 90,
      tokens_out: 320 + index * 20,
      latency_ms: 1400 + index * 110,
      cost_usd: Number((0.0012 + index * 0.0004).toFixed(6)),
      error: index === 7 ? "timeout" : null,
      at: ISO(index * 12e5),
    })),

    audit: [
      { id: "a1", org_id: "demo-org", actor_kind: "user", actor_id: "demo-user", actor_email: "pengunjung@demo.dataloom", action: "proposal.decision", target_type: "agent_proposal", target_id: "pr-1", metadata: {}, ip: "10.0.0.5", at: ISO(6e5) },
      { id: "a2", org_id: "demo-org", actor_kind: "user", actor_id: "demo-user", actor_email: "pengunjung@demo.dataloom", action: "api_key.rotate", target_type: "api_key", target_id: "k-1", metadata: {}, ip: "10.0.0.5", at: ISO(36e5) },
      { id: "a3", org_id: "demo-org", actor_kind: "system", actor_id: null, actor_email: null, action: "run.create", target_type: "run", target_id: "run-3", metadata: { trigger: "schedule" }, ip: null, at: ISO(72e5) },
      { id: "a4", org_id: "demo-org", actor_kind: "user", actor_id: "demo-user", actor_email: "pengunjung@demo.dataloom", action: "membership.role_update", target_type: "membership", target_id: "m-2", metadata: { role: "editor" }, ip: "10.0.0.5", at: ISO(864e5) },
    ],

    members: [
      { id: "m-1", org_id: "demo-org", user_id: "demo-user", role: "owner", user_email: "pengunjung@demo.dataloom", user_name: "Pengunjung Demo", created_at: ISO(9e8) },
      { id: "m-2", org_id: "demo-org", user_id: "user-2", role: "editor", user_email: "raka@demo.dataloom", user_name: "Raka Pratama", created_at: ISO(9e8) },
      { id: "m-3", org_id: "demo-org", user_id: "user-3", role: "viewer", user_email: "sinta@demo.dataloom", user_name: "Sinta Maharani", created_at: ISO(9e8) },
      { id: "m-4", org_id: "demo-org", user_id: "user-4", role: "admin", user_email: "budi@demo.dataloom", user_name: "Budi Santoso", created_at: ISO(9e8) },
    ],

    installation: {
      provider: "openai_compatible",
      base_url_host: "api.contoh.com",
      model_default: "model-menengah",
      model_small: "model-kecil",
      api_key_configured: true,
      api_key_hint: "9f2c",
      prices_configured: true,
      price_in_per_1k: 0.00015,
      price_out_per_1k: 0.0006,
      timeout_seconds: 60,
      updated_at: ISO(),
      updated_by: "pengunjung@demo.dataloom",
    },

    lineage: {
      "p-1": makeLineage(
        [
          ["source:s-1", "Webhook Pesanan", "source", null],
          ["dataset:d-1", "bronze_orders", "dataset", "bronze"],
          ["dataset:d-2", "silver_orders", "dataset", "silver"],
          ["dataset:d-3", "gold_penjualan_bulanan", "dataset", "gold"],
          ["dataset:d-4", "quarantine_orders", "dataset", "quarantine"],
        ],
        [
          ["source:s-1", "dataset:d-1"],
          ["dataset:d-1", "dataset:d-2"],
          ["dataset:d-2", "dataset:d-3"],
          ["dataset:d-2", "dataset:d-4"],
        ],
      ),
      "p-2": makeLineage(
        [
          ["source:s-5", "API CRM", "source", null],
          ["dataset:d-5", "bronze_crm_contacts", "dataset", "bronze"],
          ["dataset:d-6", "silver_customers", "dataset", "silver"],
          ["dataset:d-7", "gold_pelanggan_aktif", "dataset", "gold"],
          ["dataset:d-11", "quarantine_customers", "dataset", "quarantine"],
        ],
        [
          ["source:s-5", "dataset:d-5"],
          ["dataset:d-5", "dataset:d-6"],
          ["dataset:d-6", "dataset:d-7"],
          ["dataset:d-6", "dataset:d-11"],
        ],
      ),
      "p-3": makeLineage(
        [
          ["source:s-7", "DB Inventori", "source", null],
          ["dataset:d-8", "bronze_stock", "dataset", "bronze"],
          ["dataset:d-9", "silver_stok", "dataset", "silver"],
          ["dataset:d-10", "gold_rekap_stok", "dataset", "gold"],
          ["dataset:d-12", "quarantine_stock", "dataset", "quarantine"],
        ],
        [
          ["source:s-7", "dataset:d-8"],
          ["dataset:d-8", "dataset:d-9"],
          ["dataset:d-9", "dataset:d-10"],
          ["dataset:d-9", "dataset:d-12"],
        ],
      ),
    },

    lastPlaintextKey: null,
    lastValidation: [],
  };
}

function createProposals(): AgentProposal[] {
  const base = {
    agent_run_id: "ar-1",
    kind: "cleaning_rules" as const,
    evidence: null,
    decided_by: null,
    decided_at: null,
    decision_note: null,
    applied_pipeline_version_id: null,
    status: "pending" as const,
    created_at: ISO(9e4),
  };
  return [
    {
      ...base,
      id: "pr-1",
      title: "parse_date pada kolom tgl",
      payload: { proposal_id: "p1", op: "parse_date", column: "tgl", params: { format: "DD/MM/YYYY" }, risk: "low", confidence: 0.94, rationale: "Format tanggal konsisten.", before_samples: ["12/10/2026", "13/10/2026"], after_samples: ["2026-10-12", "2026-10-13"] },
      evidence: { evidence: "97,2% nilai cocok pola DD/MM/YYYY, 0,3% nilai kosong" },
      risk: "low",
      confidence: 0.94,
    },
    {
      ...base,
      id: "pr-2",
      title: "normalize_null pada kolom amount",
      payload: { proposal_id: "p2", op: "normalize_null", column: "amount", params: {}, risk: "low", confidence: 0.88, rationale: "11% nilai kosong berupa token teks.", before_samples: ["", "N/A", "1.250"], after_samples: ["NULL", "NULL", "1.250"] },
      evidence: { evidence: "11,0% nilai kosong terdeteksi token N/A dan string kosong" },
      risk: "low",
      confidence: 0.88,
    },
    {
      ...base,
      id: "pr-3",
      title: "drop_if pada kolom order_id",
      payload: { proposal_id: "p3", op: "drop_if", column: "order_id", params: { condition: { column: "order_id", op: "is_null" } }, risk: "destructive", confidence: 0.72, rationale: "Baris tanpa order_id tidak dapat direkonsiliasi.", before_samples: ["(kosong)"], after_samples: ["(baris dibuang)"] },
      evidence: { evidence: "0,8% baris tidak memiliki order_id" },
      risk: "destructive",
      confidence: 0.72,
    },
  ];
}

function createUsage(): UsageSummary {
  return {
    org_id: "demo-org",
    period_start: "2026-10-01",
    period_end: "2026-11-01",
    total_calls: 128,
    total_tokens_in: 184320,
    total_tokens_out: 41280,
    total_cost_usd: 0.8134,
    average_latency_ms: 1840,
    by_model: [
      { model: "model-kecil", provider: "openai_compatible", calls: 96, tokens_in: 120000, tokens_out: 24000, cost_usd: 0.4 },
      { model: "model-menengah", provider: "openai_compatible", calls: 32, tokens_in: 64320, tokens_out: 17280, cost_usd: 0.4134 },
    ],
    by_agent: [
      { agent_type: "schema_profiler", calls: 84, tokens: 168000, cost_usd: 0.52 },
      { agent_type: "text_to_sql", calls: 44, tokens: 57600, cost_usd: 0.2934 },
    ],
    daily: Array.from({ length: 14 }, (_, index) => ({ day: `2026-10-${pad(index + 5)}`, cost_usd: Number((0.02 + index * 0.006).toFixed(4)), tokens: 9000 + index * 1200, calls: 6 + index })),
    budget: {
      org_id: "demo-org",
      period_start: "2026-10-01",
      period_end: "2026-11-01",
      monthly_budget_usd: 5,
      spent_usd: 0.8134,
      remaining_usd: 4.1866,
      used_ratio: 0.1627,
      warn_threshold: 0.8,
      is_exhausted: false,
      per_run_token_limit: 20000,
    },
  };
}
