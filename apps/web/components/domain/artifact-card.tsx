import { CircleCheck, FileJson, ShieldCheck, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { formatRelative } from "@/lib/format";

export type ArtifactState = "generated" | "validated" | "failed" | "approved";

const STATE_STYLE: Record<
  ArtifactState,
  { label: string; variant: "info" | "success" | "destructive" | "muted"; icon: typeof CircleCheck }
> = {
  generated: { label: "Dihasilkan", variant: "info", icon: FileJson },
  validated: { label: "Tervalidasi", variant: "success", icon: ShieldCheck },
  failed: { label: "Gagal", variant: "destructive", icon: XCircle },
  approved: { label: "Disetujui", variant: "success", icon: CircleCheck },
};

/**
 * ArtifactCard: shows one agent-produced artifact and makes the difference
 * between generated / validated / failed / approved visually explicit with an
 * icon + text (PRD 12.4).
 */
export function ArtifactCard({
  title,
  state,
  version,
  producingAgent,
  updatedAt,
  description,
  onSelect,
  selected = false,
}: {
  title: string;
  state: ArtifactState;
  version?: number | null;
  producingAgent?: string | null;
  updatedAt?: string | null;
  description?: string | null;
  onSelect?: () => void;
  selected?: boolean;
}) {
  const style = STATE_STYLE[state];
  const StateIcon = style.icon;

  return (
    <Card
      role={onSelect ? "button" : undefined}
      tabIndex={onSelect ? 0 : undefined}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (onSelect && (event.key === "Enter" || event.key === " ")) {
          event.preventDefault();
          onSelect();
        }
      }}
      className={cn(
        "transition-colors",
        onSelect && "cursor-pointer hover:border-primary/40",
        selected && "border-primary ring-2 ring-primary/20",
      )}
    >
      <CardHeader className="flex flex-row items-start justify-between gap-2">
        <CardTitle className="truncate">{title}</CardTitle>
        <Badge variant={style.variant} className="shrink-0 gap-1">
          <StateIcon className="h-3 w-3" aria-hidden="true" />
          {style.label}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-1 text-xs text-muted-foreground">
        {description ? <p className="text-foreground">{description}</p> : null}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {version ? <span>Versi {version}</span> : null}
          {producingAgent ? <span>Oleh {producingAgent}</span> : null}
          {updatedAt ? <span>{formatRelative(updatedAt)}</span> : null}
        </div>
      </CardContent>
    </Card>
  );
}
