"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDownToLine,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Database,
  Filter,
  Merge,
  Play,
  Redo2,
  Rocket,
  Save,
  Sigma,
  Sparkles,
  Trash2,
  Undo2,
  XCircle,
} from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { notify } from "@/lib/notify";

import { EmptyState } from "@/components/domain/empty-state";
import { ErrorState } from "@/components/domain/error-state";
import {
  PipelineCanvas,
  type CanvasHistoryState,
  type PipelineCanvasHandle,
} from "@/components/domain/pipeline-canvas";
import { StatusBadge } from "@/components/domain/status-badge";
import { BlockSkeleton, LoadingAnnouncer } from "@/components/domain/state-loading";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { useCanMutate } from "@/components/providers/session-provider";
import { useProject } from "@/components/providers/project-provider";
import { api, classifyError } from "@/lib/api";
import { validateDagLocally } from "@/lib/dag-validate";
import { t } from "@/lib/i18n/id";
import type { NodeType, PipelineDAGDTO, PipelineNodeDTO, ValidationIssue } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Node library grouped exactly as PRD P5 lists it. */
const NODE_LIBRARY: { group: string; items: { type: NodeType; label: string; icon: typeof Database }[] }[] = [
  {
    group: t.pipelines.nodeGroups.sources,
    items: [{ type: "source", label: "Sumber", icon: Database }],
  },
  {
    group: t.pipelines.nodeGroups.clean,
    items: [
      { type: "validate", label: "Validasi", icon: Filter },
      { type: "clean", label: "Bersihkan", icon: Sparkles },
    ],
  },
  {
    group: t.pipelines.nodeGroups.transform,
    items: [{ type: "transform", label: "Transformasi", icon: Merge }],
  },
  {
    group: t.pipelines.nodeGroups.aggregate,
    items: [{ type: "aggregate", label: "Agregasi", icon: Sigma }],
  },
  {
    group: t.pipelines.nodeGroups.sinks,
    items: [{ type: "sink", label: "Gudang Data", icon: ArrowDownToLine }],
  },
];

const DEFAULT_PARAMS: Record<NodeType, Record<string, unknown>> = {
  source: { kind: "source", source_id: "", batch_size_rows: 5000 },
  validate: { kind: "validate", rules: [] },
  clean: { kind: "clean", operations: [{ op: "trim", column: "", params: {}, risk: "low" }] },
  transform: { kind: "transform", select_columns: [], mappings: [], derived: [], filter: null },
  aggregate: {
    kind: "aggregate",
    group_by: [],
    metrics: [{ alias: "total", function: "count", column: null }],
  },
  sink: { kind: "sink", layer: "gold", dataset_name: "gold_dataset", write_mode: "overwrite" },
};

const EMPTY_DAG: PipelineDAGDTO = { schema_version: 1, nodes: [], edges: [], metadata: {} };

export default function PipelineEditorPage() {
  const params = useParams<{ pipelineId: string }>();
  const pipelineId = params.pipelineId;
  const isNew = pipelineId === "new";
  const router = useRouter();
  const queryClient = useQueryClient();
  const { activeProjectId } = useProject();
  const canMutate = useCanMutate();

  const [dag, setDag] = useState<PipelineDAGDTO>(EMPTY_DAG);
  // The canvas is uncontrolled; positions live inside React Flow until we pull
  // them. Every validate/save reads the live graph through this handle.
  const canvasRef = useRef<PipelineCanvasHandle>(null);
  /** Live graph from the canvas, including positions the user dragged. */
  const currentDag = () => canvasRef.current?.snapshot() ?? dag;
  const [issues, setIssues] = useState<ValidationIssue[]>([]);
  /** Mirrors `issues`: false as soon as the local validator finds a problem. */
  const [localValid, setLocalValid] = useState(false);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  /**
   * Undo/redo availability. The canvas owns the history; this mirror only
   * exists so the toolbar buttons can be disabled when there is nothing to
   * undo or redo. It is pushed up by the canvas, never read back into it.
   */
  const [history, setHistory] = useState<CanvasHistoryState>({ canUndo: false, canRedo: false });
  const [pipelineName, setPipelineName] = useState("Pipeline baru");
  const [consoleOpen, setConsoleOpen] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [aiPrompt, setAiPrompt] = useState("");

  /**
   * Instant feedback: re-run the local validator a beat after every canvas
   * change so broken nodes light up on their own, without pressing "Validasi".
   * The 300 ms debounce keeps a burst of drag/connect events to one pass.
   */
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const graph = canvasRef.current?.snapshot() ?? dag;
      const result = validateDagLocally(graph);
      setIssues(result.issues);
      setLocalValid(result.valid);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [dag]);


  // Deep link from the empty state: /pipelines/new?mode=ai opens the builder.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("mode") === "ai") {
      setAiOpen(true);
    }
  }, []);

  const pipelineQuery = useQuery({
    queryKey: ["pipeline", pipelineId],
    queryFn: () => api.pipelines.get(pipelineId),
    enabled: !isNew,
  });

  const versionQuery = useQuery({
    queryKey: ["pipeline-versions", pipelineId],
    queryFn: () => api.pipelines.versions(pipelineId),
    enabled: !isNew && Boolean(pipelineQuery.data),
  });

  // Load the current version's DAG once (never overwrite user edits afterwards).
  // Validation state is NOT copied from the server version: the debounced local
  // validator below owns `issues`, so what the user sees always matches the
  // graph currently on the canvas.
  useEffect(() => {
    const versions = versionQuery.data;
    if (!versions || versions.length === 0) return;
    const current = versions[0];
    if (current) {
      setDag(current.dag);
      setPipelineName(pipelineQuery.data?.name ?? pipelineName);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [versionQuery.data]);

  const selectedNode = useMemo(
    () => dag.nodes.find((node) => node.id === selectedNodeId) ?? null,
    [dag.nodes, selectedNodeId],
  );

  /*
   * The params textarea needs its own draft state: JSON is only valid once the
   * whole object is typed, so binding the field directly to the parsed params
   * made every keystroke impossible (the previous code silently dropped
   * in-progress text). Invalid JSON is kept in the draft and reported below.
   */
  const [paramsDraft, setParamsDraft] = useState<string>("");
  const [paramsInvalid, setParamsInvalid] = useState(false);

  /*
   * The params draft must only reset when the SELECTED NODE CHANGES, not on
   * every render. Depending on `selectedNode` (a new object each render) made
   * this effect set state on every render, which fought React Flow's drag loop
   * and left nodes stuck in place.
   */
  const selectedParamsJson = selectedNode ? JSON.stringify(selectedNode.params) : "";
  useEffect(() => {
    if (!selectedNode) return;
    // Keep the user's own formatting when their draft already parses to the
    // same object: re-pretty-printing on every valid keystroke made the caret
    // jump while typing.
    setParamsDraft((current) => {
      try {
        if (JSON.stringify(JSON.parse(current)) === selectedParamsJson) return current;
      } catch {
        // Not valid JSON yet: fall through and show the canonical form.
      }
      return JSON.stringify(selectedNode.params, null, 2);
    });
    setParamsInvalid(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedNode?.id, selectedParamsJson]);

  const applyParamsDraft = (text: string) => {
    setParamsDraft(text);
    try {
      const parsed = JSON.parse(text) as Record<string, unknown>;
      setParamsInvalid(false);
      updateSelectedNode({ params: parsed as PipelineNodeDTO["params"] });
    } catch {
      setParamsInvalid(true);
    }
  };

  const validateMutation = useMutation({
    mutationFn: () => api.pipelines.validate(currentDag()),
    onSuccess: (result) => {
      // Defensive: a contract violation must degrade to "no issues", never
      // poison state with `undefined` (which crashed the console rendering).
      setIssues(Array.isArray(result.issues) ? result.issues : []);
      setLocalValid(result.valid);
      setConsoleOpen(true);
      if (result.valid) {
        notify.success(t.pipelines.validBanner, { description: t.notify.validationOk });
      } else {
        notify.warning(t.pipelines.invalidBanner, { description: t.notify.validationIssues });
      }
    },
    onError: (error: Error) => notify.error(t.notify.failedTitle, { description: error.message }),
  });

  /**
   * The "Validasi" button. Local first: when the graph already fails a rule
   * there is nothing the server could add, so we skip the request and show the
   * problems immediately. Only a locally valid graph is confirmed by the API,
   * which stays the authoritative check (it also returns the run order).
   */
  const runValidation = () => {
    const graph = currentDag();
    const local = validateDagLocally(graph);
    setIssues(local.issues);
    setLocalValid(local.valid);
    setConsoleOpen(true);

    if (!local.valid) {
      // Local problems are node-precise; keep them instead of a server summary.
      notify.warning(t.pipelines.invalidBanner, {
        description: `${local.issues.length} masalah ditemukan. ${t.notify.validationIssues}`,
      });
      return;
    }
    validateMutation.mutate();
  };


  const saveMutation = useMutation({
    mutationFn: async () => {
      const graph = currentDag();
      if (isNew) {
        return api.pipelines.create(activeProjectId as string, {
          name: pipelineName,
          dag: graph,
        });
      }
      return api.pipelines.updateDag(pipelineId, { dag: graph, change_note: "Disimpan dari kanvas" });
    },
onSuccess: async (result) => {
      const savedVersion = "version" in result ? result.version : result.current_version;
      notify.success(`Versi ${savedVersion} tersimpan.`, {
        description: t.notify.pipelineSaved,
      });
      await queryClient.invalidateQueries({ queryKey: ["pipelines", activeProjectId] });
      if (isNew && "id" in result) {
        router.replace(`/pipelines/${result.id}`);
      } else {
        await queryClient.invalidateQueries({ queryKey: ["pipeline", pipelineId] });
        await queryClient.invalidateQueries({ queryKey: ["pipeline-versions", pipelineId] });
      }
    },
    onError: (error: Error) => notify.error(t.notify.failedTitle, { description: error.message }),
  });

  const runMutation = useMutation({
    mutationFn: () => api.pipelines.run(pipelineId),
    onSuccess: (result) => {
      notify.success(`Run ${result.run.id} dimulai.`, {
        description: t.notify.runStarted,
      });
      router.push(`/runs/${result.run.id}`);
    },
    onError: (error: Error) => notify.error(t.notify.failedTitle, { description: error.message }),
  });

  const buildMutation = useMutation({
    mutationFn: () => api.agents.startBuilder(activeProjectId as string, { description: aiPrompt }),
    onSuccess: () => {
      notify.info("Agent sedang menyusun draf.", {
        description: t.notify.agentDraft,
      });
      setAiOpen(false);
      router.push("/agents");
    },
    onError: (error: Error) => notify.error(t.notify.failedTitle, { description: error.message }),
  });

  const addNode = (type: NodeType) => {
    if (!canMutate) return;
    /*
     * The canvas is uncontrolled, so appending to the page's DAG state alone
     * would leave the node invisible on screen (and outside the undo stack).
     * The handle appends it inside React Flow and reports the new DAG back.
     */
    const handle = canvasRef.current;
    if (handle) {
      const createdId = handle.addNode(type);
      if (createdId) setSelectedNodeId(createdId);
      return;
    }
    const id = `n_${type}_${dag.nodes.length + 1}`;
    const position = { x: dag.nodes.length * 280, y: 40 };
    const node: PipelineNodeDTO = {
      id,
      type,
      label: NODE_LIBRARY.flatMap((group) => group.items).find((item) => item.type === type)?.label,
      params: DEFAULT_PARAMS[type] as Record<string, unknown> & { kind: string },
      position,
    };
    const last = dag.nodes.at(-1);
    const edges = last ? [...dag.edges, { from: last.id, to: id }] : dag.edges;
    setDag({ ...dag, nodes: [...dag.nodes, node], edges });
    setSelectedNodeId(id);
  };

  const updateSelectedNode = (patch: Partial<PipelineNodeDTO>) => {
    if (!selectedNode) return;
    /*
     * The canvas is the source of truth for node content, not this page's
     * `dag` copy (that copy is only what the canvas last reported). Writing
     * here alone updated the form but not the card, and the next save would
     * have persisted the old values.
     */
    const handle = canvasRef.current;
    if (handle) {
      handle.patchNode(selectedNode.id, patch);
      return;
    }
    setDag({
      ...dag,
      nodes: dag.nodes.map((node) => (node.id === selectedNode.id ? { ...node, ...patch } : node)),
    });
  };

  /**
   * Maps a `ValidationIssue` back to a node id.
   *
   * `loc` uses the `["nodes", index, ...]` shape that `dagToFlow` already reads,
   * which works for both our local issues and the server's Pydantic `loc`. The
   * server also fills `node_ref` with the node INDEX as a string (see
   * `pipeline_service.format_dag_errors`), so it is only a fallback and its
   * numeric form is resolved through the same index lookup.
   */
  const nodeIdForIssue = (issue: ValidationIssue): string | null => {
    const loc = issue.loc;
    if (Array.isArray(loc) && loc[0] === "nodes" && typeof loc[1] === "number") {
      return dag.nodes[loc[1]]?.id ?? null;
    }
    const ref = issue.node_ref;
    if (typeof ref === "string" && ref.length > 0) {
      const direct = dag.nodes.find((node) => node.id === ref);
      if (direct) return direct.id;
      if (/^\d+$/.test(ref)) return dag.nodes[Number(ref)]?.id ?? null;
    }
    return null;
  };

  /** Clicking a console line selects that node on the canvas. */
  const selectIssueNode = (nodeId: string) => {
    const handle = canvasRef.current;
    if (handle) {
      handle.selectNode(nodeId);
      return;
    }
    setSelectedNodeId(nodeId);
  };


  if (!isNew && pipelineQuery.isError) {
    const kind = classifyError(pipelineQuery.error).kind;
    return (
      <div className="page-container">
        <ErrorState
          kind={kind}
          message={classifyError(pipelineQuery.error).message}
          requestId={classifyError(pipelineQuery.error).requestId}
          onRetry={kind === "not_found" ? () => router.replace("/pipelines") : () => void pipelineQuery.refetch()}
        />
      </div>
    );
  }

  if (!isNew && pipelineQuery.isLoading) {
    return (
      <div className="page-container space-y-4">
        <LoadingAnnouncer />
        <BlockSkeleton height={48} />
        <BlockSkeleton height={520} />
      </div>
    );
  }

  const currentVersion = versionQuery.data?.[0];

  /*
   * "Jalankan" executes the last SAVED version on the server, while the user
   * validates the graph that is on screen. Without this guard the two diverge:
   * you fix a node, the canvas turns green, you press Run, and the server runs
   * the old DAG. Comparing the live graph with the saved one is the honest
   * gate, and it also explains itself in the tooltip.
   */
  const savedDagJson = currentVersion ? JSON.stringify(currentVersion.dag) : null;
  const isDirty = isNew || savedDagJson === null || JSON.stringify(dag) !== savedDagJson;
  const readOnlyForLibrary = !canMutate;
  /** Why save/run are blocked, phrased for a tooltip. */
  const invalidTitle =
    issues.length === 0
      ? "Pipeline belum lolos validasi."
      : `DAG belum valid (${issues.length} masalah). Perbaiki node yang ditandai merah: ${issues[0]?.msg ?? ""}`;
  const saveDisabledTitle = !canMutate
    ? t.state.disabledTooltip
    : !localValid
      ? invalidTitle
      : paramsInvalid
        ? "JSON parameter node belum valid."
        : dag.nodes.length === 0
          ? "Kanvas masih kosong."
          : undefined;
  const runDisabledTitle = !canMutate
    ? t.state.disabledTooltip
    : !localValid
      ? invalidTitle
      : isDirty
        ? "Ada perubahan yang belum disimpan. Simpan versi dulu agar yang dijalankan sama dengan yang Anda lihat."
        : undefined;


  return (
    /*
     * `--app-chrome` is published by AppShell from the measured banner+topbar
     * height. `100dvh` (not `100vh`) is what mobile browsers actually show once
     * the URL bar is accounted for; the pair keeps the canvas exactly one
     * viewport tall with no phantom page scrollbar.
     */
    <div className="flex h-[calc(100dvh-var(--app-chrome,3.5rem))] flex-col">
      {/* Top toolbar (PRD P5) */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-background px-3 py-2">
        {/*
          The visible control is an editable field, but the page still needs a
          real heading for assistive tech and for the document outline. The
          heading is visually hidden because the field already shows the name.
        */}
        <h1 className="sr-only">Pipeline {pipelineName}</h1>
        <Input
          value={pipelineName}
          onChange={(event) => setPipelineName(event.target.value)}
          className="h-8 w-56"
          aria-label="Nama pipeline"
          disabled={!canMutate}
        />
        <Badge variant="outline" className="gap-1">
          v{currentVersion?.version ?? 1} {t.pipelines.draft}
        </Badge>
        {currentVersion ? <StatusBadge status={currentVersion.validation_status === "valid" ? "validated" : "draft"} /> : null}

        <div className="ml-auto flex flex-wrap items-center gap-2">
          {/*
            Undo/redo mirror the canvas history. They sit before the DAG-level
            actions so the reading order matches the usual editor layout:
            edit controls, then validate, then save/run.
          */}
          <Button
            size="sm"
            variant="outline"
            onClick={() => canvasRef.current?.undo()}
            disabled={!canMutate || !history.canUndo}
            title={
              canMutate
                ? history.canUndo
                  ? "Urungkan perubahan terakhir (Ctrl+Z)"
                  : "Belum ada perubahan yang bisa diurungkan"
                : t.state.disabledTooltip
            }
            aria-label="Urungkan perubahan terakhir"
          >
            <Undo2 className="h-3.5 w-3.5" />
            Urungkan
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => canvasRef.current?.redo()}
            disabled={!canMutate || !history.canRedo}
            title={
              canMutate
                ? history.canRedo
                  ? "Ulangi perubahan yang diurungkan (Ctrl+Shift+Z atau Ctrl+Y)"
                  : "Belum ada perubahan yang bisa diulang"
                : t.state.disabledTooltip
            }
            aria-label="Ulangi perubahan yang diurungkan"
          >
            <Redo2 className="h-3.5 w-3.5" />
            Ulangi
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={runValidation}
            disabled={validateMutation.isPending}
            title="Periksa DAG sekarang. Validasi lokal berjalan lebih dulu, server hanya dikonfirmasi bila lokal sudah bersih."
          >
            <CheckCircle2 className="h-3.5 w-3.5" />
            {t.pipelines.validate}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setAiOpen(true)}
            disabled={!canMutate}
            title={canMutate ? undefined : t.state.disabledTooltip}
          >
            <Sparkles className="h-3.5 w-3.5" />
            {t.pipelines.askAi}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => saveMutation.mutate()}
            disabled={!canMutate || saveMutation.isPending || !localValid || paramsInvalid}
            title={saveDisabledTitle}
          >
            <Save className="h-3.5 w-3.5" />
            {t.pipelines.saveVersion}
          </Button>
          <Button
            size="sm"
            onClick={() => runMutation.mutate()}
            disabled={!canMutate || isNew || runMutation.isPending || !localValid || isDirty}
            title={runDisabledTitle}
          >
            <Play className="h-3.5 w-3.5" />
            {t.pipelines.run}
          </Button>
        </div>
      </div>

      {/* Mobile notice: canvas is read-only below 768px (PRD 12.5) */}
      <p className="border-b border-warning/30 bg-warning/10 px-3 py-1 text-[11px] text-warning-text lg:hidden">
        {t.pipelines.mobileReadOnly}
      </p>

      <div className="flex min-h-0 flex-1">
        {/* Left: node library. Items are DRAG sources for the canvas and
            double-click (or Enter) to append, so keyboard-only users can
            build a pipeline without pointer interaction. */}
        <aside className="hidden w-56 shrink-0 overflow-y-auto border-r border-border bg-sidebar p-3 lg:block">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t.pipelines.library}
          </h2>
          <p className="mb-2 text-[11px] text-muted-foreground">
            Tarik ke kanvas, atau tekan Enter untuk menambah di akhir alur.
          </p>
          {NODE_LIBRARY.map((group) => (
            <div key={group.group} className="mb-3">
              <p className="mb-1 text-[11px] font-medium text-muted-foreground">{group.group}</p>
              <ul className="space-y-1">
                {group.items.map((item) => {
                  const Icon = item.icon;
                  return (
                    <li key={`${group.group}-${item.type}`}>
                      <button
                        type="button"
                        draggable={!readOnlyForLibrary}
                        onDragStart={(event) => {
                          event.dataTransfer.setData("application/x-dataloom-node", item.type);
                          event.dataTransfer.effectAllowed = "copy";
                        }}
                        onClick={() => addNode(item.type)}
                        disabled={!canMutate}
                        title={canMutate ? `Tambah node ${item.label}` : t.state.disabledTooltip}
                        aria-label={`Tambah node ${item.label}`}
                        className={cn(
                          "flex w-full cursor-grab items-center gap-2 rounded-lg border border-border bg-background px-2 py-1.5 text-left text-xs hover:bg-muted active:cursor-grabbing disabled:cursor-not-allowed disabled:opacity-50",
                          readOnlyForLibrary && "cursor-not-allowed",
                        )}
                      >
                        <Icon className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
                        {item.label}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </aside>

        {/* Center: canvas */}
        <div className="relative min-w-0 flex-1">
          {dag.nodes.length === 0 ? (
            <div className="flex h-full items-center justify-center p-6">
              <EmptyState
                title="Kanvas kosong"
                description="Tambahkan node dari pustaka, atau minta agent menyusun pipeline dari deskripsi."
                primaryAction={{
                  label: t.pipelines.askAi,
                  onClick: () => setAiOpen(true),
                  disabled: !canMutate,
                  disabledReason: t.state.disabledTooltip,
                }}
              />
            </div>
          ) : (
            <PipelineCanvas
              ref={canvasRef}
              dag={dag}
              issues={issues}
              readOnly={!canMutate}
              onChange={setDag}
              onHistoryChange={setHistory}
              onSelectNode={setSelectedNodeId}
              selectedNodeId={selectedNodeId}
            />
          )}
        </div>

        {/* Right: properties */}
        <aside className="hidden w-80 shrink-0 overflow-y-auto border-l border-border bg-background p-3 xl:block">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t.pipelines.properties}
          </h2>
          {selectedNode ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-2">
                <h3 className="truncate text-sm font-semibold text-foreground">{selectedNode.label}</h3>
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-destructive-text hover:text-destructive-text"
                  disabled={!canMutate}
                  title={canMutate ? "Hapus node ini" : t.state.disabledTooltip}
                  onClick={() => {
                    const handle = canvasRef.current;
                    if (handle) {
                      handle.deleteNode(selectedNode.id);
                    } else {
                      setDag((current) => ({
                        ...current,
                        nodes: current.nodes.filter((node) => node.id !== selectedNode.id),
                        edges: current.edges.filter(
                          (edge) => edge.from !== selectedNode.id && edge.to !== selectedNode.id,
                        ),
                      }));
                    }
                    setSelectedNodeId(null);
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Hapus
                </Button>
              </div>
              <div className="space-y-1">
                <Label htmlFor="node-label">Label</Label>
                <Input
                  id="node-label"
                  value={selectedNode.label ?? ""}
                  onChange={(event) => updateSelectedNode({ label: event.target.value })}
                  disabled={!canMutate}
                />
              </div>

              <div className="space-y-1">
                <Label htmlFor="node-type">Tipe</Label>
                <Input id="node-type" value={selectedNode.type} readOnly disabled />
              </div>

              {selectedNode.type === "clean" ? (
                <div className="space-y-1">
                  <Label htmlFor="clean-op">{t.pipelines.params.operation}</Label>
                  <Select
                    value={String(
                      ((selectedNode.params.operations as { op?: string }[] | undefined)?.[0]?.op) ?? "trim",
                    )}
                    onValueChange={(value) =>
                      updateSelectedNode({
                        params: {
                          kind: "clean",
                          operations: [
                            {
                              op: value,
                              column: String(
                                ((selectedNode.params.operations as { column?: string }[] | undefined)?.[0]?.column) ?? "",
                              ),
                              params:
                                ((selectedNode.params.operations as { params?: Record<string, unknown> }[] | undefined)?.[0]
                                  ?.params) ?? {},
                              risk: "low",
                            },
                          ],
                        },
                      })
                    }
                    disabled={!canMutate}
                  >
                    <SelectTrigger id="clean-op">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {[
                        "cast",
                        "trim",
                        "normalize_null",
                        "dedup",
                        "parse_date",
                        "lowercase",
                        "regex_replace",
                        "drop_if",
                        "fill_default",
                      ].map((op) => (
                        <SelectItem key={op} value={op}>
                          {op}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              ) : null}

              <div className="space-y-1">
                <Label htmlFor="node-params">Parameter (JSON)</Label>
                <Textarea
                  id="node-params"
                  value={paramsDraft}
                  onChange={(event) => applyParamsDraft(event.target.value)}
                  rows={8}
                  className={cn("font-mono text-xs", paramsInvalid && "border-destructive focus-visible:ring-destructive")}
                  aria-invalid={paramsInvalid}
                  aria-describedby="params-hint"
                  disabled={!canMutate}
                />
                <p
                  id="params-hint"
                  className={cn(
                    "text-[11px]",
                    paramsInvalid ? "font-medium text-destructive-text" : "text-muted-foreground",
                  )}
                >
                  {paramsInvalid
                    ? "JSON belum valid. Perbaiki sintaks sebelum menyimpan."
                    : "Perubahan langsung diterapkan pada node di kanvas."}
                </p>
              </div>

              {(selectedNode.params as { operations?: { before_samples?: string[]; after_samples?: string[] }[] })
                .operations?.[0] ? (
                <div className="rounded-lg border border-border bg-muted p-2 text-[11px]">
                  <p className="font-medium text-foreground">{t.pipelines.params.before}</p>
                  <pre className="overflow-auto font-mono">
                    {JSON.stringify(
                      (selectedNode.params as { operations?: { before_samples?: string[] }[] }).operations?.[0]
                        ?.before_samples ?? [],
                      null,
                      1,
                    )}
                  </pre>
                  <p className="mt-1 font-medium text-foreground">{t.pipelines.params.after}</p>
                  <pre className="overflow-auto font-mono">
                    {JSON.stringify(
                      (selectedNode.params as { operations?: { after_samples?: string[] }[] }).operations?.[0]
                        ?.after_samples ?? [],
                      null,
                      1,
                    )}
                  </pre>
                </div>
              ) : null}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              {t.pipelines.noSelection} {t.pipelines.noSelectionBody}
            </p>
          )}
        </aside>
      </div>

      {/* Bottom collapsible console (validation messages) */}
      <div className="border-t border-border bg-background">
        <button
          type="button"
          onClick={() => setConsoleOpen((open) => !open)}
          className="flex w-full items-center justify-between px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted"
          aria-expanded={consoleOpen}
        >
          <span className="flex items-center gap-1.5">
            {issues.length === 0 ? (
              <CheckCircle2 className="h-3.5 w-3.5 text-success" aria-hidden="true" />
            ) : (
              <XCircle className="h-3.5 w-3.5 text-destructive" aria-hidden="true" />
            )}
            {t.pipelines.console} ({issues.length})
          </span>
          {/* A real icon, not a text glyph: the icon set stays consistent. */}
          {consoleOpen ? (
            <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
          )}
        </button>
        {consoleOpen ? (
          <div className="console max-h-40 overflow-auto border-t border-border p-2">
            {issues.length === 0 ? (
              <p className="text-muted-foreground">{t.pipelines.validBanner}</p>
            ) : (
              <>
                <p className="mb-1 text-[11px] text-muted-foreground">
                  Klik salah satu masalah untuk membuka node yang bersangkutan.
                </p>
                {issues.map((issue, index) => {
                  const targetId = nodeIdForIssue(issue);
                  if (!targetId) {
                    // DAG-level problem: no node to jump to.
                    return (
                      <p key={index} className="py-0.5 text-destructive-text">
                        {issue.msg}
                      </p>
                    );
                  }
                  return (
                    <button
                      key={index}
                      type="button"
                      onClick={() => selectIssueNode(targetId)}
                      title={`Buka node ${targetId}`}
                      className="block w-full rounded px-1 py-0.5 text-left text-destructive-text hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40"
                    >
                      {issue.msg}
                    </button>
                  );
                })}
              </>
            )}
          </div>
        ) : null}
      </div>

      {/* Ask AI to build (Pipeline Builder) */}
      <Sheet open={aiOpen} onOpenChange={setAiOpen}>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>{t.pipelines.generateWithAi}</SheetTitle>
            <SheetDescription>
              Jelaskan pipeline yang Anda inginkan dalam bahasa natural. Draf akan divalidasi
              secara deterministik sebelum ditampilkan, dan tetap butuh persetujuan Anda.
            </SheetDescription>
          </SheetHeader>
          <div className="mt-4 space-y-2">
            <Label htmlFor="ai-description">{t.common.description}</Label>
            <Textarea
              id="ai-description"
              value={aiPrompt}
              onChange={(event) => setAiPrompt(event.target.value)}
              rows={6}
              placeholder="Contoh: Bersihkan kolom tanggal, hapus baris duplikat, lalu hitung total pendapatan per bulan."
            />
          </div>
          <SheetFooter className="mt-6">
            <Button variant="outline" onClick={() => setAiOpen(false)}>
              {t.common.cancel}
            </Button>
            <Button
              onClick={() => buildMutation.mutate()}
              disabled={aiPrompt.trim().length < 5 || buildMutation.isPending || !canMutate}
            >
              <Rocket className="h-4 w-4" />
              Susun draf
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </div>
  );
}











