"use client";

import { useMemo, useState } from "react";

import type { LineageGraph } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Layer tones come from the theme tokens, not from Tailwind default palette.
 * The raw `amber-50 / slate-50 / indigo-50 / red-50` literals predated the
 * blue theme and were the only four places in the app that ignored it.
 */
const LAYER_TONE: Record<string, string> = {
  bronze: "bg-warning/10 border-warning/30 text-warning-text",
  silver: "bg-muted border-border text-foreground",
  gold: "bg-accent border-primary/30 text-accent-foreground",
  quarantine: "bg-destructive/10 border-destructive/30 text-destructive-text",
};

/**
 * Dataset-level lineage graph (source -> bronze -> silver -> gold, FR-027).
 *
 * Deliberately a plain SVG, not React Flow: there are a handful of nodes, the
 * layout is deterministic, and nothing needs to be dragged.
 *
 * Accessibility: each node renders its layer as visible TEXT as well as colour.
 */
export function LineageGraphView({ graph }: { graph: LineageGraph }) {
  const [hovered, setHovered] = useState<string | null>(null);

  const positions = useMemo(() => {
    const byLayer: Record<string, typeof graph.nodes> = {};
    for (const node of graph.nodes) {
      const key = node.layer ?? "source";
      byLayer[key] = byLayer[key] ?? [];
      byLayer[key].push(node);
    }
    const order = ["source", "bronze", "silver", "gold", "quarantine"];
    const map = new Map<string, { x: number; y: number }>();
    order.forEach((layer, columnIndex) => {
      const items = byLayer[layer] ?? [];
      items.forEach((node, rowIndex) => {
        map.set(node.id, { x: 40 + columnIndex * 210, y: 30 + rowIndex * 62 });
      });
    });
    return map;
  }, [graph]);

  const width = Math.max(260, ...Array.from(positions.values()).map((p) => p.x + 180));
  const height = Math.max(120, ...Array.from(positions.values()).map((p) => p.y + 60));

  if (graph.nodes.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        Belum ada dataset untuk ditampilkan pada lineage.
      </p>
    );
  }

  return (
    <div className="overflow-auto rounded-lg border border-border bg-background p-2">
      <svg width={width} height={height} role="img" aria-label="Graf lineage dataset">
        {graph.edges.map((edge) => {
          const from = positions.get(edge.source);
          const to = positions.get(edge.target);
          if (!from || !to) return null;
          const x1 = from.x + 160;
          const y1 = from.y + 20;
          const x2 = to.x;
          const y2 = to.y + 20;
          const midX = (x1 + x2) / 2;
          return (
            <path
              key={`${edge.source}->${edge.target}`}
              d={`M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}`}
              fill="none"
              stroke={hovered === edge.target ? "hsl(var(--primary))" : "hsl(var(--border))"}
              strokeWidth={hovered === edge.target ? 3 : 1.5}
            />
          );
        })}

        {graph.nodes.map((node) => {
          const position = positions.get(node.id);
          if (!position) return null;
          const tone = LAYER_TONE[node.layer ?? ""] ?? "bg-background border-border text-foreground";
          return (
            <g
              key={node.id}
              transform={`translate(${position.x}, ${position.y})`}
              onMouseEnter={() => setHovered(node.id)}
              onMouseLeave={() => setHovered(null)}
              /* Keyboard parity: the highlight is also reachable by Tab. */
              tabIndex={0}
              role="button"
              aria-label={`${node.label}, lapisan ${node.layer ?? node.kind}`}
              onFocus={() => setHovered(node.id)}
              onBlur={() => setHovered(null)}
              className="cursor-default rounded-lg outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            >
              <title>{`${node.label} (${node.layer ?? node.kind})`}</title>
              <rect
                width={160}
                height={40}
                rx={8}
                className={cn("fill-current", "text-background")}
                stroke="hsl(var(--border))"
              />
              <foreignObject width={160} height={40}>
                <div className={cn("flex h-10 items-center justify-between gap-1 rounded-lg border px-2", tone)}>
                  <span className="truncate text-[11px] font-medium">{node.label}</span>
                  <span className="shrink-0 text-[10px] uppercase opacity-70">
                    {node.layer ?? node.kind}
                  </span>
                </div>
              </foreignObject>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
