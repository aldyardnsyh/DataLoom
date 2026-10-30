"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, RotateCcw, Square, Wand2 } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { notify } from "@/lib/notify";

import { EmptyState } from "@/components/domain/empty-state";
import { ErrorState } from "@/components/domain/error-state";
import { RunTimeline } from "@/components/domain/run-timeline";
import { StatusBadge } from "@/components/domain/status-badge";
import { BlockSkeleton, LoadingAnnouncer, PageHeaderSkeleton } from "@/components/domain/state-loading";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/domain/confirm-dialog";
import { useCanMutate } from "@/components/providers/session-provider";
import { useRunStream } from "@/hooks/use-run-stream";
import { api, classifyError } from "@/lib/api";
import { formatDateTime, formatDuration, formatNumber, formatPercent, formatTime } from "@/lib/format";
import { t } from "@/lib/i18n/id";
import type { RunNode } from "@/lib/types";
import { cn } from "@/lib/utils";

function SummaryTile({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <Card>
      <CardContent className="p-3">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        <p className={cn("mt-1 text-lg font-bold tabular", tone ?? "text-foreground")}>{value}</p>
      </CardContent>
    </Card>
  );
}

/** Run Monitor detail (PRD 18.3 P6) with live SSE updates. */
export default function RunDetailPage() {
  const params = useParams<{ runId: string }>();
  const runId = params.runId;
  const queryClient = useQueryClient();
  const canMutate = useCanMutate();

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);

  // Navigating to another run must not keep a node selected from the previous
  // one (ids can repeat across runs).
  useEffect(() => {
    setSelectedNodeId(null);
    setConfirmCancel(false);
  }, [runId]);

  const query = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.runs.get(runId),
    enabled: Boolean(runId),
    // Poll as a fallback so a dropped SSE stream never leaves the page stale.
    refetchInterval: (state) => {
      const status = state.state.data?.status;
      return status === "running" || status === "queued" || status === "retrying" ? 5_000 : false;
    },
  });

  // `retrying` is NOT terminal: the worker will run the node again, so the
  // stream must stay open and cancel must stay available.
  const isLive =
    query.data?.status === "running" ||
    query.data?.status === "queued" ||
    query.data?.status === "retrying";
  const stream = useRunStream(runId, Boolean(isLive));

  const cancelMutation = useMutation({
    mutationFn: () => api.runs.cancel(runId),
    onSuccess: () => {
      notify.success(`Run ${runId} dibatalkan.`, { description: t.notify.runCancelled });
      void queryClient.invalidateQueries({ queryKey: ["run", runId] });
    },
    onError: (error: Error) => notify.error(t.notify.failedTitle, { description: error.message }),
  });

  const run = query.data;

  // Merge persisted node rows with live SSE updates. Live values win while the
  // run is streaming (they are strictly newer); once the stream closes, the
  // next poll/reload replaces this with persisted rows anyway.
  const nodes: RunNode[] = useMemo(() => {
    if (!run) return [];
    const merged = run.nodes.map((node) => {
      const live = stream.nodes.get(node.node_id);
      return live ? ({ ...node, ...live } as RunNode) : node;
    });
    // Include nodes that only exist in the live stream (brand new rows).
    for (const [nodeId, live] of stream.nodes) {
      if (!merged.some((node) => node.node_id === nodeId)) {
        merged.push({
          id: live.node_id,
          run_id: run.id,
          node_id: live.node_id,
          node_type: live.node_type ?? "unknown",
          status: live.status ?? "pending",
          retry_count: live.retry_count ?? 0,
          started_at: null,
          finished_at: null,
          duration_ms: live.duration_ms ?? null,
          rows_in: live.rows_in ?? 0,
          rows_out: live.rows_out ?? 0,
          rows_quarantined: live.rows_quarantined ?? 0,
          error_code: live.error_code ?? null,
          error_message: live.error_message ?? null,
          error: null,
        });
      }
    }
    return merged;
  }, [run, stream.nodes]);

  const failedNode = nodes.find((node) => node.status === "failed") ?? null;

  // A run that exists but has not executed any node yet is a valid, honest
  // empty state (e.g. still queued). We never render an empty timeline as if
  // the run had finished with zero nodes.
  const hasNodes = nodes.length > 0;

  if (query.isLoading) {
    return (
      <div className="page-container space-y-6">
        <LoadingAnnouncer />
        <PageHeaderSkeleton />
        <div className="grid min-w-0 gap-4 sm:grid-cols-2 xl:grid-cols-5">
          {Array.from({ length: 5 }).map((_, index) => (
            <BlockSkeleton key={index} height={72} />
          ))}
        </div>
        <BlockSkeleton height={320} />
      </div>
    );
  }

  if (query.isError || !run) {
    const kind = query.isError ? classifyError(query.error).kind : "not_found";
    return (
      <div className="page-container">
        <ErrorState
          kind={kind}
          message={query.isError ? classifyError(query.error).message : undefined}
          requestId={query.isError ? classifyError(query.error).requestId : undefined}
          onRetry={
            kind === "not_found"
              ? undefined
              : () => void query.refetch()
          }
        />
      </div>
    );
  }

  const status = stream.status ?? run.status;
  // A gate recomputed mid-run is newer than the persisted checklist.
  const gate = stream.gate ?? run.gate;

  return (
    <div className="page-container space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-bold tracking-tight">{t.runs.detailTitle}</h1>
            <StatusBadge status={status} />
            {isLive ? (
              <Badge variant={stream.connection === "live" ? "success" : "warning"} className="gap-1">
                <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
                {stream.connection === "live" ? t.state.live : t.state.disconnected}
              </Badge>
            ) : null}
          </div>
          <p className="font-mono text-xs text-muted-foreground">
            {run.id} · v{run.version} · {run.trigger}
          </p>
          {isLive && stream.connection === "disconnected" ? (
            <p role="status" className="flex items-center gap-1 text-xs font-medium text-warning-text">
              <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
              {t.runs.disconnected}
            </p>
          ) : null}
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void query.refetch()}
          >
            <RotateCcw className="h-3.5 w-3.5" />
            {t.common.refresh}
          </Button>
          <Button
            variant="destructive"
            size="sm"
            disabled={!canMutate || !isLive}
            title={canMutate ? undefined : t.state.disabledTooltip}
            onClick={() => setConfirmCancel(true)}
          >
            <Square className="h-3.5 w-3.5" />
            {t.runs.cancelRun}
          </Button>
        </div>
      </header>

      <section aria-label="Ringkasan run" className="grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <SummaryTile label={t.runs.rowsIn} value={formatNumber(run.rows_in)} />
        <SummaryTile label={t.runs.rowsOut} value={formatNumber(run.rows_out)} />
        <SummaryTile
          label={t.runs.quarantined}
          value={formatNumber(run.rows_quarantined)}
          tone="text-warning-text"
        />
        <SummaryTile label={t.runs.duration} value={formatDuration(run.duration_ms)} />
        <SummaryTile label={t.runs.retries} value={formatNumber(run.retry_total)} />
      </section>

      <div className="grid min-w-0 gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>{t.runs.timeline}</CardTitle>
          </CardHeader>
          <CardContent>
            {hasNodes ? (
              <RunTimeline nodes={nodes} onSelect={setSelectedNodeId} selectedNodeId={selectedNodeId} />
            ) : (
              <EmptyState
                compact
                title="Belum ada node yang dieksekusi"
                description="Run masih menunggu antrian atau worker belum memulai node pertama."
                primaryAction={{ label: "Lihat semua run", href: "/runs" }}
              />
            )}

            {failedNode ? (
              <div className="mt-4 rounded-lg border border-destructive/30 bg-destructive/5 p-3">
                <p className="text-sm font-semibold text-destructive-text">
                  {failedNode.node_id} gagal
                </p>
                <pre className="mt-1.5 max-h-40 overflow-auto rounded bg-background p-2 font-mono text-[11px] text-foreground">
                  {(failedNode.error_message ?? "Tidak ada pesan galat.") +
                    (failedNode.error_code ? `\ncode: ${failedNode.error_code}` : "")}
                </pre>
                <Button variant="outline" size="sm" className="mt-2" asChild>
                  <Link href="/agents">
                    <Wand2 className="h-3.5 w-3.5" />
                    {t.runs.askHealing}
                  </Link>
                </Button>
              </div>
            ) : null}
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>{t.runs.gateChecklist}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {gate ? (
                <>
                  {gate.items.map((item) => (
                    <div key={item.rule} className="flex items-start gap-2 text-xs">
                      <StatusBadge status={item.decision === "pass" ? "succeeded" : "failed"} showIcon />
                      <div className="min-w-0 flex-1">
                        <p className="font-medium text-foreground">{item.rule}</p>
                        <p className="text-muted-foreground">{item.evidence}</p>
                      </div>
                    </div>
                  ))}
{run.quarantine_ratio !== null ? (
                    <p className="border-t border-border pt-2 text-[11px] text-muted-foreground">
                      Rasio karantina: <span className="tabular text-foreground">{formatPercent(run.quarantine_ratio)}</span>
                    </p>
                  ) : null}
                  <Link
                    href="/data-contract"
                    className="inline-flex min-h-8 items-center text-[11px] font-medium text-primary underline underline-offset-4 hover:text-primary/80"
                  >
                    {t.nav.contract}
                  </Link>
                </>
              ) : (
                <p className="text-xs text-muted-foreground">Daftar periksa belum tersedia.</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>{t.runs.liveLog}</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="console max-h-64 overflow-auto p-2" aria-live="polite">
                {stream.events.length === 0 ? (
                  <p className="text-muted-foreground">Menunggu peristiwa…</p>
                ) : (
                  stream.events.map((event) => (
                    <p key={`${event.seq}-${event.at}`} className="whitespace-pre-wrap">
                      <span className="text-muted-foreground">{formatTime(event.at)}</span>{" "}
                      {event.text}
                    </p>
                  ))
                )}
              </div>
              <p className="mt-2 text-[11px] text-muted-foreground">
                {t.common.started}: {formatDateTime(run.started_at ?? run.created_at)}
              </p>
            </CardContent>
          </Card>
        </div>
      </div>

      <ConfirmDialog
        open={confirmCancel}
        onOpenChange={setConfirmCancel}
        title={t.runs.cancelRun}
        description="Node yang sedang berjalan akan menyelesaikan batch-nya lalu run dihentikan. Tindakan ini tidak dapat dibatalkan."
        resourceName={run.id.slice(0, 8)}
        confirmLabel={t.runs.cancelRun}
        onConfirm={() => cancelMutation.mutate()}
        pending={cancelMutation.isPending}
      />
    </div>
  );
}







