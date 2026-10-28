import { AlertTriangle } from "lucide-react";

import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import { formatNumber, formatPercent, formatUsd } from "@/lib/format";
import { t } from "@/lib/i18n/id";

/**
 * BudgetMeter: LLM budget consumption (FR-035).
 *
 * Warning appears at >=80% with an ICON + TEXT (never colour alone). When the
 * budget is exhausted the meter states that runs will stop, which mirrors the
 * backend guard.
 */
export function BudgetMeter({
  used,
  budget,
  tokensUsed,
  tokenLimit,
  compact = false,
  className,
}: {
  used: number;
  budget: number;
  tokensUsed?: number;
  tokenLimit?: number;
  compact?: boolean;
  className?: string;
}) {
  /*
   * A zero budget is "not configured", NOT "free". Treating it as 0% used hid
   * real spend behind a green bar; any spend with no budget is shown as
   * exhausted so the operator sees that nothing is capping the cost.
   */
  const hasBudget = budget > 0;
  const ratio = hasBudget ? Math.min(1, used / budget) : used > 0 ? 1 : 0;
  const percent = formatPercent(ratio, 0);
  const isWarning = hasBudget && ratio >= 0.8 && ratio < 1;
  const isExhausted = hasBudget ? ratio >= 1 : used > 0;

  const indicatorClass = isExhausted
    ? "bg-destructive"
    : isWarning
      ? "bg-warning"
      : "bg-primary";

  return (
    <div className={cn("space-y-1.5", className)}>
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground">
          {compact ? null : `${formatUsd(used)} ${t.dashboard.vsBudget} `}
          <span className="tabular font-medium text-foreground">{formatUsd(budget)}</span>
        </span>
        <span
          className={cn(
            "tabular font-semibold",
            isExhausted ? "text-destructive-text" : isWarning ? "text-warning-text" : "text-foreground",
          )}
        >
          {percent}
        </span>
      </div>

      <Progress value={ratio * 100} indicatorClassName={indicatorClass} aria-label={`Anggaran terpakai ${percent}`} />

      {typeof tokensUsed === "number" && typeof tokenLimit === "number" ? (
        <p className="text-[11px] text-muted-foreground">
          Token per run:{" "}
          <span className="tabular text-foreground">
            {formatNumber(tokensUsed)} / {formatNumber(tokenLimit)}
          </span>
        </p>
      ) : null}

      {isWarning || isExhausted ? (
        <p
          className={cn(
            "flex items-center gap-1 text-[11px] font-medium",
            isExhausted ? "text-destructive-text" : "text-warning-text",
          )}
        >
          <AlertTriangle className="h-3 w-3" aria-hidden="true" />
          {isExhausted ? "Anggaran habis. Agent dihentikan sampai anggaran dinaikkan." : t.usage.budgetWarning}
        </p>
      ) : null}
    </div>
  );
}

