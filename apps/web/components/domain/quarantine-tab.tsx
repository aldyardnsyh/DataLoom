"use client";

import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  ShieldAlert,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { DataTable } from "@/components/domain/data-table";
import { EmptyState } from "@/components/domain/empty-state";
import { ErrorState } from "@/components/domain/error-state";
import { StatusBadge } from "@/components/domain/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, classifyError, type ErrorKind } from "@/lib/api";
import { formatNumber, formatPercent, formatRelative } from "@/lib/format";
import { t } from "@/lib/i18n/id";
import type { Dataset, DatasetPreview, GateCheckItem, Run } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Quarantine & reconciliation.
 *
 * The product does not sell "cleaning": a spreadsheet formula can clean a
 * column. It sells the answer to three operational questions that a formula
 * cannot give:
 *   1. Which rows were rejected, with which reason code?
 *   2. Which run rejected them, and how does that compare with the project
 *      threshold?
 *   3. Did the deterministic gate allow gold to be promoted?
 *
 * The tab therefore shows a ledger, not a spare table: rejected rows, reason
 * counts, the run that produced them, and that run's gate checklist. Numbers
 * are read from the API; nothing is computed from a marketing assumption.
 */

const FINISHED_STATUSES = new Set(["succeeded", "failed", "needs_attention", "cancelled"]);

function isFinished(run: Run): boolean {
  return Boolean(run.finished_at) || FINISHED_STATUSES.has(run.status);
}

function runRatio(run: Run): number | null {
  if (run.quarantine_ratio !== null && run.quarantine_ratio !== undefined) {
    return run.quarantine_ratio;
  }
  const denominator = run.rows_out + run.rows_quarantined;
  return denominator > 0 ? run.rows_quarantined / denominator : null;
}

function sortRuns(runs: Run[]): Run[] {
  return [...runs].sort((left, right) => {
    const leftAt = left.started_at ?? left.created_at;
    const rightAt = right.started_at ?? right.created_at;
    return new Date(rightAt).getTime() - new Date(leftAt).getTime();
  });
}

export function QuarantineTab({
  projectId,
  datasets,
  selected,
  threshold,
  onSelectDataset,
}: {
  projectId: string;
  datasets: Dataset[];
  selected: Dataset | null;
  threshold: number | null;
  onSelectDataset: (datasetId: string) => void;
}) {
  const quarantineDatasets = useMemo(
    () => datasets.filter((dataset) => dataset.layer === "quarantine"),
    [datasets],
  );

  const focused =
    selected?.layer === "quarantine" ? selected : quarantineDatasets[0] ?? null;

  const previewQuery = useQuery({
    queryKey: ["dataset-preview", projectId, focused?.id],
    queryFn: () => api.datasets.preview(projectId, focused!.id, 1000),
    enabled: Boolean(projectId && focused?.id),
  });

  const qualityQuery = useQuery({
    queryKey: ["dataset-quality", projectId, focused?.id],
    queryFn: () => api.datasets.quality(projectId, focused!.id),
    enabled: Boolean(projectId && focused?.id),
  });

  const runsQuery = useQuery({
    queryKey: ["runs", projectId],
    queryFn: () => api.runs.list(projectId),
    enabled: Boolean(projectId),
  });

  const runs = useMemo(() => sortRuns(runsQuery.data ?? []), [runsQuery.data]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  /**
   * Default to the newest finished run that actually rejected rows. That is
   * the run an operator came here to understand; a queued run has nothing to
   * explain yet.
   */
  useEffect(() => {
    if (runs.length === 0) {
      if (selectedRunId !== null) setSelectedRunId(null);
      return;
    }
    if (selectedRunId && runs.some((run) => run.id === selectedRunId)) return;

    const finished = runs.filter(isFinished);
    const rejected = (finished.length > 0 ? finished : runs).filter(
      (run) => run.rows_quarantined > 0,
    );
    const fallback = rejected[0] ?? finished[0] ?? runs[0];
    if (fallback) setSelectedRunId(fallback.id);
  }, [runs, selectedRunId]);

  const selectedRun = runs.find((run) => run.id === selectedRunId) ?? null;

  const runDetailQuery = useQuery({
    queryKey: ["run", selectedRunId],
    queryFn: () => api.runs.get(selectedRunId!),
    enabled: Boolean(selectedRunId),
  });

  const gate = runDetailQuery.data?.gate ?? null;
  const reconciliation = gate?.items.find((item) => item.rule === "reconciliation") ?? null;
  const selectedRatio = selectedRun ? runRatio(selectedRun) : null;

  const reasonCounts = useMemo(() => {
    const counts = new Map<string, number>();
    const preview = previewQuery.data;

    if (preview) {
      const reasonIndex = preview.columns.findIndex((column) => column.name === "_reason_code");
      if (reasonIndex >= 0) {
        for (const row of preview.rows) {
          const reason = String(row[reasonIndex] ?? "").trim();
          if (!reason) continue;
          counts.set(reason, (counts.get(reason) ?? 0) + 1);
        }
      }
    }

    // Real API previews of parquet layers currently return no rows. The
    // quality endpoint still carries the persisted reason counts, so the tab
    // stays useful instead of claiming there were no rejections.
    if (counts.size === 0) {
      for (const [reason, count] of Object.entries(qualityQuery.data?.reasons ?? {})) {
        if (!reason) continue;
        counts.set(reason, Number(count) || 0);
      }
    }

    return [...counts.entries()]
      .filter(([, count]) => count > 0)
      .sort((left, right) => right[1] - left[1]);
  }, [previewQuery.data, qualityQuery.data]);

  return (
    <div className="space-y-4">
      <div
        role="note"
        className="flex items-start gap-2.5 rounded-lg border border-warning/30 bg-warning/10 px-4 py-3"
      >
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden="true" />
        <div className="min-w-0">
          <p className="text-sm font-medium text-warning-text">{t.explorer.quarantineTitle}</p>
          <p className="mt-0.5 text-xs leading-relaxed text-warning-text">
            {t.explorer.quarantineBody}
          </p>
        </div>
      </div>

      {quarantineDatasets.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-muted-foreground">
            {t.explorer.quarantinePick}:
          </span>
          {quarantineDatasets.map((dataset) => (
            <Button
              key={dataset.id}
              type="button"
              size="sm"
              variant={dataset.id === focused?.id ? "default" : "outline"}
              aria-pressed={dataset.id === focused?.id}
              onClick={() => onSelectDataset(dataset.id)}
            >
              <span className="font-mono">{dataset.name}</span>
              <span
                className={cn(
                  "tabular rounded px-1.5 py-0.5 text-[11px]",
                  dataset.id === focused?.id
                    ? "bg-primary-foreground/15 text-primary-foreground"
                    : "bg-muted text-muted-foreground",
                )}
              >
                {formatNumber(dataset.row_count)}
              </span>
            </Button>
          ))}
        </div>
      ) : null}

      {focused ? (
        <dl className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 xl:grid-cols-4">
          <div className="bg-background p-3">
            <dt className="text-[11px] font-medium text-muted-foreground">
              {t.explorer.quarantineStored}
            </dt>
            <dd className="mt-1 flex items-baseline gap-1.5">
              <span className="tabular text-lg font-bold text-foreground">
                {formatNumber(focused.row_count)}
              </span>
              <span className="text-[11px] text-muted-foreground">{t.common.rows}</span>
            </dd>
            <p className="mt-1 truncate font-mono text-[10px] text-muted-foreground">
              {focused.name}
            </p>
          </div>

          <div className="bg-background p-3">
            <dt className="text-[11px] font-medium text-muted-foreground">
              {t.explorer.quarantineRunRatio}
            </dt>
            <dd className="mt-1 flex items-baseline gap-1.5">
              <span className="tabular text-lg font-bold text-foreground">
                {formatPercent(selectedRatio, 2)}
              </span>
              <span className="text-[11px] text-muted-foreground">
                {threshold === null
                  ? t.explorer.quarantineNotEvaluated
                  : `${t.explorer.quarantineThreshold} ${formatPercent(threshold, 2)}`}
              </span>
            </dd>
            <p className="mt-1 truncate font-mono text-[10px] text-muted-foreground">
              {selectedRun ? selectedRun.id : t.explorer.quarantineNotEvaluated}
            </p>
          </div>

          <div className="bg-background p-3">
            <dt className="text-[11px] font-medium text-muted-foreground">
              {t.explorer.quarantineGate}
            </dt>
            <dd className="mt-1 flex items-center gap-1.5 text-sm font-semibold">
              {gate ? (
                gate.promotable ? (
                  <>
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-success" aria-hidden="true" />
                    <span className="text-success-text">{t.explorer.quarantinePromotable}</span>
                  </>
                ) : (
                  <>
                    <XCircle className="h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
                    <span className="text-destructive-text">{t.explorer.quarantineBlocked}</span>
                  </>
                )
              ) : (
                <>
                  <CircleDashed className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <span className="text-muted-foreground">{t.explorer.quarantineNotEvaluated}</span>
                </>
              )}
            </dd>
            <p className="mt-1 text-[11px] text-muted-foreground">
              {gate
                ? `${gate.items.filter((item) => item.decision === "pass").length}/${gate.items.length}`
                : t.explorer.quarantineNoGate}
            </p>
          </div>

          <div className="bg-background p-3">
            <dt className="text-[11px] font-medium text-muted-foreground">
              {t.explorer.quarantineReconciliation}
            </dt>
            <dd className="mt-1 flex items-center gap-1.5 text-sm font-semibold">
              {reconciliation ? (
                reconciliation.decision === "pass" ? (
                  <>
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-success" aria-hidden="true" />
                    <span className="text-success-text">{t.runs.gatePassed}</span>
                  </>
                ) : (
                  <>
                    <XCircle className="h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
                    <span className="text-destructive-text">{t.runs.gateFailed}</span>
                  </>
                )
              ) : (
                <>
                  <CircleDashed className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <span className="text-muted-foreground">{t.explorer.quarantineNotEvaluated}</span>
                </>
              )}
            </dd>
            <p className="mt-1 line-clamp-2 text-[11px] text-muted-foreground">
              {reconciliation?.evidence ??
                (gate ? t.explorer.quarantineReconciliationPass : t.explorer.quarantineNoGate)}
            </p>
          </div>
        </dl>
      ) : null}

      <GatePanel
        run={selectedRun}
        gate={gate}
        reconciliation={reconciliation}
        loading={runDetailQuery.isLoading}
        error={runDetailQuery.isError ? classifyError(runDetailQuery.error) : null}
        onRetry={() => void runDetailQuery.refetch()}
      />

      <RunLedger
        runs={runs}
        selectedRunId={selectedRunId}
        onSelect={setSelectedRunId}
        loading={runsQuery.isLoading}
        error={runsQuery.isError ? classifyError(runsQuery.error) : null}
        onRetry={() => void runsQuery.refetch()}
      />

      {focused ? (
        <section className="rounded-lg border border-border">
          <div className="border-b border-border px-4 py-3">
            <h3 className="text-sm font-semibold text-foreground">{t.explorer.quarantineReasons}</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {t.explorer.quarantineReasonBody}
            </p>
          </div>
          <div className="p-4">
            {previewQuery.isLoading || qualityQuery.isLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-2/3" />
              </div>
            ) : previewQuery.isError ? (
              <ErrorState
                compact
                kind={classifyError(previewQuery.error).kind}
                message={classifyError(previewQuery.error).message}
                requestId={classifyError(previewQuery.error).requestId}
                onRetry={() => void previewQuery.refetch()}
              />
            ) : reasonCounts.length === 0 ? (
              <p role="note" className="text-sm text-muted-foreground">
                {t.explorer.quarantineNoReasons}
              </p>
            ) : (
              <ul className="space-y-2">
                {reasonCounts.map(([reason, count]) => {
                  const max = Math.max(...reasonCounts.map(([, value]) => value), 1);
                  return (
                    <li key={reason} className="rounded-lg border border-border p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <Badge variant="warning" className="font-mono">
                          {reason}
                        </Badge>
                        <span className="tabular text-sm font-semibold text-foreground">
                          {formatNumber(count)}
                        </span>
                      </div>
                      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden="true">
                        <div
                          className="h-full rounded-full bg-warning"
                          style={{ width: `${Math.max(4, (count / max) * 100)}%` }}
                        />
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </section>
      ) : (
        <EmptyState
          compact
          icon={ShieldCheck}
          title={t.explorer.quarantineNoDatasetTitle}
          description={t.explorer.quarantineNoDatasetBody}
        />
      )}

      {focused ? (
        <RejectedRows
          dataset={focused}
          preview={previewQuery.data ?? null}
          loading={previewQuery.isLoading}
          error={previewQuery.isError ? classifyError(previewQuery.error) : null}
          onRetry={() => void previewQuery.refetch()}
        />
      ) : null}
    </div>
  );
}

function GatePanel({
  run,
  gate,
  reconciliation,
  loading,
  error,
  onRetry,
}: {
  run: Run | null;
  gate: { promotable: boolean; items: GateCheckItem[]; evaluated_at: string } | null;
  reconciliation: GateCheckItem | null;
  loading: boolean;
  error: { kind: ErrorKind; message: string; requestId: string | null } | null;
  onRetry: () => void;
}) {
  return (
    <section className="rounded-lg border border-border">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-foreground">{t.explorer.quarantineGate}</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {run
              ? `${t.explorer.quarantineRun} ${run.id}, ${formatRelative(run.finished_at ?? run.started_at ?? run.created_at)}`
              : t.explorer.quarantineNoGate}
          </p>
        </div>
        {run ? <StatusBadge status={run.status} /> : null}
      </div>

      <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <div className="space-y-3">
          <div
            className={cn(
              "flex items-start gap-2.5 rounded-lg border px-3 py-2.5",
              reconciliation?.decision === "pass"
                ? "border-success/30 bg-success/10"
                : reconciliation
                  ? "border-destructive/30 bg-destructive/5"
                  : "border-border bg-muted/50",
            )}
          >
            {reconciliation?.decision === "pass" ? (
              <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden="true" />
            ) : reconciliation ? (
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
            ) : (
              <CircleDashed className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
            )}
            <div className="min-w-0">
              <p className="text-xs font-semibold text-foreground">
                {t.explorer.quarantineReconciliation}
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {reconciliation
                  ? reconciliation.evidence
                  : t.explorer.quarantineNoGate}
              </p>
            </div>
          </div>

          {run ? (
            <dl className="grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-border bg-border text-xs">
              <div className="bg-background p-2.5">
                <dt className="text-[11px] text-muted-foreground">{t.explorer.quarantineRowsIn}</dt>
                <dd className="tabular mt-0.5 font-semibold text-foreground">
                  {formatNumber(run.rows_in)}
                </dd>
              </div>
              <div className="bg-background p-2.5">
                <dt className="text-[11px] text-muted-foreground">{t.explorer.quarantineRowsOut}</dt>
                <dd className="tabular mt-0.5 font-semibold text-foreground">
                  {formatNumber(run.rows_out)}
                </dd>
              </div>
              <div className="bg-background p-2.5">
                <dt className="text-[11px] text-muted-foreground">
                  {t.explorer.quarantineRowsRejected}
                </dt>
                <dd className="tabular mt-0.5 font-semibold text-warning-text">
                  {formatNumber(run.rows_quarantined)}
                </dd>
              </div>
            </dl>
          ) : null}

          {run ? (
            <Link
              href={`/runs/${run.id}`}
              className="inline-flex min-h-9 items-center text-xs font-medium text-primary underline underline-offset-4 hover:text-primary/80"
            >
              {t.explorer.quarantineOpenRun}
            </Link>
          ) : null}
        </div>

        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            {t.explorer.contractGate}
          </p>
          {loading ? (
            <div className="mt-2 space-y-2">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-3/4" />
            </div>
          ) : error ? (
            <ErrorState
              compact
              kind={error.kind}
              message={t.explorer.quarantineRunDetailError}
              requestId={error.requestId}
              onRetry={onRetry}
            />
          ) : gate ? (
            <ul className="mt-1 divide-y divide-border">
              {gate.items.map((item) => (
                <li key={item.rule} className="flex items-start gap-2 py-2">
                  {item.decision === "pass" ? (
                    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden="true" />
                  ) : (
                    <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
                  )}
                  <div className="min-w-0">
                    <p className="font-mono text-[11px] font-medium text-foreground">{item.rule}</p>
                    <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                      {item.evidence}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-muted-foreground">{t.explorer.quarantineNoGate}</p>
          )}
        </div>
      </div>
    </section>
  );
}

function RunLedger({
  runs,
  selectedRunId,
  onSelect,
  loading,
  error,
  onRetry,
}: {
  runs: Run[];
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
  loading: boolean;
  error: { kind: ErrorKind; message: string; requestId: string | null } | null;
  onRetry: () => void;
}) {
  return (
    <section className="rounded-lg border border-border">
      <div className="border-b border-border px-4 py-3">
        <h3 className="text-sm font-semibold text-foreground">{t.explorer.quarantineRunLedger}</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {t.explorer.quarantineRunLedgerBody}
        </p>
      </div>

      {loading ? (
        <div className="space-y-2 p-4">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-3/4" />
        </div>
      ) : error ? (
        <div className="p-4">
          <ErrorState
            compact
            kind={error.kind}
            message={error.message}
            requestId={error.requestId}
            onRetry={onRetry}
          />
        </div>
      ) : runs.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">{t.runs.emptyBody}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[680px] border-collapse text-xs" aria-label={t.explorer.quarantineRunLedger}>
            <thead>
              <tr className="bg-muted text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                <th scope="col" className="px-3 py-2 font-medium">{t.explorer.quarantineRun}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t.common.status}</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">{t.explorer.quarantineRowsIn}</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">{t.explorer.quarantineRowsOut}</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">{t.explorer.quarantineRowsRejected}</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">{t.explorer.quarantineRatio}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t.common.actions}</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => {
                const active = run.id === selectedRunId;
                return (
                  <tr
                    key={run.id}
                    className={cn(
                      "border-t border-border",
                      active ? "bg-accent/60" : "hover:bg-muted/50",
                    )}
                  >
                    <td className="px-3 py-2">
                      <button
                        type="button"
                        onClick={() => onSelect(run.id)}
                        aria-pressed={active}
                        className={cn(
                          "rounded-sm font-mono text-left text-[11px] font-medium underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                          active ? "text-accent-foreground" : "text-foreground hover:underline",
                        )}
                      >
                        {run.id}
                      </button>
                    </td>
                    <td className="px-3 py-2">
                      <StatusBadge status={run.status} />
                    </td>
                    <td className="tabular px-3 py-2 text-right text-foreground">
                      {formatNumber(run.rows_in)}
                    </td>
                    <td className="tabular px-3 py-2 text-right text-foreground">
                      {formatNumber(run.rows_out)}
                    </td>
                    <td className="tabular px-3 py-2 text-right font-semibold text-warning-text">
                      {formatNumber(run.rows_quarantined)}
                    </td>
                    <td className="tabular px-3 py-2 text-right text-foreground">
                      {formatPercent(runRatio(run), 2)}
                    </td>
                    <td className="px-3 py-2">
                      <Link
                        href={`/runs/${run.id}`}
                        className="inline-flex min-h-8 items-center text-[11px] font-medium text-primary underline underline-offset-4 hover:text-primary/80"
                      >
                        {t.explorer.quarantineOpenRun}
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function RejectedRows({
  dataset,
  preview,
  loading,
  error,
  onRetry,
}: {
  dataset: Dataset;
  preview: DatasetPreview | null;
  loading: boolean;
  error: { kind: ErrorKind; message: string; requestId: string | null } | null;
  onRetry: () => void;
}) {
  const rows = preview?.rows ?? [];
  const columns = preview?.columns ?? [];
  const reasonIndex = columns.findIndex((column) => column.name === "_reason_code");

  return (
    <section className="rounded-lg border border-border">
      <div className="border-b border-border px-4 py-3">
        <h3 className="text-sm font-semibold text-foreground">{t.explorer.quarantineRowsTitle}</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">{t.explorer.quarantineRowsBody}</p>
      </div>
      <div className="p-4">
        {loading ? (
          <Skeleton className="h-56 w-full" />
        ) : error ? (
          <ErrorState
            compact
            kind={error.kind}
            message={error.message}
            requestId={error.requestId}
            onRetry={onRetry}
          />
        ) : rows.length === 0 ? (
          <p role="note" className="text-sm text-muted-foreground">
            {t.explorer.quarantineNoRows}
          </p>
        ) : (
          <DataTable
            label={`Baris karantina ${dataset.name}`}
            columns={columns.map((column, index) => ({
              key: column.name,
              header: column.name,
              mono: true,
              cell: (row: (string | number | boolean | null)[]) => {
                const value = row[index];
                if (value === null || value === undefined) return "";
                if (column.name === "_reason_code") {
                  return (
                    <Badge variant="warning" className="font-mono">
                      {String(value)}
                    </Badge>
                  );
                }
                return String(value);
              },
            }))}
            rows={rows}
            rowKey={(_, index) => `${dataset.id}-${index}`}
            height={280}
            truncationNotice={preview?.truncated ? t.explorer.truncatedBanner : undefined}
          />
        )}
      </div>
    </section>
  );
}
