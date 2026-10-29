"use client";

import { useQuery } from "@tanstack/react-query";
import { History } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { EmptyState } from "@/components/domain/empty-state";
import { ErrorState } from "@/components/domain/error-state";
import { StatusBadge } from "@/components/domain/status-badge";
import { LoadingAnnouncer, TableSkeleton } from "@/components/domain/state-loading";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { useProject } from "@/components/providers/project-provider";
import { api, classifyError } from "@/lib/api";
import { formatDateTime, formatDuration, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n/id";

const STATUS_FILTERS = [
  { value: "all", label: "Semua status" },
  { value: "running", label: t.status.running },
  { value: "succeeded", label: t.status.succeeded },
  { value: "failed", label: t.status.failed },
  { value: "needs_attention", label: t.status.needsAttention },
  { value: "queued", label: t.status.queued },
];

/** Runs index: the entry point to the Run Monitor detail page. */
export default function RunsPage() {
  const { activeProjectId } = useProject();
  const [status, setStatus] = useState("all");

  const query = useQuery({
    queryKey: ["runs", activeProjectId, status],
    queryFn: () => api.runs.list(activeProjectId as string, status === "all" ? undefined : status),
    enabled: Boolean(activeProjectId),
  });

  if (query.isLoading) {
    return (
      <div className="page-container space-y-6">
        <LoadingAnnouncer />
        <TableSkeleton />
      </div>
    );
  }

  if (query.isError) {
    const kind = classifyError(query.error).kind;
    return (
      <div className="page-container">
        <ErrorState
          kind={kind}
          message={classifyError(query.error).message}
          requestId={classifyError(query.error).requestId}
          onRetry={() => void query.refetch()}
        />
      </div>
    );
  }

  const runs = query.data ?? [];

  return (
    <div className="page-container space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight">{t.runs.title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t.runs.subtitle}</p>
        </div>
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="w-44" aria-label="Filter status">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STATUS_FILTERS.map((filter) => (
              <SelectItem key={filter.value} value={filter.value}>
                {filter.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </header>

      {runs.length === 0 ? (
        <EmptyState
          icon={History}
          title={t.runs.emptyTitle}
          description={t.runs.emptyBody}
          primaryAction={{ label: t.pipelines.title, href: "/pipelines" }}
        />
      ) : (
        <div className="overflow-hidden rounded-card border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Run</TableHead>
                <TableHead>Pipeline</TableHead>
                <TableHead>{t.common.version}</TableHead>
                <TableHead>{t.common.trigger}</TableHead>
                <TableHead>{t.common.status}</TableHead>
                <TableHead className="text-right">Masuk</TableHead>
                <TableHead className="text-right">Keluar</TableHead>
                <TableHead className="text-right">Karantina</TableHead>
                <TableHead className="text-right">{t.common.duration}</TableHead>
                <TableHead>{t.common.started}</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.map((run) => (
                <TableRow key={run.id}>
                  <TableCell className="font-mono text-xs">{run.id.slice(0, 8)}</TableCell>
                  <TableCell className="font-mono text-xs">{run.pipeline_id.slice(0, 8)}</TableCell>
                  <TableCell className="tabular">v{run.version}</TableCell>
                  <TableCell className="capitalize">{run.trigger}</TableCell>
                  <TableCell>
                    <StatusBadge status={run.status} />
                  </TableCell>
                  <TableCell className="text-right tabular">{formatNumber(run.rows_in)}</TableCell>
                  <TableCell className="text-right tabular">{formatNumber(run.rows_out)}</TableCell>
                  <TableCell className="text-right tabular text-warning-text">
                    {formatNumber(run.rows_quarantined)}
                  </TableCell>
                  <TableCell className="text-right tabular">{formatDuration(run.duration_ms)}</TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                    {formatDateTime(run.started_at ?? run.created_at)}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button asChild variant="ghost" size="sm">
                      <Link href={`/runs/${run.id}`}>Lihat</Link>
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}

