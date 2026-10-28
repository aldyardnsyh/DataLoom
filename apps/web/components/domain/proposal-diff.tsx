import { AlertTriangle, ShieldAlert, ShieldCheck } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { RiskLevel } from "@/lib/types";

const RISK_STYLE: Record<
  RiskLevel,
  { label: string; variant: "muted" | "warning" | "destructive"; icon: typeof ShieldCheck }
> = {
  low: { label: "Risiko rendah", variant: "muted", icon: ShieldCheck },
  medium: { label: "Risiko sedang", variant: "warning", icon: AlertTriangle },
  destructive: { label: "Destruktif", variant: "destructive", icon: ShieldAlert },
};

export function RiskBadge({ risk }: { risk: RiskLevel }) {
  const style = RISK_STYLE[risk] ?? RISK_STYLE.low;
  const Icon = style.icon;
  return (
    <Badge variant={style.variant} className="gap-1">
      <Icon className="h-3 w-3" aria-hidden="true" />
      {style.label}
    </Badge>
  );
}

export type DiffState = "added" | "removed" | "changed" | "unchanged";

export interface DiffLine {
  state: DiffState;
  before?: string;
  after?: string;
}

const DIFF_STYLE: Record<DiffState, string> = {
  added: "bg-success/10 text-foreground",
  removed: "bg-destructive/10 text-foreground line-through decoration-destructive/60",
  changed: "bg-warning/10 text-foreground",
  unchanged: "text-muted-foreground",
};

const DIFF_LABEL: Record<DiffState, string> = {
  added: "Ditambah",
  removed: "Dihapus",
  changed: "Diubah",
  unchanged: "Tetap",
};

const DIFF_ICON: Record<DiffState, string> = {
  added: "+",
  removed: "−",
  changed: "~",
  unchanged: " ",
};

/**
 * ProposalDiff: before/after comparison for a cleaning proposal.
 * Each row carries a visible symbol AND a screen-reader label so the diff does
 * not rely on colour (PRD 12.5, and WCAG for non-text contrast).
 */
export function ProposalDiff({
  before,
  after,
  lines,
  className,
}: {
  before?: string[] | null;
  after?: string[] | null;
  lines?: DiffLine[];
  className?: string;
}) {
  const rows: DiffLine[] =
    lines ??
    (before ?? []).map((value, index) => ({
      state: "changed" as const,
      before: value,
      after: after?.[index],
    }));

  if (rows.length === 0) {
    return <p className="text-xs text-muted-foreground">Tidak ada contoh baris untuk dibandingkan.</p>;
  }

  return (
    <div className={cn("overflow-hidden rounded-lg border border-border", className)}>
      <table className="w-full border-collapse text-xs">
        <caption className="sr-only">Perbandingan nilai sebelum dan sesudah aturan diterapkan</caption>
        <thead>
          <tr className="bg-muted text-left text-muted-foreground">
            <th scope="col" className="w-8 px-2 py-1.5 font-semibold">
              <span className="sr-only">Perubahan</span>
            </th>
            <th scope="col" className="px-2 py-1.5 font-semibold">
              Sebelum
            </th>
            <th scope="col" className="px-2 py-1.5 font-semibold">
              Sesudah
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} className={cn("border-t border-border", DIFF_STYLE[row.state])}>
              <td className="px-2 py-1.5 font-mono font-bold">
                <span aria-hidden="true">{DIFF_ICON[row.state]}</span>
                <span className="sr-only">{DIFF_LABEL[row.state]}</span>
              </td>
              <td className="px-2 py-1.5 font-mono">{row.before ?? "(kosong)"}</td>
              <td className="px-2 py-1.5 font-mono">{row.after ?? "(kosong)"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
