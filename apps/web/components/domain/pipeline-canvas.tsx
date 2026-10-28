"use client";

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  BaseEdge,
  Background,
  BackgroundVariant,
  Controls,
  EdgeLabelRenderer,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  getSmoothStepPath,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type EdgeProps,
  type Node,
  type NodeChange,
  type OnSelectionChangeFunc,
} from "@xyflow/react";
import { LayoutGrid, Map as MapIcon, Plus, X } from "lucide-react";

import { NodeCard, type NodeCardData } from "@/components/domain/node-card";
import {
  CanvasActionsProvider,
  useCanvasActions,
  type CanvasActions,
} from "@/components/domain/canvas-actions";
import { Checkbox } from "@/components/ui/checkbox";
import type { NodeType, PipelineDAGDTO, PipelineNodeDTO, ValidationIssue } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Pipeline Canvas, modelled on n8n's workflow editor (adapted to DataLoom):
 *
 * - Nodes are freely draggable; every committed change flows up to the page
 *   as a full DAG (positions included) and can be saved as a new version.
 * - Edges are drawn by dragging from a node's output handle to another node.
 * - Hovering an edge reveals n8n's two controls: "+" inserts a transform node
 *   mid-flow, "x" deletes the connection.
 * - A selected node floats a toolbar (duplicate / delete) via NodeToolbar.
 * - Guardrails: no self-connection, no duplicate edge. Cycles are left to the
 *   DAG validator, which runs before save/run.
 * - Editor conveniences on top of that: undo/redo (50 steps), multi-select,
 *   copy/paste/duplicate, optional 16px grid snapping, optional minimap.
 */

export const nodeTypes = { dataloom: NodeCard };
export const edgeTypes = { dataloom: DataloomEdge };

export interface CanvasNodeData extends NodeCardData {
  params: Record<string, unknown>;
  nodeTypeValue: string;
}

export type CanvasNode = Node<CanvasNodeData, "dataloom">;

const GRID_COLUMN_WIDTH = 280;
const GRID_ROW_HEIGHT = 140;
/** Drag payload type shared with the page's node library. */
const DND_MIME = "application/x-dataloom-node";

/** Undo/redo depth. A DAG snapshot is tiny, so 50 steps stay cheap. */
const MAX_HISTORY_STEPS = 50;
/** Offset applied to pasted nodes so they never hide their originals. */
const PASTE_OFFSET = 40;
/** Snap spacing in flow units, matching the dotted background. */
const SNAP_GRID: [number, number] = [16, 16];
const SNAP_STORAGE_KEY = "dataloom.canvasSnap";
const MINIMAP_STORAGE_KEY = "dataloom.canvasMinimap";

/** Smooth edge with hover controls, rendered correctly under pan/zoom. */
function DataloomEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
}: EdgeProps) {
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });
  const actions = useCanvasActions();
  const readOnly = actions?.readOnly ?? true;

  return (
    <>
      <BaseEdge id={id} path={path} />
      {!readOnly ? (
        <EdgeLabelRenderer>
          <div
            className="pointer-events-none absolute flex items-center gap-1 transition-opacity"
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          >
            <button
              type="button"
              title="Sisipkan node Transformasi di sini"
              aria-label="Sisipkan node di tengah alur ini"
              className="pointer-events-auto flex h-6 w-6 items-center justify-center rounded-full border border-border bg-background text-foreground opacity-40 shadow-pop transition-opacity hover:bg-muted hover:opacity-100 focus-visible:opacity-100"
              onClick={() => actions?.insertNodeOnEdge(id)}
            >
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
            <button
              type="button"
              title="Hapus koneksi ini"
              aria-label="Hapus koneksi"
              className="pointer-events-auto flex h-6 w-6 items-center justify-center rounded-full border border-border bg-background text-destructive-text opacity-40 shadow-pop transition-opacity hover:bg-destructive/10 hover:opacity-100 focus-visible:opacity-100"
              onClick={() => actions?.deleteEdge(id)}
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

/** Deterministic LR layout so a freshly generated DAG is readable. */
export function autoLayout(nodes: PipelineNodeDTO[]): Record<string, { x: number; y: number }> {
  const depth = new Map<string, number>();
  nodes.forEach((node, index) => {
    depth.set(node.id, index);
  });

  return Object.fromEntries(
    nodes.map((node, index) => [
      node.id,
      {
        x: (depth.get(node.id) ?? index) * GRID_COLUMN_WIDTH,
        y: (index % 2) * GRID_ROW_HEIGHT,
      },
    ]),
  );
}

export function dagToFlow(
  dag: PipelineDAGDTO,
  issues: ValidationIssue[] = [],
): { nodes: CanvasNode[]; edges: Edge[] } {
  const issuesByNode = new Map<string, string[]>();
  for (const issue of issues) {
    const index = issue.loc?.[0] === "nodes" ? Number(issue.loc[1]) : NaN;
    const node = Number.isFinite(index) ? dag.nodes[index] : undefined;
    if (node) {
      const list = issuesByNode.get(node.id) ?? [];
      list.push(issue.msg);
      issuesByNode.set(node.id, list);
    }
  }

  const layout = autoLayout(dag.nodes);

  const nodes: CanvasNode[] = dag.nodes.map((node, index) => {
    const errors = issuesByNode.get(node.id) ?? [];
    const label = node.label ?? defaultLabel(node.type);
    return {
      id: node.id,
      type: "dataloom",
      position: node.position ?? layout[node.id] ?? { x: index * GRID_COLUMN_WIDTH, y: 0 },
      data: {
        label,
        nodeType: node.type,
        nodeTypeValue: node.type,
        params: (node.params ?? {}) as Record<string, unknown>,
        subtitle: subtitleFor(node),
        status: errors.length > 0 ? "invalid" : "idle",
        errors,
      },
    };
  });

  const edges: Edge[] = dag.edges.map((edge, index) => ({
    id: edge.id ?? `e-${edge.from}-${edge.to}-${index}`,
    source: edge.from,
    target: edge.to,
    type: "dataloom",
    data: {},
  }));

  return { nodes, edges };
}

export function flowToDag(
  nodes: CanvasNode[],
  edges: Edge[],
  metadata: Record<string, unknown> = {},
): PipelineDAGDTO {
  return {
    schema_version: 1,
    nodes: nodes.map<PipelineNodeDTO>((node) => ({
      id: node.id,
      type: node.data.nodeTypeValue as NodeType,
      label: node.data.label,
      params: (node.data.params ?? { kind: node.data.nodeType }) as Record<string, unknown> & {
        kind: string;
      },
      position: { x: node.position.x, y: node.position.y },
    })),
    edges: edges.map((edge) => ({ id: edge.id, from: edge.source, to: edge.target })),
    metadata,
  };
}

function defaultLabel(type: string): string {
  const labels: Record<string, string> = {
    source: "Sumber",
    validate: "Validasi",
    clean: "Bersihkan",
    transform: "Transformasi",
    aggregate: "Agregasi",
    sink: "Gudang Data",
  };
  return labels[type] ?? type;
}

/**
 * One-line summary under the node title. Shared by the initial mapping and by
 * `patchNode`, so editing parameters in the properties panel updates the card
 * with exactly the same wording a freshly loaded DAG would show.
 */
function subtitleFromParams(type: string, params: Record<string, unknown>): string {
  if (type === "clean" && Array.isArray(params.operations)) {
    return `${params.operations.length} operasi`;
  }
  if (type === "validate" && Array.isArray(params.rules)) {
    return `${params.rules.length} aturan`;
  }
  if (type === "aggregate" && Array.isArray(params.metrics)) {
    return `${params.metrics.length} metrik`;
  }
  if (type === "sink" && typeof params.dataset_name === "string") {
    return params.dataset_name;
  }
  return type;
}

function subtitleFor(node: PipelineNodeDTO): string {
  return subtitleFromParams(node.type, (node.params ?? {}) as Record<string, unknown>);
}

/** Collision-free id, so pasting twice never produces a duplicate node. */
function makeNodeId(type: string, taken?: Set<string>): string {
  const next = () =>
    `n_${type}_${Date.now().toString(36)}${Math.floor(Math.random() * 1e6).toString(36)}`;
  let id = next();
  while (taken?.has(id)) id = next();
  return id;
}

function makeNode(type: NodeType, position: { x: number; y: number }): CanvasNode {
  return {
    id: makeNodeId(type),
    type: "dataloom",
    position,
    data: {
      label: defaultLabel(type),
      nodeType: type,
      nodeTypeValue: type,
      params: { kind: type },
      subtitle: type,
      status: "idle",
    },
  };
}

/** Read a boolean preference. Storage can be blocked (private mode, policy). */
function readStoredFlag(key: string, fallback: boolean): boolean {
  if (typeof window === "undefined") return fallback;
  try {
    const value = window.localStorage.getItem(key);
    return value === null ? fallback : value === "true";
  } catch {
    return fallback;
  }
}

function writeStoredFlag(key: string, value: boolean): void {
  try {
    window.localStorage.setItem(key, String(value));
  } catch {
    // A preference is a nicety; never break the editor over it.
  }
}

/** True when focus sits in a text field, where the browser owns the shortcut. */
function isTextEntryTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName.toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return true;
  return target.isContentEditable;
}

/**
 * Undo stack of DAG snapshots. `index` points at the state currently on screen;
 * undo/redo only move that pointer, so replaying never re-commits a step.
 */
interface HistoryState {
  entries: PipelineDAGDTO[];
  index: number;
}

/** What the page needs in order to enable/disable its undo and redo buttons. */
export interface CanvasHistoryState {
  canUndo: boolean;
  canRedo: boolean;
}

interface ClipboardPayload {
  nodes: CanvasNode[];
  edges: Edge[];
}

const CanvasInner = forwardRef<PipelineCanvasHandle, CanvasInnerProps>(function CanvasInner(
  { dag, issues = [], readOnly = false, onChange, onHistoryChange, onSelectNode },
  apiRef,
) {
  /**
   * UNCONTROLLED canvas (the n8n / draw.io model).
   *
   * React Flow owns node and edge state internally; the page only reads the
   * graph when it needs it (save / validate) through `onChange`. Driving node
   * positions from React props on every tick caused the parent to write a
   * stale list back into the store, which is what made nodes snap back and
   * feel "stuck". One-way sync removes that whole class of bug.
   */
  const initial = useMemo(() => dagToFlow(dag, issues), []); // eslint-disable-line react-hooks/exhaustive-deps
  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasNode>(initial.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initial.edges);
  const [dropHint, setDropHint] = useState(false);
  /** Drives NodeToolbar visibility: several selected nodes show no toolbar. */
  const [selectionCount, setSelectionCount] = useState(0);
  const [snapEnabled, setSnapEnabled] = useState(false);
  const [minimapVisible, setMinimapVisible] = useState(true);
  const { screenToFlowPosition, getNodes, getEdges } = useReactFlow();

  // Latest nodes/edges for callbacks outside React's render cycle.
  const nodesRef = useRef(nodes);
  nodesRef.current = nodes;
  const edgesRef = useRef(edges);
  edgesRef.current = edges;

  // Created lazily so the initial snapshot is taken exactly once.
  const historyRef = useRef<HistoryState | null>(null);
  if (historyRef.current === null) {
    historyRef.current = {
      entries: [flowToDag(initial.nodes, initial.edges, dag.metadata ?? {})],
      index: 0,
    };
  }
  const clipboardRef = useRef<ClipboardPayload | null>(null);
  /**
   * Set when React Flow itself edits the graph (Delete key, multi-delete). The
   * commit then runs from an effect, where `nodes`/`edges` are guaranteed to be
   * the state React just produced, instead of a ref that may still lag behind.
   */
  const pendingCommitRef = useRef(false);
  /** Last published availability, so the page is not notified needlessly. */
  const publishedHistoryRef = useRef<CanvasHistoryState | null>(null);

  const emit = useCallback(
    (nextNodes: CanvasNode[], nextEdges: Edge[]) => {
      if (readOnly || !onChange) return;
      onChange(flowToDag(nextNodes, nextEdges, dag.metadata ?? {}));
    },
    [readOnly, onChange, dag.metadata],
  );

  const syncHistory = useCallback(() => {
    const history = historyRef.current;
    if (!history) return;
    const next: CanvasHistoryState = {
      canUndo: history.index > 0,
      canRedo: history.index < history.entries.length - 1,
    };
    const published = publishedHistoryRef.current;
    // Guard here as well: the page may store the object in state, and an
    // unconditional notification would loop through its re-render.
    if (published && published.canUndo === next.canUndo && published.canRedo === next.canRedo) {
      return;
    }
    publishedHistoryRef.current = next;
    onHistoryChange?.(next);
  }, [onHistoryChange]);

  // Publish the current availability on mount so the page buttons start right.
  useEffect(() => {
    syncHistory();
  }, [syncHistory]);

  /** Push a new undo step, unless it is identical to the one already on top. */
  const pushHistory = useCallback(
    (snapshot: PipelineDAGDTO) => {
      const history = historyRef.current;
      if (!history) return;
      const current = history.entries[history.index];
      if (current && JSON.stringify(current) === JSON.stringify(snapshot)) return;
      const entries = history.entries.slice(0, history.index + 1);
      entries.push(snapshot);
      while (entries.length > MAX_HISTORY_STEPS) entries.shift();
      historyRef.current = { entries, index: entries.length - 1 };
      syncHistory();
    },
    [syncHistory],
  );

  /** Record an undo step AND report the graph to the page, in that order. */
  const commit = useCallback(
    (nextNodes: CanvasNode[], nextEdges: Edge[]) => {
      pushHistory(flowToDag(nextNodes, nextEdges, dag.metadata ?? {}));
      emit(nextNodes, nextEdges);
    },
    [pushHistory, emit, dag.metadata],
  );

  /** Commit a graph React Flow changed for us (Delete / Backspace). */
  useEffect(() => {
    if (!pendingCommitRef.current) return;
    pendingCommitRef.current = false;
    commit(nodes, edges);
  }, [nodes, edges, commit]);

  /**
   * Paint the CURRENT validation result onto the live nodes.
   *
   * `dagToFlow` can only do this once, at mount, because the canvas is
   * uncontrolled. Without this effect the console listed the problems but every
   * node stayed green until the page was reloaded, which is exactly the
   * "validator that does not really say anything" the editor must not have.
   *
   * Issue -> node resolution: `node_ref` (our local validator fills it with the
   * real node id) first, then the Pydantic-style `["nodes", index]` location as
   * a fallback for server responses.
   */
  const applyIssues = useCallback(
    (incoming: ValidationIssue[]) => {
      const nodeIds = nodesRef.current.map((node) => node.id);
      const errorsById = new Map<string, string[]>();

      for (const issue of incoming) {
        let id: string | null = null;
        const ref = issue.node_ref;
        if (typeof ref === "string" && nodeIds.includes(ref)) {
          id = ref;
        } else if (Array.isArray(issue.loc) && issue.loc[0] === "nodes") {
          const index = Number(issue.loc[1]);
          if (Number.isInteger(index) && index >= 0 && index < nodeIds.length) {
            id = nodeIds[index] ?? null;
          }
        }
        if (!id) continue;
        const list = errorsById.get(id) ?? [];
        list.push(issue.msg);
        errorsById.set(id, list);
      }

      setNodes((current) => {
        let changed = false;
        const next = current.map((node) => {
          const errors = errorsById.get(node.id) ?? [];
          // A node that is no longer blamed goes back to "idle"; a node that
          // is running/succeeded keeps its execution status.
          const status: NonNullable<CanvasNodeData["status"]> =
            errors.length > 0
              ? "invalid"
              : node.data.status === "invalid"
                ? "idle"
                : (node.data.status ?? "idle");
          const sameErrors =
            (node.data.errors?.length ?? 0) === errors.length &&
            (node.data.errors ?? []).every((message, index) => message === errors[index]);
          if (status === node.data.status && sameErrors) return node;
          changed = true;
          return { ...node, data: { ...node.data, status, errors } };
        });
        return changed ? next : current;
      });
    },
    [setNodes],
  );

  useEffect(() => {
    applyIssues(issues);
  }, [issues, applyIssues]);

  /** Swap the whole graph for a history entry (undo / redo). */
  const restoreSnapshot = useCallback(
    (snapshot: PipelineDAGDTO) => {
      const restored = dagToFlow(snapshot);
      const existing = new Map(nodesRef.current.map((node) => [node.id, node]));
      // The DAG does not carry validation markers, so nodes that survive the
      // jump keep the ones the last validation produced.
      const merged = restored.nodes.map<CanvasNode>((node) => {
        const current = existing.get(node.id);
        if (!current) return node;
        return {
          ...node,
          data: { ...node.data, status: current.data.status, errors: current.data.errors },
        };
      });
      setNodes(merged);
      setEdges(restored.edges);
      emit(merged, restored.edges);
    },
    [setNodes, setEdges, emit],
  );

  const undo = useCallback(() => {
    const history = historyRef.current;
    if (readOnly || !history || history.index <= 0) return;
    const nextIndex = history.index - 1;
    const entry = history.entries[nextIndex];
    if (!entry) return;
    history.index = nextIndex;
    restoreSnapshot(entry);
    syncHistory();
  }, [readOnly, restoreSnapshot, syncHistory]);

  const redo = useCallback(() => {
    const history = historyRef.current;
    if (readOnly || !history || history.index >= history.entries.length - 1) return;
    const nextIndex = history.index + 1;
    const entry = history.entries[nextIndex];
    if (!entry) return;
    history.index = nextIndex;
    restoreSnapshot(entry);
    syncHistory();
  }, [readOnly, restoreSnapshot, syncHistory]);

  /**
   * Only STRUCTURAL edits are pushed up immediately (add/remove/connect), so
   * the page's "unsaved changes" signal and dirty state stay honest. Plain
   * position drags are read on demand by `snapshot()` and committed on drop.
   */
  const handleNodesChange = useCallback(
    (changes: NodeChange<CanvasNode>[]) => {
      if (readOnly) return;
      onNodesChange(changes);
      if (changes.some((change) => change.type === "remove" || change.type === "add")) {
        pendingCommitRef.current = true;
      }
    },
    [readOnly, onNodesChange],
  );

  const handleEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      if (readOnly) return;
      onEdgesChange(changes);
      if (changes.some((change) => change.type === "remove")) {
        pendingCommitRef.current = true;
      }
    },
    [readOnly, onEdgesChange],
  );

  const handleSelectionChange = useCallback<OnSelectionChangeFunc<CanvasNode, Edge>>(
    (params) => {
      setSelectionCount(params.nodes.length);
      // Keep the page's properties panel in sync: a single selection opens that
      // node, anything else (empty, multi) closes the panel.
      onSelectNode?.(params.nodes.length === 1 ? params.nodes[0]!.id : null);
    },
    [onSelectNode],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      if (readOnly) return;
      if (connection.source === connection.target) return;
      if (edgesRef.current.some((e) => e.source === connection.source && e.target === connection.target)) {
        return;
      }
      const nextEdges = addEdge(
        { ...connection, id: `e-${connection.source}-${connection.target}`, type: "dataloom", data: {} },
        edgesRef.current,
      );
      setEdges(nextEdges);
      commit(nodesRef.current, nextEdges);
    },
    [readOnly, setEdges, commit],
  );

  const duplicateNode = useCallback(
    (nodeId: string) => {
      if (readOnly) return;
      const source = nodesRef.current.find((node) => node.id === nodeId);
      if (!source) return;
      const copy = makeNode(source.data.nodeTypeValue as NodeType, {
        x: source.position.x + GRID_COLUMN_WIDTH / 2,
        y: source.position.y + GRID_ROW_HEIGHT,
      });
      copy.data = {
        ...source.data,
        label: `${source.data.label} (salinan)`,
        params: structuredClone(source.data.params ?? {}),
        subtitle: source.data.subtitle,
      };
      const nextNodes = [...nodesRef.current, copy];
      setNodes(nextNodes);
      commit(nextNodes, edgesRef.current);
    },
    [readOnly, setNodes, commit],
  );

  const deleteNode = useCallback(
    (nodeId: string) => {
      if (readOnly) return;
      const nextNodes = nodesRef.current.filter((node) => node.id !== nodeId);
      const nextEdges = edgesRef.current.filter(
        (edge) => edge.source !== nodeId && edge.target !== nodeId,
      );
      setNodes(nextNodes);
      setEdges(nextEdges);
      commit(nextNodes, nextEdges);
    },
    [readOnly, setNodes, setEdges, commit],
  );

  /**
   * Apply a properties-panel edit (label / params / type) to the LIVE node.
   *
   * The page's DAG state is only a projection of the canvas, so writing to it
   * directly changed the form but left the card on screen untouched — and the
   * next `snapshot()` would then save the OLD values. Routing the edit through
   * the canvas keeps one source of truth and puts the change on the undo stack.
   */
  const patchNode = useCallback(
    (nodeId: string, patch: Partial<PipelineNodeDTO>) => {
      if (readOnly) return;
      const target = nodesRef.current.find((node) => node.id === nodeId);
      if (!target) return;

      const type = (patch.type ?? target.data.nodeTypeValue) as NodeType;
      const params = (patch.params ?? target.data.params ?? {}) as Record<string, unknown>;
      let label = target.data.label;
      if (patch.label !== undefined) {
        const candidate = patch.label ?? "";
        label = candidate.trim() === "" ? defaultLabel(type) : candidate;
      }

      const nextNodes = nodesRef.current.map((node) =>
        node.id === nodeId
          ? {
              ...node,
              data: {
                ...node.data,
                label,
                nodeType: type,
                nodeTypeValue: type,
                params,
                subtitle: subtitleFromParams(type, params),
              },
            }
          : node,
      );
      setNodes(nextNodes);
      commit(nextNodes, edgesRef.current);
    },
    [readOnly, setNodes, commit],
  );

  /** Select exactly one node (used by the validation console jump links). */
  const selectNode = useCallback(
    (nodeId: string | null) => {
      setNodes((current) =>
        current.map((node) => (node.selected === (node.id === nodeId) ? node : { ...node, selected: node.id === nodeId })),
      );
      onSelectNode?.(nodeId);
    },
    [setNodes, onSelectNode],
  );

  /** n8n's edge "+": insert a transform node between two connected nodes. */
  const insertNodeOnEdge = useCallback(
    (edgeId: string) => {
      if (readOnly) return;
      const edge = edgesRef.current.find((candidate) => candidate.id === edgeId);
      if (!edge) return;
      const source = nodesRef.current.find((node) => node.id === edge.source);
      const target = nodesRef.current.find((node) => node.id === edge.target);
      if (!source || !target) return;
      const inserted = makeNode("transform", {
        x: (source.position.x + target.position.x) / 2,
        y: (source.position.y + target.position.y) / 2 + GRID_ROW_HEIGHT / 2,
      });
      const nextNodes = [...nodesRef.current, inserted];
      const nextEdges: Edge[] = [
        ...edgesRef.current.filter((candidate) => candidate.id !== edgeId),
        { id: `e-${edge.source}-${inserted.id}`, source: edge.source, target: inserted.id, type: "dataloom", data: {} },
        { id: `e-${inserted.id}-${edge.target}`, source: inserted.id, target: edge.target, type: "dataloom", data: {} },
      ];
      setNodes(nextNodes);
      setEdges(nextEdges);
      commit(nextNodes, nextEdges);
    },
    [readOnly, setNodes, setEdges, commit],
  );

  const deleteEdgeById = useCallback(
    (edgeId: string) => {
      if (readOnly) return;
      const nextEdges = edgesRef.current.filter((candidate) => candidate.id !== edgeId);
      setEdges(nextEdges);
      commit(nodesRef.current, nextEdges);
    },
    [readOnly, setEdges, commit],
  );

  /** Ctrl+C: stash the selection plus the edges that live entirely inside it. */
  const copySelection = useCallback((): boolean => {
    if (readOnly) return false;
    const selected = nodesRef.current.filter((node) => node.selected);
    if (selected.length === 0) return false;
    const selectedIds = new Set(selected.map((node) => node.id));
    clipboardRef.current = {
      nodes: selected.map((node) => ({
        ...node,
        data: { ...node.data, params: structuredClone(node.data.params ?? {}) },
      })),
      edges: edgesRef.current
        .filter((edge) => selectedIds.has(edge.source) && selectedIds.has(edge.target))
        .map((edge) => ({ ...edge })),
    };
    return true;
  }, [readOnly]);

  /**
   * Shared paste routine for Ctrl+V and Ctrl+D. Copies get fresh ids and a
   * fixed offset; an edge is recreated only when BOTH of its endpoints were
   * part of the payload, so a copy never points back at the originals.
   */
  const insertCopies = useCallback(
    (payload: ClipboardPayload) => {
      if (payload.nodes.length === 0) return;
      const taken = new Set(nodesRef.current.map((node) => node.id));
      const idMap = new Map<string, string>();
      const pastedNodes = payload.nodes.map<CanvasNode>((node) => {
        const id = makeNodeId(node.data.nodeTypeValue, taken);
        taken.add(id);
        idMap.set(node.id, id);
        return {
          ...node,
          id,
          selected: true,
          position: { x: node.position.x + PASTE_OFFSET, y: node.position.y + PASTE_OFFSET },
          data: { ...node.data, params: structuredClone(node.data.params ?? {}) },
        };
      });

      const pastedEdges = payload.edges.flatMap<Edge>((edge) => {
        const source = idMap.get(edge.source);
        const target = idMap.get(edge.target);
        if (!source || !target) return [];
        return [{ id: `e-${source}-${target}`, source, target, type: "dataloom", data: {} }];
      });

      // Only the copies stay selected, so a second paste keeps stepping away
      // from the originals instead of stacking on top of the first copy.
      const nextNodes = [
        ...nodesRef.current.map((node) => (node.selected ? { ...node, selected: false } : node)),
        ...pastedNodes,
      ];
      const nextEdges = [...edgesRef.current, ...pastedEdges];
      setNodes(nextNodes);
      setEdges(nextEdges);
      commit(nextNodes, nextEdges);
    },
    [setNodes, setEdges, commit],
  );

  /** Ctrl+V: paste the internal clipboard. */
  const pasteClipboard = useCallback((): boolean => {
    if (readOnly) return false;
    const clip = clipboardRef.current;
    if (!clip || clip.nodes.length === 0) return false;
    insertCopies(clip);
    return true;
  }, [readOnly, insertCopies]);

  /**
   * Ctrl+D: duplicate the selection. Built from the live selection rather than
   * the clipboard, so a duplicate does not overwrite what the user copied.
   */
  const duplicateSelection = useCallback(() => {
    if (readOnly) return;
    const selected = nodesRef.current.filter((node) => node.selected);
    if (selected.length === 0) return;
    const selectedIds = new Set(selected.map((node) => node.id));
    insertCopies({
      nodes: selected.map((node) => ({
        ...node,
        data: { ...node.data, params: structuredClone(node.data.params ?? {}) },
      })),
      edges: edgesRef.current
        .filter((edge) => selectedIds.has(edge.source) && selectedIds.has(edge.target))
        .map((edge) => ({ ...edge })),
    });
  }, [readOnly, insertCopies]);

  /**
   * Library button. Routed through the canvas (instead of the page's DAG
   * state) so the new node really appears on the uncontrolled canvas, lands on
   * the undo stack, and keeps the page's copy of the graph in sync.
   */
  const addNodeFromLibrary = useCallback(
    (type: NodeType): string | undefined => {
      if (readOnly) return undefined;
      const current = nodesRef.current;
      const position =
        current.length === 0
          ? { x: 0, y: 0 }
          : {
              x: Math.max(...current.map((node) => node.position.x)) + GRID_COLUMN_WIDTH + 40,
              y: Math.min(...current.map((node) => node.position.y)),
            };
      const created: CanvasNode = { ...makeNode(type, position), selected: true };
      const nextNodes = [
        ...current.map((node) => (node.selected ? { ...node, selected: false } : node)),
        created,
      ];
      // Parity with the previous library behaviour: append to the end of the chain.
      const last = current.at(-1);
      const nextEdges: Edge[] =
        last && !edgesRef.current.some((edge) => edge.source === last.id && edge.target === created.id)
          ? [
              ...edgesRef.current,
              { id: `e-${last.id}-${created.id}`, source: last.id, target: created.id, type: "dataloom", data: {} },
            ]
          : edgesRef.current;
      setNodes(nextNodes);
      setEdges(nextEdges);
      commit(nextNodes, nextEdges);
      return created.id;
    },
    [readOnly, setNodes, setEdges, commit],
  );

  /**
   * Commit the end of a drag. The dragged positions come from React Flow's
   * event payload, because `nodesRef` can still be one render behind the final
   * mouse position at the moment the drop fires.
   */
  const commitDragPositions = useCallback(
    (dragged: CanvasNode[]) => {
      if (dragged.length === 0) {
        commit(nodesRef.current, edgesRef.current);
        return;
      }
      const positions = new Map(dragged.map((node) => [node.id, node.position]));
      const nextNodes = nodesRef.current.map((node) => {
        const position = positions.get(node.id);
        return position ? { ...node, position: { x: position.x, y: position.y } } : node;
      });
      commit(nextNodes, edgesRef.current);
    },
    [commit],
  );

  const actions = useMemo<CanvasActions>(
    () => ({
      readOnly,
      selectionCount,
      duplicateNode,
      deleteNode,
      insertNodeOnEdge,
      deleteEdge: deleteEdgeById,
    }),
    [readOnly, selectionCount, duplicateNode, deleteNode, insertNodeOnEdge, deleteEdgeById],
  );

  /** Drop target: create a node where the dragged library item lands. */
  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      setDropHint(false);
      if (readOnly) return;
      const raw = event.dataTransfer.getData(DND_MIME);
      if (!raw) return;
      const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
      const nextNodes = [...nodesRef.current, makeNode(raw as NodeType, position)];
      setNodes(nextNodes);
      commit(nextNodes, edgesRef.current);
    },
    [readOnly, setNodes, commit, screenToFlowPosition],
  );

  /** Editor shortcuts. Text fields keep the browser's own bindings. */
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (readOnly) return;
      if (!(event.ctrlKey || event.metaKey)) return;
      if (isTextEntryTarget(event.target)) return;
      const key = event.key.toLowerCase();
      if (key === "z") {
        event.preventDefault();
        if (event.shiftKey) redo();
        else undo();
        return;
      }
      if (key === "y") {
        event.preventDefault();
        redo();
        return;
      }
      if (key === "c") {
        if (copySelection()) event.preventDefault();
        return;
      }
      if (key === "v") {
        if (pasteClipboard()) event.preventDefault();
        return;
      }
      if (key === "d") {
        event.preventDefault();
        duplicateSelection();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [readOnly, undo, redo, copySelection, pasteClipboard, duplicateSelection]);

  /** Public escape hatch: the page pulls the graph when saving/validating. */
  useImperativeHandle(
    apiRef,
    () => ({
      // Read straight from the React Flow store: it is the source of truth,
      // and it is always current even before React commits a re-render.
      snapshot: () => flowToDag(getNodes() as CanvasNode[], getEdges(), dag.metadata ?? {}),
      undo,
      redo,
      canUndo: () => (historyRef.current?.index ?? 0) > 0,
      canRedo: () => {
        const history = historyRef.current;
        if (!history) return false;
        return history.index < history.entries.length - 1;
      },
      addNode: addNodeFromLibrary,
      patchNode,
      deleteNode,
      selectNode,
    }),
    [getNodes, getEdges, dag.metadata, undo, redo, addNodeFromLibrary, patchNode, deleteNode, selectNode],
  );

  // Preferences are read after mount so the server-rendered markup stays stable.
  useEffect(() => {
    setSnapEnabled(readStoredFlag(SNAP_STORAGE_KEY, false));
    setMinimapVisible(readStoredFlag(MINIMAP_STORAGE_KEY, true));
  }, []);

  const toggleSnap = (next: boolean) => {
    setSnapEnabled(next);
    writeStoredFlag(SNAP_STORAGE_KEY, next);
  };

  const toggleMinimap = () => {
    const next = !minimapVisible;
    setMinimapVisible(next);
    writeStoredFlag(MINIMAP_STORAGE_KEY, next);
  };

  return (
    <CanvasActionsProvider value={actions}>
      <div
        className="h-full w-full"
        aria-label="Kanvas pipeline"
        role="application"
        data-dag-snapshot=""
        onDragOver={(event) => {
          if (readOnly) return;
          if (event.dataTransfer.types.includes(DND_MIME)) {
            event.preventDefault();
            event.dataTransfer.dropEffect = "copy";
            setDropHint(true);
          }
        }}
        onDragLeave={() => setDropHint(false)}
        onDrop={onDrop}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={handleNodesChange}
          onEdgesChange={handleEdgesChange}
          onSelectionChange={handleSelectionChange}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onConnect={onConnect}
          nodesDraggable={!readOnly}
          nodesConnectable={!readOnly}
          edgesReconnectable={!readOnly}
          elementsSelectable
          snapToGrid={snapEnabled}
          snapGrid={SNAP_GRID}
          onNodeDragStop={(_event, _node, dragged) => commitDragPositions(dragged)}
          onSelectionDragStop={(_event, dragged) => commitDragPositions(dragged)}
          deleteKeyCode={readOnly ? null : ["Backspace", "Delete"]}
          fitView
          minZoom={0.2}
          maxZoom={2}
          proOptions={{ hideAttribution: true }}
          className={cn("bg-background", dropHint && "ring-2 ring-inset ring-primary/40")}
        >
          <Background variant={BackgroundVariant.Dots} gap={16} size={1} color="hsl(var(--border))" />
          <Controls showInteractive={!readOnly} />
          {minimapVisible ? (
            <MiniMap pannable zoomable={!readOnly} nodeColor="hsl(var(--accent))" maskColor="hsl(var(--background) / 0.75)" />
          ) : null}
          <Panel
            position="top-right"
            className="nodrag flex items-center gap-3 rounded-lg border border-border bg-background/95 px-2 py-1.5 shadow-card"
          >
            <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground">
              <Checkbox
                checked={snapEnabled}
                disabled={readOnly}
                onCheckedChange={(checked) => toggleSnap(checked === true)}
                aria-label="Ratakan ke grid"
              />
              <LayoutGrid className="h-3 w-3" aria-hidden="true" />
              Ratakan ke grid
            </label>
            <button
              type="button"
              onClick={toggleMinimap}
              aria-pressed={minimapVisible}
              title={minimapVisible ? "Sembunyikan peta mini" : "Tampilkan peta mini"}
              aria-label={minimapVisible ? "Sembunyikan peta mini" : "Tampilkan peta mini"}
              className={cn(
                "flex h-6 w-6 items-center justify-center rounded-md border border-border bg-background text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                minimapVisible && "border-primary/40 bg-primary/10 text-primary",
              )}
            >
              <MapIcon className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </Panel>
        </ReactFlow>
      </div>
    </CanvasActionsProvider>
  );
});

export interface PipelineCanvasHandle {
  /** Current canvas graph, including positions the user dragged. */
  snapshot: () => PipelineDAGDTO;
  /** Step one change back. No-op when the stack is empty. */
  undo: () => void;
  /** Replay the change that was undone. No-op when nothing was undone. */
  redo: () => void;
  canUndo: () => boolean;
  canRedo: () => boolean;
  /** Append a node from the library so the action lands on the undo stack. */
  addNode: (type: NodeType) => string | undefined;
  /** Apply a properties-panel edit to the live node (label / params / type). */
  patchNode: (nodeId: string, patch: Partial<PipelineNodeDTO>) => void;
  /** Remove a node and every edge attached to it. */
  deleteNode: (nodeId: string) => void;
  /** Select exactly one node; `null` clears the selection. */
  selectNode: (nodeId: string | null) => void;
}

export interface CanvasInnerProps {
  dag: PipelineDAGDTO;
  issues?: ValidationIssue[];
  readOnly?: boolean;
  onChange?: (dag: PipelineDAGDTO) => void;
  /** Reactive undo/redo availability, so the page can disable its buttons. */
  onHistoryChange?: (state: CanvasHistoryState) => void;
  /**
   * Accepted for compatibility with the page's props. Selection is owned by
   * React Flow (multi-select included), so the canvas does not drive it from
   * here; writing it back would fight the drag loop the uncontrolled model
   * exists to avoid.
   */
  onSelectNode?: (nodeId: string | null) => void;
  selectedNodeId?: string | null;
}

/** Provider wrapper so the canvas owns zoom helpers (drop position mapping). */
export const PipelineCanvas = forwardRef<PipelineCanvasHandle, CanvasInnerProps>(
  function PipelineCanvas(props, ref) {
    return (
      <ReactFlowProvider>
        <CanvasInner {...props} ref={ref} />
      </ReactFlowProvider>
    );
  },
);
