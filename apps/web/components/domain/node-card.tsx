"use client";

import { memo } from "react";
import { Handle, NodeToolbar, Position, type NodeProps } from "@xyflow/react";
import {
  ArrowDownToLine,
  Copy,
  Database,
  Filter,
  Merge,
  Sigma,
  Sparkles,
  Trash2,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { useCanvasActions } from "@/components/domain/canvas-actions";

export interface NodeCardData extends Record<string, unknown> {
  label: string;
  nodeType: string;
  subtitle?: string;
  status?: "idle" | "running" | "succeeded" | "failed" | "invalid";
  selected?: boolean;
  /** Validation messages shown under the card when invalid. */
  errors?: string[];
}

const NODE_ICON: Record<string, LucideIcon> = {
  source: Database,
  validate: Filter,
  clean: Sparkles,
  transform: Merge,
  aggregate: Sigma,
  sink: ArrowDownToLine,
};

const STATUS_DOT: Record<string, string> = {
  idle: "bg-muted-foreground",
  running: "bg-info",
  succeeded: "bg-success",
  failed: "bg-destructive",
  invalid: "bg-destructive",
};

const STATUS_TEXT: Record<string, string> = {
  idle: "Siap",
  running: "Berjalan",
  succeeded: "Berhasil",
  failed: "Gagal",
  invalid: "Tidak valid",
};

/**
 * NodeCard: the only React Flow node type in the canvas.
 *
 * n8n-style behaviour: selecting a node floats a small toolbar above it with
 * the two actions that matter most (duplicate, delete), so the common edits
 * never require a hunt through panels or the keyboard.
 *
 * Status is conveyed by a dot + a text label (never colour alone, PRD 12.5).
 * Invalid nodes get a red border AND an explicit "Tidak valid" tag.
 */
function NodeCardComponent({ id, data, selected }: NodeProps) {
  const nodeData = data as unknown as NodeCardData;
  const Icon = NODE_ICON[nodeData.nodeType] ?? Database;
  const status = nodeData.status ?? "idle";
  const invalid = status === "invalid" || (nodeData.errors?.length ?? 0) > 0;
  const actions = useCanvasActions();
  /**
   * A multi-select would otherwise float one identical toolbar per node. Show
   * it only when this node is the sole selection.
   */
  const showToolbar = Boolean(selected) && (actions?.selectionCount ?? 1) <= 1;

  return (
    <div
      className={cn(
        "w-[220px] rounded-lg border bg-card shadow-card transition-colors",
        invalid ? "border-destructive" : "border-border",
        selected && !invalid && "border-primary ring-2 ring-primary/20",
        Boolean(nodeData.disabled) && "opacity-60",
      )}
    >
      <NodeToolbar isVisible={showToolbar} position={Position.Top} className="flex items-center gap-1">
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            actions?.duplicateNode(id);
          }}
          disabled={!actions || actions.readOnly}
          title="Duplikat node"
          aria-label="Duplikat node"
          className="flex h-7 w-7 items-center justify-center rounded-md border border-border bg-background text-foreground shadow-pop transition-colors hover:bg-muted"
        >
          <Copy className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            actions?.deleteNode(id);
          }}
          disabled={!actions || actions.readOnly}
          title="Hapus node"
          aria-label="Hapus node"
          className="flex h-7 w-7 items-center justify-center rounded-md border border-border bg-background text-destructive-text shadow-pop transition-colors hover:bg-destructive/10"
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </NodeToolbar>

      <Handle
        type="target"
        position={Position.Left}
        className="!h-2.5 !w-2.5 !border-border !bg-background"
        isConnectable={!nodeData.disabled}
      />

      <div className="flex items-start gap-2 p-3">
        <span
          className={cn(
            "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-accent text-accent-foreground",
            invalid && "bg-destructive/10 text-destructive",
          )}
          aria-hidden="true"
        >
          <Icon className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-foreground">{nodeData.label}</p>
          <p className="truncate text-xs text-muted-foreground">
            {nodeData.subtitle ?? nodeData.nodeType}
          </p>
          <p className="mt-1 flex items-center gap-1 text-[11px] text-muted-foreground">
            <span className={cn("h-1.5 w-1.5 rounded-full", STATUS_DOT[status])} aria-hidden="true" />
            {STATUS_TEXT[status] ?? status}
          </p>
        </div>
      </div>

      {invalid ? (
        <div className="border-t border-destructive/30 bg-destructive/5 px-3 py-1.5">
          <p className="text-[11px] font-semibold text-destructive-text">Tidak valid</p>
          {nodeData.errors?.slice(0, 2).map((message, index) => (
            <p key={index} className="truncate text-[11px] text-destructive-text/90">
              {message}
            </p>
          ))}
        </div>
      ) : null}

      <Handle
        type="source"
        position={Position.Right}
        className="!h-2.5 !w-2.5 !border-border !bg-background"
        isConnectable={!nodeData.disabled}
      />
    </div>
  );
}

export const NodeCard = memo(NodeCardComponent);




