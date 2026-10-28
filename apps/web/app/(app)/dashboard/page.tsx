"use client";

import { useQuery } from "@tanstack/react-query";
import { History, Link2, ListChecks, MessageSquareCode, Play, Workflow } from "lucide-react";
import Link from "next/link";

import { BudgetMeter } from "@/components/domain/budget-meter";
import { EmptyState } from "@/components/domain/empty-state";
import { ErrorState } from "@/components/domain/error-state";
import { StatusBadge } from "@/components/domain/status-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  KpiSkeleton,
  LoadingAnnouncer,
  PageHeaderSkeleton,
  TableSkeleton,
} from "@/components/domain/state-loading";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { useProject } from "@/components/providers/project-provider";
import { useCanMutate } from "@/components/providers/session-provider";
import { api, classifyError } from "@/lib/api";
import { formatDuration, formatNumber, formatPercent } from "@/lib/format";
import { t } from "@/lib/i18n/id";
import type { Run } from "@/lib/types";

function KpiCard({
  label,
  value,
  hint,
  icon: Icon,
}: {
  label: string;
  value: string;
  hint?: React.ReactNode;
  icon: typeof Workflow;
}) {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-center justify-between">
          <p className="text-xs font-medium text-muted-foreground">{label}</p>
          <Icon className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        </div>
        <p className="mt-2 text-xl font-bold tabular tracking-tight text-foreground">{value}</p>
        {hint ? <div className="mt-1 text-xs text-muted-foreground">{hint}</div> : null}
      </CardContent>
    </Card>
  );
}

export default function DashboardPage() {
  const { activeProject, activeProjectId, loading: projectsLoading, projects } = useProject();
  const canMutate = useCanMutate();

  const runsQuery = useQuery({
    queryKey: ["runs", activeProjectId],
    queryFn: () => api.runs.list(activeProjectId as string),
    enabled: Boolean(activeProjectId),
  });

  const usageQuery = useQuery({
    queryKey: ["usage", activeProjectId],
    queryFn: () => api.usage.summary(activeProjectId ?? undefined),
    enabled: Boolean(activeProjectId),
    // Usage page is a P1 surface; a missing endpoint must not break the dashboard.
    retry: false,
  });

  // Queue depth is measured by a SEPARATE endpoint (real Redis XLEN), so the
  // dashboard card stays honest even when no LLM call has happened yet.
  const queueQuery = useQuery({
    queryKey: ["usage-queue"],
    queryFn: () => api.usage.queue(),
    retry: false,
    refetchInterval: 15_000,
  });

  // Every number below is derived from the API response, never hardcoded.
  const runs: Run[] = runsQuery.data ?? [];
  const dayAgo = Date.now() - 24 * 60 * 60 * 1000;
  const runs24h = runs.filter((run) => new Date(run.created_at).getTime() >= dayAgo);
  const succeeded = runs24h.filter((run) => run.status === "succeeded").length;
  const successRate = runs24h.length > 0 ? succeeded / runs24h.length : null;
  const rowsProcessed = runs24h.reduce((total, run) => total + run.rows_out, 0);

  /*
   * A data-driven KPI must not render zeros while the data is still loading:
   * "0 run" and "Belum ada" are claims, not placeholders. The runs list is
   * scoped to the ACTIVE project: deriving these numbers from every project
   * made the headline change meaning whenever the visitor switched project.
   */
  if (projectsLoading || runsQuery.isLoading) {
    return (
      <div className="page-container space-y-6">
        <LoadingAnnouncer />
        <PageHeaderSkeleton />
        <div className="grid min-w-0 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <KpiSkeleton />
          <KpiSkeleton />
          <KpiSkeleton />
          <KpiSkeleton />
        </div>
        <TableSkeleton />
      </div>
    );
  }

  if (projects.length === 0) {
    return (
      <div className="page-container space-y-6">
        <header>
          <h1 className="text-xl font-bold tracking-tight">{t.dashboard.title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t.dashboard.subtitle}</p>
        </header>
        <EmptyState
          icon={Link2}
          title={t.dashboard.noProjectsTitle}
          description={t.dashboard.noProjectsBody}
          primaryAction={{ label: t.dashboard.createFirst, href: "/projects" }}
        />
      </div>
    );
  }

  if (runsQuery.isError) {
    const kind = classifyError(runsQuery.error).kind;
    return (
      <div className="page-container space-y-6">
        <header>
          <h1 className="text-xl font-bold tracking-tight">{t.dashboard.title}</h1>
        </header>
        <ErrorState
          kind={kind}
          message={classifyError(runsQuery.error).message}
          requestId={classifyError(runsQuery.error).requestId}
          onRetry={() => void runsQuery.refetch()}
        />
      </div>
    );
  }

  const usage = usageQuery.data;

  return (
    <div className="page-container space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight">{t.dashboard.title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {t.dashboard.subtitle} · <span className="font-medium text-foreground">{activeProject?.name}</span>
          </p>
        </div>
        {/* Buttons grouped on the right: with three direct children under
            `justify-between`, the first button used to drift to the middle of
            the header, which read as a random gap. */}
        <div className="flex shrink-0 items-center gap-2">
          <Button asChild variant="outline" size="sm">
            <Link href="/runs">
              <History className="h-4 w-4" aria-hidden="true" />
              {t.nav.runs}
            </Link>
          </Button>
          {/* Dashboard has no mutations except "run a pipeline". The control is
              rendered even for viewers so the disabled reason is discoverable
              (PRD 12.5: disabled buttons explain why). */}
          <Button
            variant="default"
            size="sm"
            disabled={!canMutate}
            title={canMutate ? "Buka pipeline untuk menjalankan" : t.state.disabledTooltip}
            asChild={canMutate}
          >
            {canMutate ? (
              <Link href="/pipelines">
                <Play className="h-4 w-4" aria-hidden="true" />
                {t.pipelines.run}
              </Link>
            ) : (
              <>
                <Play className="h-4 w-4" aria-hidden="true" />
                <span>{t.pipelines.run}</span>
              </>
            )}
          </Button>
        </div>
      </header>

      <section aria-label="Metrik utama" className="grid min-w-0 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label={t.dashboard.kpiRuns}
          value={formatNumber(runs24h.length)}
          hint={`${t.dashboard.kpiSuccessRate}: ${successRate === null ? "Belum ada" : formatPercent(successRate, 0)}`}
          icon={Workflow}
        />
        <KpiCard label={t.dashboard.kpiRows} value={formatNumber(rowsProcessed)} icon={ListChecks} />
        <KpiCard
          label={t.dashboard.kpiQueue}
          value={
            queueQuery.data && queueQuery.data.length > 0
              ? formatNumber(queueQuery.data.reduce((total, item) => total + item.length, 0))
              : t.common.none
          }
          hint={queueQuery.data ? t.dashboard.kpiQueueHint : t.common.notAvailable}
          icon={MessageSquareCode}
        />
        <Card>
          <CardContent className="p-4">
            <p className="text-xs font-medium text-muted-foreground">{t.dashboard.kpiCost}</p>
            {usage ? (
              <div className="mt-2">
                <BudgetMeter used={usage.budget.spent_usd} budget={usage.budget.monthly_budget_usd} />
              </div>
            ) : (
              <p className="mt-2 text-sm text-muted-foreground">{t.dashboard.kpiCostUnavailable}</p>
            )}
          </CardContent>
        </Card>
      </section>

      <section className="grid min-w-0 gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>{t.dashboard.recentRuns}</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {runs.length === 0 ? (
              <p className="px-4 py-10 text-center text-sm text-muted-foreground">{t.dashboard.emptyRuns}</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t.dashboard.colRun}</TableHead>
                    <TableHead>{t.common.version}</TableHead>
                    <TableHead>{t.common.trigger}</TableHead>
                    <TableHead>{t.common.status}</TableHead>
                    <TableHead className="text-right">{t.runs.rowsIn}</TableHead>
                    <TableHead className="text-right">{t.runs.quarantined}</TableHead>
                    <TableHead className="text-right">{t.common.duration}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {runs.slice(0, 8).map((run) => (
                    <TableRow key={run.id}>
                      <TableCell>
                        <Link href={`/runs/${run.id}`} className="inline-flex min-h-6 items-center rounded-sm px-1 font-mono text-xs text-primary hover:underline">
                          {run.id.slice(0, 8)}
                        </Link>
                      </TableCell>
                      <TableCell className="tabular">v{run.version}</TableCell>
                      <TableCell className="capitalize">{run.trigger}</TableCell>
                      <TableCell>
                        <StatusBadge status={run.status} />
                      </TableCell>
                      <TableCell className="text-right tabular">{formatNumber(run.rows_in)}</TableCell>
                      <TableCell className="text-right tabular text-warning-text">
                        {formatNumber(run.rows_quarantined)}
                      </TableCell>
                      <TableCell className="text-right tabular">{formatDuration(run.duration_ms)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t.dashboard.pipelineHealth}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {runs.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">{t.dashboard.emptyRuns}</p>
            ) : (
              /*
               * Most recent run per pipeline. `runs` is newest-first, so the
               * FIRST entry per pipeline_id wins. Building the Map with
               * `new Map(runs.map(...))` kept the LAST (oldest) one instead,
               * which showed stale health for every pipeline with >1 run.
               */
              Array.from(
                runs.reduce((latest, run) => {
                  if (!latest.has(run.pipeline_id)) latest.set(run.pipeline_id, run);
                  return latest;
                }, new Map<string, Run>()),
              )
                .map(([, run]) => run)
                .slice(0, 5)
                .map((run) => (
                  <div key={run.pipeline_id} className="flex items-center justify-between gap-2 rounded-lg border border-border px-2.5 py-2">
                    <div className="min-w-0">
                      <p className="truncate font-mono text-xs text-foreground">{run.pipeline_id.slice(0, 8)}</p>
                      <p className="text-[11px] text-muted-foreground">v{run.version}</p>
                    </div>
                    <StatusBadge status={run.status} />
                  </div>
                ))
            )}
          </CardContent>
        </Card>
      </section>
    </div>
  );
}






