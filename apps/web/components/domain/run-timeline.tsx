import { CircleCheck, CircleDashed, CircleSlash, Loader2, RotateCw, XCircle } from "lucide-react";

import { cn } from "@/lib/utils";
import { formatDuration, formatNumber } from "@/lib/format";
import { statusLabel } from "@/lib/i18n/id";
import type { RunNode } from "@/lib/types";

const STATUS_ICON = {
  pending: CircleDashed,
  running: Loader2,
  retrying: RotateCw,
  succeeded: CircleCheck,
  failed: XCircle,
  skipped: CircleSlash,
  cancelled: CircleSlash,
} as const;

const STATUS_TONE = {
  pending: "text-muted-foreground",
  running: "text-info-text",
  retrying: "text-warning-text",
  succeeded: "text-success-text",
  failed: "text-destructive-text",
  skipped: "text-muted-foreground",
  cancelled: "text-muted-foreground",
} as const;

/**
 * RunTimeline: vertical node timeline for the Run Monitor (PRD 12.3/12.4).
 * Shows status icon + text, duration bar, rows in/out/quarantine and retries.
 */
export function RunTimeline({
  nodes,
  onSelect,
  selectedNodeId,
}: {
  nodes: RunNode[];
  onSelect?: (nodeId: string) => void;
  selectedNodeId?: string | null;
}) {
  const maxDuration = Math.max(1, ...nodes.map((node) => node.duration_ms ?? 0));

  if (nodes.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">
        Belum ada node yang dieksekusi.
      </p>
    );
  }

  return (
    <ol className="space-y-0">
      {nodes.map((node, index) => {
        const Icon = STATUS_ICON[node.status] ?? CircleDashed;
        const tone = STATUS_TONE[node.status] ?? "text-muted-foreground";
        const isLast = index === nodes.length - 1;
        const width = Math.max(4, Math.round(((node.duration_ms ?? 0) / maxDuration) * 100));

        return (
          <li key={node.id} className="relative flex gap-3 pb-4">
            {/* connector line */}
            {!isLast ? (
              <span className="absolute left-[11px] top-6 h-full w-px bg-border" aria-hidden="true" />
            ) : null}

            <span className={cn("relative z-10 mt-0.5 flex h-6 w-6 items-center justify-center rounded-full border border-border bg-background", tone)}>
              <Icon className={cn("h-3.5 w-3.5", node.status === "running" && "animate-spin")} aria-hidden="true" />
            </span>

            <button
              type="button"
              onClick={() => onSelect?.(node.node_id)}
              className={cn(
                "flex-1 rounded-lg border border-transparent p-2 text-left transition-colors hover:bg-muted focus-visible:border-primary",
                selectedNodeId === node.node_id && "border-border bg-muted",
              )}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-foreground">{node.node_id}</span>
                  <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
                    {node.node_type}
                  </span>
                </div>
                <span className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span className={tone}>{statusLabel(node.status)}</span>
                  <span className="tabular">{formatDuration(node.duration_ms)}</span>
                </span>
              </div>

              <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className={cn(
                    "h-full rounded-full",
                    node.status === "failed" ? "bg-destructive" : node.status === "succeeded" ? "bg-success" : "bg-info",
                  )}
                  style={{ width: `${width}%` }}
                />
              </div>

              <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-muted-foreground">
                <div className="flex gap-1">
                  <dt>Masuk</dt>
                  <dd className="tabular text-foreground">{formatNumber(node.rows_in)}</dd>
                </div>
                <div className="flex gap-1">
                  <dt>Keluar</dt>
                  <dd className="tabular text-foreground">{formatNumber(node.rows_out)}</dd>
                </div>
                <div className="flex gap-1">
                  <dt>Karantina</dt>
                  <dd className="tabular text-warning-text">{formatNumber(node.rows_quarantined)}</dd>
                </div>
                <div className="flex gap-1">
                  <dt>Percobaan ulang</dt>
                  <dd className="tabular text-foreground">{node.retry_count}</dd>
                </div>
              </dl>

              {node.error_message ? (
                <p className="mt-1.5 rounded-md bg-destructive/5 px-2 py-1 text-[11px] text-destructive-text">
                  {node.error_code ? <span className="font-mono">{node.error_code}: </span> : null}
                  {node.error_message}
                </p>
              ) : null}
            </button>
          </li>
        );
      })}
    </ol>
  );
}

