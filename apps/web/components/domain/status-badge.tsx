import { Loader2 } from "lucide-react";

import { Badge, type BadgeProps } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { statusStyle } from "@/lib/status";

/**
 * StatusBadge: pill with an ICON and TEXT (never colour-only, PRD 12.5).
 * Handles run/node/source/proposal statuses via the central status map.
 */
export function StatusBadge({
  status,
  className,
  showIcon = true,
}: {
  status: string;
  className?: string;
  showIcon?: boolean;
}) {
  const style = statusStyle(status);
  const Icon = style.icon;
  return (
    <Badge
      variant={style.variant as BadgeProps["variant"]}
      className={cn("gap-1 font-medium", className)}
    >
      {showIcon ? (
        <Icon
          className={cn("h-3 w-3", style.spin && "animate-spin")}
          aria-hidden="true"
        />
      ) : null}
      <span>{style.label}</span>
    </Badge>
  );
}

/** Small inline "running" indicator used in tables without a pill. */
export function InlineSpinner({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-info-text">
      <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
      {label}
    </span>
  );
}

