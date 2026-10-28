/**
 * Demo backend (client side).
 *
 * A tiny in-memory implementation of every route in `lib/api.ts`. When demo
 * mode is active, `apiFetch` dispatches here INSTEAD of calling the network, so
 * a demo session is structurally incapable of touching the real backend.
 *
 * Properties that matter:
 * 1. **No I/O.** No `fetch`, no cookies, no storage. State lives in a module
 *    variable and dies with the tab.
 * 2. **Every write mutates the local copy and is visible on the next read**, so
 *    the demo is genuinely hands-on: create a project, run a pipeline, approve a
 *    proposal, rotate a key -- all of it "works", none of it persists.
 * 3. **Honest failures.** Unknown routes throw `DemoHttpError` (a small `404`),
 *    exactly like the mock server, instead of inventing an empty success.
 *
 * This is NOT security-relevant code (it never runs against a server), but it
 * shares the same error shape so the UI's `classifyError` keeps working.
 */

import type {
  AgentProposal,
  AgentRun,
  ApiKey,
  ApiKeyCreated,
  CleaningProposal,
  ProfileReport,
  Pipeline,
  PipelineDAGDTO,
  PipelineVersion,
  Project,
  ProjectSettings,
  Run,
  RunDetail,
  SessionInfo,
  Source,
  ValidationIssue,
} from "@/lib/types";
import { validateDagLocally } from "@/lib/dag-validate";
import { createDemoDb, type DemoDb, type Dict } from "./fixtures";

/** Mirror of the API error envelope so `classifyError` behaves the same. */
export class DemoHttpError extends Error {
  readonly code: string;
  readonly status: number;
  readonly request_id = "demo";

  constructor(message: string, code: string, status: number) {
    super(message);
    this.name = "DemoHttpError";
    this.code = code;
    this.status = status;
  }
}

export interface DemoRequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
}

let state: DemoDb | null = null;

function db(): DemoDb {
  if (state === null) state = createDemoDb();
  return state;
}

/** Wipe every demo mutation and start from the fixtures again. */
export function resetDemoDb(): void {
  state = createDemoDb();
}

const id = (prefix: string) => `${prefix}-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4).toString(36)}`;
const now = () => new Date().toISOString();
const asDict = (value: unknown): Dict => (typeof value === "object" && value !== null ? (value as Dict) : {});
const notFound = (what: string) => new DemoHttpError(`${what} tidak ditemukan di sesi demo.`, "not_found", 404);

/** Small artificial latency so loading states are exercised, never painful. */
function pause(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 60));
}

function topologicalOrder(dag: PipelineDAGDTO): string[] {
  const indegree = new Map<string, number>();
  const adjacency = new Map<string, string[]>();
  for (const node of dag.nodes) {
    indegree.set(node.id, 0);
    adjacency.set(node.id, []);
  }
  for (const edge of dag.edges) {
    if (!adjacency.has(edge.from) || !indegree.has(edge.to)) continue;
    adjacency.get(edge.from)!.push(edge.to);
    indegree.set(edge.to, (indegree.get(edge.to) ?? 0) + 1);
  }
  const ready = [...indegree.entries()].filter(([, degree]) => degree === 0).map(([nodeId]) => nodeId);
  const order: string[] = [];
  while (ready.length > 0) {
    const current = ready.shift()!;
    order.push(current);
    for (const next of adjacency.get(current) ?? []) {
      const degree = (indegree.get(next) ?? 0) - 1;
      indegree.set(next, degree);
      if (degree === 0) ready.push(next);
    }
  }
  return order;
}

/**
 * Current version of one pipeline.
 *
 * Versions are keyed by pipeline id because the real API keeps them separate:
 * saving pipeline A must never change the DAG that pipeline B loads. A demo
 * pipeline created at runtime gets a lazily-created empty version.
 */
function versionFor(pipelineId: string): PipelineVersion {
  const d = db();
  const existing = d.versions[pipelineId];
  if (existing) return existing;

  const created: PipelineVersion = {
    id: id("pv"),
    org_id: "demo-org",
    pipeline_id: pipelineId,
    version: 1,
    dag: { schema_version: 1, nodes: [], edges: [], metadata: {} },
    validation_status: "draft",
    validation_errors: [],
    created_by_kind: "user",
    created_by_user_id: "demo-user",
    created_by_agent_run_id: null,
    parent_version_id: null,
    change_note: "Versi awal demo",
    created_at: now(),
  };
  d.versions[pipelineId] = created;
  return created;
}

function audit(action: string, targetType: string, targetId: string): void {
  db().audit.unshift({
    id: id("a"),
    org_id: "demo-org",
    actor_kind: "user",
    actor_id: "demo-user",
    actor_email: "pengunjung@demo.dataloom",
    action,
    target_type: targetType,
    target_id: targetId,
    metadata: {},
    ip: "10.0.0.5",
    at: now(),
  });
}

// ---------------------------------------------------------------------------
// Route table
// ---------------------------------------------------------------------------
function route(path: string, method: string, body: Dict): unknown {
  const d = db();
  const seg = path.split("/").filter(Boolean);
  const [head, id1, sub, id2, sub2] = seg;

  // --- auth ---------------------------------------------------------------
  // Nothing to authenticate against: every demo auth call returns the demo
  // identity, and logout simply drops the client-side flag.
  if (head === "auth") return d.session;

  // --- projects -----------------------------------------------------------
  if (head === "projects") {
    if (seg.length === 1) {
      if (method === "POST") {
        const created = {
          id: id("p"),
          name: String(body.name ?? "Proyek baru"),
          description: body.description ? String(body.description) : null,
          source_count: 0,
          pipeline_count: 0,
          last_run_status: null,
          updated_at: now(),
        };
        d.projects.push(created);
        audit("project.create", "project", created.id);
        return created;
      }
      return d.projects;
    }

    const projectId = id1 ?? "";
    const project = d.projects.find((candidate) => candidate.id === projectId);

    // /projects/{id}
    if (seg.length === 2) {
      if (method === "PATCH") {
        if (!project) throw notFound("Proyek");
        if (typeof body.name === "string") project.name = body.name;
        if ("description" in body) project.description = body.description ? String(body.description) : null;
        project.updated_at = now();
        audit("project.update", "project", project.id);
      }
      if (method === "DELETE") {
        const index = d.projects.findIndex((candidate) => candidate.id === projectId);
        if (index >= 0) d.projects.splice(index, 1);
        audit("project.delete", "project", projectId);
        return {};
      }
      if (!project) throw notFound("Proyek");
      return {
        id: project.id,
        org_id: "demo-org",
        name: project.name,
        description: project.description,
        settings: d.settings,
        created_at: project.updated_at,
        updated_at: project.updated_at,
      } satisfies Project;
    }

    // /projects/{id}/settings
    if (sub === "settings") {
      if (method === "PUT") {
        Object.assign(d.settings, body);
        audit("project.settings_update", "project", projectId);
      }
      return d.settings;
    }

    // /projects/{id}/sources[/{sid}]
    if (sub === "sources") {
      if (seg.length === 3) {
        if (method === "POST") {
          const rawType = String(asDict(body.config).type ?? "webhook");
          const created: Source = {
            id: id("s"),
            org_id: "demo-org",
            project_id: projectId,
            name: String(body.name ?? "Sumber baru"),
            slug: `sumber-${Date.now().toString(36)}`,
            type: (["webhook", "csv", "api", "postgres"] as const).find((candidate) => candidate === rawType) ?? "webhook",
            status: "connected",
            config_public: {},
            ingest_path: `/v1/ingest/${id("s")}`,
            last_received_at: null,
            created_at: now(),
            updated_at: now(),
          };
          d.sources.push(created);
          if (project) project.source_count = d.sources.filter((source) => source.project_id === projectId).length;
          audit("source.create", "source", created.id);
          return created;
        }
        return d.sources.filter((source) => source.project_id === projectId || !source.project_id);
      }
      const source = d.sources.find((candidate) => candidate.id === id2);
      if (method === "DELETE") {
        const index = d.sources.findIndex((candidate) => candidate.id === id2);
        if (index >= 0) d.sources.splice(index, 1);
        if (project) project.source_count = d.sources.filter((candidate) => candidate.project_id === projectId).length;
        audit("source.delete", "source", id2 ?? "");
        return {};
      }
      return source ?? notFound("Sumber");
    }

    // /projects/{id}/api-keys[/{kid}[/rotate]]
    if (sub === "api-keys") {
      if (seg.length === 3) {
        if (method === "POST") {
          const plaintext = `dl_demo_${id("key")}`;
          const created: ApiKey = {
            id: id("k"),
            org_id: "demo-org",
            project_id: projectId,
            name: String(body.name ?? "Key baru"),
            prefix: plaintext.slice(0, 11),
            created_at: now(),
            last_used_at: null,
            revoked_at: null,
          };
          d.keys.push(created);
          d.lastPlaintextKey = plaintext;
          audit("api_key.create", "api_key", created.id);
          return {
            key: created,
            plaintext_key: plaintext,
            warning: "Simpan key ini sekarang. Nilai lengkap hanya tampil sekali (demo).",
          } satisfies ApiKeyCreated;
        }
        // Keys are project-scoped on the real API; listing every project's keys
        // here made one tenant's demo key appear under another project.
        return d.keys.filter((candidate) => candidate.project_id === projectId);
      }

      const key = d.keys.find((candidate) => candidate.id === id2);
      if (sub2 === "rotate" && method === "POST") {
        if (!key) throw notFound("API key");
        const plaintext = `dl_demo_${id("key")}`;
        key.prefix = plaintext.slice(0, 11);
        key.created_at = now();
        key.revoked_at = null;
        d.lastPlaintextKey = plaintext;
        audit("api_key.rotate", "api_key", key.id);
        return {
          key,
          plaintext_key: plaintext,
          warning: "Simpan key ini sekarang. Nilai lengkap hanya tampil sekali (demo).",
        } satisfies ApiKeyCreated;
      }
      if (method === "DELETE") {
        if (key) {
          key.revoked_at = now();
          audit("api_key.revoke", "api_key", key.id);
        }
        return {};
      }
      return key ?? notFound("API key");
    }

    // /projects/{id}/uploads[/{uid}/complete]
    if (sub === "uploads") {
      if (seg.length === 3 && method === "POST") {
        return {
          upload_id: id("up"),
          dataset_id: id("d"),
          storage_key: `demo/uploads/${Date.now()}`,
          parts: [{ part_number: 1, url: "demo://upload", headers: {} }],
          expires_at: new Date(Date.now() + 36e5).toISOString(),
          max_bytes: 500 * 1024 * 1024,
        };
      }
      if (sub2 === "complete") {
        audit("upload.complete", "upload", id2 ?? "");
        return {
          upload_id: id2 ?? id("up"),
          dataset_id: id("d"),
          status: "completed",
          storage_uri: "demo://upload",
          size_bytes: 0,
          ingest_job_id: null,
        };
      }
      return { upload_id: id2 ?? id("up"), status: "completed", storage_uri: "demo://upload", size_bytes: 0 };
    }

    // /projects/{id}/pipelines
    if (sub === "pipelines") {
      if (method === "POST") {
        const dag = (body.dag ?? { schema_version: 1, nodes: [], edges: [] }) as PipelineDAGDTO;
        const validation = validateDagLocally(dag);
        const created: Pipeline = {
          id: id("pl"),
          org_id: "demo-org",
          project_id: projectId,
          name: String(body.name ?? "Pipeline baru"),
          description: body.description ? String(body.description) : null,
          current_version_id: id("pv"),
          current_version: 1,
          schedule_cron: null,
          created_by: "demo-user",
          created_at: now(),
          updated_at: now(),
        };
        d.pipelines.push(created);
        d.versions[created.id] = {
          id: created.current_version_id ?? id("pv"),
          org_id: "demo-org",
          pipeline_id: created.id,
          version: 1,
          dag,
          validation_status: validation.valid ? "valid" : "invalid",
          validation_errors: validation.issues,
          created_by_kind: "user",
          created_by_user_id: "demo-user",
          created_by_agent_run_id: null,
          parent_version_id: null,
          change_note: "Dibuat di sesi demo",
          created_at: now(),
        };
        if (project) project.pipeline_count = d.pipelines.filter((candidate) => candidate.project_id === projectId).length;
        audit("pipeline.create", "pipeline", created.id);
        return created;
      }
      return d.pipelines.filter((candidate) => candidate.project_id === projectId);
    }

    // /projects/{id}/datasets[/{did}[/preview|/quality]]
    if (sub === "datasets") {
      if (seg.length === 3) return d.datasets.filter((candidate) => candidate.project_id === projectId);
      const dataset = d.datasets.find((candidate) => candidate.id === id2);
      if (sub2 === "preview") {
        if (!dataset) throw notFound("Dataset");
        // POSITIONAL rows, matching `DatasetPreview.rows: list[list[scalar]]`.
        // Rows are per dataset, so every project previews its own shape instead
        // of the sales example.
        return {
          dataset_id: dataset.id,
          layer: dataset.layer,
          columns: dataset.schema,
          rows: d.previewRows[dataset.id] ?? [],
          total_rows: dataset.row_count ?? 0,
          truncated: false,
        };
      }
      if (sub2 === "quality") {
        return {
          columns: (dataset?.schema ?? []).map((column, index) => ({
            name: column.name,
            dtype: column.dtype,
            null_ratio: index === 1 ? 0.004 : 0,
            distinct_count: 12 + index * 200,
            histogram: [],
          })),
          reasons: { duplicate_value: 47, null_value: 6 },
        };
      }
      if (!dataset) throw notFound("Dataset");
      return dataset;
    }

    // /projects/{id}/runs
    if (sub === "runs") return d.runs.filter((candidate) => candidate.project_id === projectId);

    // /projects/{id}/lineage
    if (sub === "lineage") {
      // Lineage is per project: returning the sales graph for every project was
      // one of the "all data belongs to p-1" leftovers.
      return d.lineage[projectId] ?? { nodes: [], edges: [] };
    }
  }

  // --- pipelines ----------------------------------------------------------
  if (head === "pipelines") {
    if (id1 === "validate") {
      const dag = (body.dag ?? { schema_version: 1, nodes: [], edges: [] }) as PipelineDAGDTO;
      const result = validateDagLocally(dag);
      d.lastValidation = result.issues;
      return { valid: result.valid, issues: result.issues, topological_order: topologicalOrder(dag) };
    }

    const pipeline = d.pipelines.find((candidate) => candidate.id === id1);

    if (seg.length === 2) {
      if (method === "PATCH") {
        const version = versionFor(id1 ?? "");
        const dag = body.dag as PipelineDAGDTO | undefined;
        if (dag) version.dag = dag;
        version.version += 1;
        version.created_at = now();
        version.change_note = typeof body.change_note === "string" ? body.change_note : version.change_note;
        const result = validateDagLocally(version.dag);
        version.validation_status = result.valid ? "valid" : "invalid";
        version.validation_errors = result.issues;
        if (pipeline) {
          pipeline.current_version = version.version;
          pipeline.current_version_id = version.id;
          pipeline.updated_at = now();
        }
        audit("pipeline.update", "pipeline", id1 ?? "");
        return version;
      }
      if (!pipeline) throw notFound("Pipeline");
      return pipeline;
    }

    if (sub === "versions") return [versionFor(id1 ?? "")];

    if (sub === "runs") {
      const version = versionFor(id1 ?? "");
      const created: Run = {
        id: id("run"),
        org_id: "demo-org",
        project_id: pipeline?.project_id ?? "p-1",
        pipeline_id: id1 ?? "",
        pipeline_version_id: version.id,
        version: version.version,
        trigger: "manual",
        status: "queued",
        current_node_id: null,
        queued_at: now(),
        started_at: null,
        finished_at: null,
        duration_ms: null,
        rows_in: 0,
        rows_out: 0,
        rows_quarantined: 0,
        quarantine_ratio: null,
        retry_total: 0,
        error_code: null,
        error_message: null,
        created_at: now(),
      };
      d.runs.unshift(created);
      audit("run.create", "run", created.id);
      return { run: created, stream_url: `/v1/runs/${created.id}/events` };
    }
  }

  // --- runs ---------------------------------------------------------------
  if (head === "runs") {
    const runId = id1 ?? "";
    const run = d.runs.find((candidate) => candidate.id === runId);

    if (sub === "events") return { stream: true };

    if (method === "DELETE") {
      if (run) {
        run.status = "cancelled";
        run.finished_at = now();
        audit("run.cancel", "run", run.id);
      }
      return { run_id: runId, status: "cancelled", cancelled_at: now() };
    }

    if (method === "GET") {
      if (!run) throw notFound("Run");
      // Node timelines and gate checklists are keyed per run: a queued run has
      // neither, and a finished one carries its own - not only run-1's.
      const nodes = d.runNodes[runId] ?? [];
      const gate = d.gates[runId] ?? null;
      return { ...run, nodes, gate } satisfies RunDetail;
    }
  }

  // --- datasets (bare) ----------------------------------------------------
  if (head === "datasets") {
    const dataset = d.datasets.find((candidate) => candidate.id === id1);
    if (dataset) return dataset;
  }

  // --- agents -------------------------------------------------------------
  if (head === "agents") {
    if (id1 === "profile") return { agent_run: d.agentRun };
    if (id1 === "build-pipeline") {
      // Keep the two agent types separate: overwriting d.agentRun made the
      // profiler run disappear from the workspace after one build attempt.
      const builder: AgentRun = {
        ...d.agentRun,
        id: id("ar"),
        agent_type: "pipeline_builder",
        thread_id: "pipeline_builder:demo",
        status: "waiting_approval",
        current_step: "waiting_approval",
        started_at: now(),
        created_at: now(),
        finished_at: null,
      };
      d.builderRun = builder;
      audit("agent.build_pipeline", "agent_run", builder.id);
      return { agent_run: builder };
    }
if (id1 === "ask" && seg.length === 2) {
      const question = String(body.question ?? "");
      audit("agent.ask", "agent_run", "ar-sql");
      return {
        agent_run: { id: id("ar-sql"), thread_id: "text_to_sql:demo" },
        answer: { ...d.sqlAnswer, question },
      };
    }
    // `/agents/ask/schema`: the only table list a demo user may query.
    if (id1 === "ask" && sub === "schema") {
      return {
        tables: [
          { name: "silver_orders", layer: "silver", columns: ["order_id", "tgl", "amount"] },
          { name: "gold_penjualan_bulanan", layer: "gold", columns: ["bulan", "total_pendapatan"] },
        ],
      };
    }
  }

  if (head === "agent-runs") {
    const agentRunId = id1 ?? "";
    if (sub === "proposals") {
      // Only the demo profiler run has proposals; an unknown run id gets an
      // empty list rather than another run's proposals.
      return d.proposals.filter((proposal) => proposal.agent_run_id === agentRunId);
    }
    if (sub === "events") return d.agentEvents;
    if (sub === "profile") {
      return {
        status: "ok",
        dataset_id: "d-1",
        columns: [],
        anomalies: [],
        sampled_rows: 1000,
        total_rows: 124000,
        cleaning_proposals: d.proposals.map((proposal) => {
          const payload = asDict(proposal.payload);
          return {
            proposal_id: String(payload.proposal_id ?? proposal.id),
            op: String(payload.op ?? "trim") as CleaningProposal["op"],
            column: String(payload.column ?? ""),
            params: asDict(payload.params),
            evidence: String(asDict(proposal.evidence).evidence ?? "Bukti demo."),
            risk: proposal.risk,
            confidence: proposal.confidence ?? 0,
            rationale: payload.rationale ? String(payload.rationale) : null,
            before_samples: (payload.before_samples as string[] | undefined) ?? [],
            after_samples: (payload.after_samples as string[] | undefined) ?? [],
          };
        }),
        open_questions: [],
        summary: "Profil demo: 3 usulan pembersihan siap diputuskan.",
        generated_at: now(),
      } satisfies ProfileReport;
    }
    if (sub === "apply") {
      const pipelineId = String(body.pipeline_id ?? "pl-1");
      const version = versionFor(pipelineId);
      version.version += 1;
      version.change_note = "Terapkan usulan agent (demo)";
      const applied = d.proposals.filter((proposal) => proposal.status === "approved").length;
      audit("agent.apply", "pipeline", pipelineId);
      return {
        pipeline_id: pipelineId,
        pipeline_version_id: version.id,
        version: version.version,
        applied_proposals: applied,
      };
    }
    // Return the run the caller asked for: the profiler run or the builder
    // run, instead of always answering with the profiler run.
    return agentRunId === d.builderRun.id ? d.builderRun : d.agentRun;
  }

  if (head === "proposals") {
    const proposal = d.proposals.find((candidate) => candidate.id === id1);
    if (!proposal) throw notFound("Usulan");
    if (sub === "decision") {
      proposal.status = body.decision === "approve" ? "approved" : "rejected";
      proposal.decision_note = typeof body.note === "string" ? body.note : null;
      proposal.decided_by = "demo-user";
      proposal.decided_at = now();
      audit("proposal.decision", "agent_proposal", proposal.id);
      return proposal;
    }
    return proposal;
  }

  // --- usage --------------------------------------------------------------
  if (head === "usage") {
    if (id1 === "llm-calls") return d.llmCalls;
    if (id1 === "queue") {
      return [
        {
          source_id: "s-1",
          stream: "ingest:demo-org:s-1",
          length: 42,
          pending: 0,
          consumers: 1,
          measured_at: now(),
        },
      ];
    }
    if (id1 === "budget") {
      if (method === "PATCH") {
        const budget = d.usage.budget;
        if (typeof body.monthly_budget_usd === "number") budget.monthly_budget_usd = body.monthly_budget_usd;
        if (typeof body.per_run_token_limit === "number") budget.per_run_token_limit = body.per_run_token_limit;
        budget.remaining_usd = Math.max(0, budget.monthly_budget_usd - budget.spent_usd);
        budget.used_ratio =
          budget.monthly_budget_usd > 0 ? Math.min(1, budget.spent_usd / budget.monthly_budget_usd) : 0;
        budget.is_exhausted = budget.remaining_usd <= 0;
        audit("usage.budget_update", "organization", "demo-org");
      }
      return d.usage.budget;
    }
    return d.usage;
  }

  // --- settings -----------------------------------------------------------
  if (head === "settings") {
    if (id1 === "members") {
      if (seg.length === 3) {
        // `/settings/members/{membershipId}` -> the id is the third segment.
        const membershipId = sub ?? "";
        if (method === "PATCH") {
          const member = d.members.find((candidate) => candidate.id === membershipId);
          if (!member) throw notFound("Anggota");
          if (typeof body.role === "string") {
            member.role = body.role as typeof member.role;
            audit("membership.role_update", "membership", member.id);
          }
          return member;
        }
        if (method === "DELETE") {
          const index = d.members.findIndex((candidate) => candidate.id === membershipId);
          if (index >= 0) d.members.splice(index, 1);
          audit("membership.remove", "membership", membershipId);
          return {};
        }
      }
      return d.members;
    }
    if (id1 === "audit-logs") return d.audit;
  }

  // --- installation -------------------------------------------------------
  if (head === "installation") {
    if (id1 === "llm") {
      if (sub === "test" && method === "POST") {
        audit("installation.llm_test", "installation_settings", "1");
        return { ok: true, latency_ms: 214, error_code: null, error_message: null };
      }
      if (method === "PUT") {
        const status = d.installation;
        if (typeof body.model_default === "string") status.model_default = body.model_default;
        if (typeof body.model_small === "string") status.model_small = body.model_small;
        if (typeof body.timeout_seconds === "number") status.timeout_seconds = body.timeout_seconds;
        if (typeof body.price_in_per_1k === "number") status.price_in_per_1k = body.price_in_per_1k;
        if (typeof body.price_out_per_1k === "number") status.price_out_per_1k = body.price_out_per_1k;
        if (typeof body.base_url === "string") {
          try {
            status.base_url_host = new URL(body.base_url).host;
          } catch {
            status.base_url_host = body.base_url;
          }
        }
        if (typeof body.api_key === "string" && body.api_key.length > 0) {
          status.api_key_configured = true;
          status.api_key_hint = body.api_key.slice(-4);
        }
        status.updated_at = now();
        status.updated_by = "pengunjung@demo.dataloom";
        audit("installation.llm_update", "installation_settings", "1");
        return status;
      }
      return d.installation;
    }
    if (id1 === "admins" && method === "POST") {
      const email = String(body.email ?? "baru@demo.dataloom");
      audit("installation.admin_grant", "user", email);
      return {
        user_id: id("user"),
        email,
        granted_by: "pengunjung@demo.dataloom",
        reason: body.reason ?? null,
        created_at: now(),
      };
    }
  }

  throw notFound(`Route demo ${method} ${path}`);
}

/**
 * Dispatch one API call against the demo backend.
 *
 * The signature intentionally mirrors `apiFetch` so `lib/api.ts` can swap the
 * implementation with a single branch.
 */
export async function demoRequest<T>(path: string, options: DemoRequestOptions = {}): Promise<T> {
  await pause();
  const clean = path.split("?")[0] ?? path;
  const normalized = clean.length > 1 ? clean.replace(/\/+$/, "") : clean;

  return route(normalized, options.method ?? "GET", asDict(options.body)) as T;
}

/** Exposed for tests/inspection: current demo session identity. */
export function demoSession(): SessionInfo {
  return db().session;
}
