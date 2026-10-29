"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Check, Loader2, Sparkles, X } from "lucide-react";
import { useMemo, useState } from "react";
import { notify } from "@/lib/notify";

import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

import { EmptyState } from "@/components/domain/empty-state";
import { ErrorState } from "@/components/domain/error-state";
import { ProposalDiff, RiskBadge } from "@/components/domain/proposal-diff";
import { StatusBadge } from "@/components/domain/status-badge";
import { BlockSkeleton, LoadingAnnouncer } from "@/components/domain/state-loading";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useCanMutate } from "@/components/providers/session-provider";
import { useProject } from "@/components/providers/project-provider";
import { api, classifyError } from "@/lib/api";
import { applyAgentProposals } from "@/lib/agent-client";
import { formatDateTime, formatNumber, formatPercent } from "@/lib/format";
import { LAYER_LABEL } from "@/lib/status";
import { t } from "@/lib/i18n/id";
import type { AgentProposal, AgentRun, Evidence } from "@/lib/types";

/** Evidence may arrive as a string or as a list of objects; render both. */
function evidenceText(proposal: AgentProposal): string {
  const raw = proposal.evidence as unknown;
  if (!raw) return "";
  if (typeof raw === "string") return raw;
  if (typeof raw === "object" && raw !== null) {
    const record = raw as Record<string, unknown>;
    if (typeof record.evidence === "string") return record.evidence;
    if (Array.isArray(record.items)) {
      return (record.items as Evidence[])
        .map((item) => [item.key, item.value].filter(Boolean).join(": "))
        .join(" Â· ");
    }
  }
  return "";
}

/**
 * Agent Workspace (PRD 18.3 P8).
 *
 * Left: agent summary + execution trace. Right: proposal cards with evidence,
 * risk, confidence, before/after diff and Approve/Reject.
 * Sticky footer: "x of y approved" + "apply as new pipeline version".
 */
export default function AgentWorkspacePage() {
  const { activeProjectId } = useProject();
  const canMutate = useCanMutate();
  const queryClient = useQueryClient();

  const [agentRunId, setAgentRunId] = useState<string | null>(null);
  const [pipelineId, setPipelineId] = useState<string>("");
  const [datasetPickerOpen, setDatasetPickerOpen] = useState(false);
  const [rejectTarget, setRejectTarget] = useState<AgentProposal | null>(null);
  const [approveTarget, setApproveTarget] = useState<AgentProposal | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  const datasetsQuery = useQuery({
    queryKey: ["datasets", activeProjectId],
    queryFn: () => api.datasets.list(activeProjectId as string),
    enabled: Boolean(activeProjectId),
  });

  const pipelinesQuery = useQuery({
    queryKey: ["pipelines", activeProjectId],
    queryFn: () => api.pipelines.list(activeProjectId as string),
    enabled: Boolean(activeProjectId),
  });

  const runQuery = useQuery({
    queryKey: ["agent-run", agentRunId],
    queryFn: () => api.agents.getRun(agentRunId as string),
    enabled: Boolean(agentRunId),
    refetchInterval: (state) => {
      const status = state.state.data?.status;
      return status === "running" || status === "pending" ? 3_000 : false;
    },
  });

  const proposalsQuery = useQuery({
    queryKey: ["agent-proposals", agentRunId],
    queryFn: () => api.agents.proposals(agentRunId as string),
    enabled: Boolean(agentRunId),
  });

  const eventsQuery = useQuery({
    queryKey: ["agent-events", agentRunId],
    queryFn: () => api.agents.events(agentRunId as string),
    enabled: Boolean(agentRunId),
  });

  const startMutation = useMutation({
    mutationFn: (datasetId: string) => api.agents.startProfile(activeProjectId as string, { dataset_id: datasetId }),
    onSuccess: (data) => {
      setAgentRunId(data.agent_run.id);
      notify.info("Profil dimulai.", { description: t.notify.agentProfileStarted });
    },
    onError: (error: Error) => notify.error(t.notify.failedTitle, { description: error.message }),
  });

  const decideMutation = useMutation({
    mutationFn: ({ id, decision, note }: { id: string; decision: "approve" | "reject"; note?: string }) =>
      api.agents.decide(id, { decision, note }),
    onSuccess: async (_result, variables) => {
      if (variables.decision === "approve") {
        notify.success(t.agents.decisionSuccess, { description: t.notify.proposalApproved });
      } else {
        notify.info(t.agents.decisionSuccess, { description: t.notify.proposalRejected });
      }
      setRejectTarget(null);
      setRejectReason("");
      await queryClient.invalidateQueries({ queryKey: ["agent-proposals", agentRunId] });
    },
  });

  const applyMutation = useMutation({
    mutationFn: async () => {
      if (!agentRunId) throw new Error("Tidak ada agent run.");
      return applyAgentProposals(agentRunId, pipelineId);
    },
    onSuccess: () => {
      notify.success(t.agents.applySuccess, { description: t.notify.agentApplied });
      void queryClient.invalidateQueries({ queryKey: ["agent-proposals", agentRunId] });
    },
    onError: (error: Error) => notify.error(t.notify.failedTitle, { description: error.message }),
  });
  const proposals = proposalsQuery.data ?? [];
  const approved = proposals.filter((proposal) => proposal.status === "approved").length;
  const run: AgentRun | null = runQuery.data ?? null;
  const datasets = datasetsQuery.data ?? [];
  const pipelines = pipelinesQuery.data ?? [];

  const tokenRatio = useMemo(() => {
    if (!run || run.budget_tokens <= 0) return 0;
    return Math.min(1, run.tokens_used / run.budget_tokens);
  }, [run]);

  if (!activeProjectId) {
    return (
      <div className="page-container">
        <EmptyState
          title={t.dashboard.noProjectsTitle}
          description={t.dashboard.noProjectsBody}
          primaryAction={{ label: t.dashboard.createFirst, href: "/projects" }}
        />
      </div>
    );
  }

  if (datasetsQuery.isLoading) {
    return (
      <div className="page-container space-y-4">
        <LoadingAnnouncer />
        <BlockSkeleton height={80} />
        <BlockSkeleton height={320} />
      </div>
    );
  }

  if (datasetsQuery.isError) {
    const kind = classifyError(datasetsQuery.error).kind;
    return (
      <div className="page-container">
        <ErrorState
          kind={kind}
          message={classifyError(datasetsQuery.error).message}
          requestId={classifyError(datasetsQuery.error).requestId}
          onRetry={() => void datasetsQuery.refetch()}
        />
      </div>
    );
  }

  return (
    <div className="page-container flex min-h-[calc(100dvh-var(--app-chrome,3.5rem))] flex-col gap-4 pb-24">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold tracking-tight">
            <Bot className="h-5 w-5 text-primary" aria-hidden="true" />
            {t.agents.title}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">{t.agents.subtitle}</p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Select value={pipelineId} onValueChange={setPipelineId}>
            <SelectTrigger className="w-56" aria-label="Pipeline tujuan">
              <SelectValue placeholder="Pipeline tujuanâ€¦" />
            </SelectTrigger>
            <SelectContent>
              {pipelines.length === 0 ? (
                <SelectItem value="__none" disabled>
                  Belum ada pipeline
                </SelectItem>
              ) : (
                pipelines.map((pipeline) => (
                  <SelectItem key={pipeline.id} value={pipeline.id}>
                    {pipeline.name}
                  </SelectItem>
                ))
              )}
            </SelectContent>
          </Select>

          <Button
            onClick={() => setDatasetPickerOpen(true)}
            disabled={!canMutate || datasets.length === 0 || startMutation.isPending}
            title={
              canMutate
                ? undefined
                : datasets.length === 0
                  ? "Belum ada dataset untuk diprofilkan."
                  : t.state.disabledTooltip
            }
          >
            {startMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bot className="h-4 w-4" />}
            {t.agents.startProfile}
          </Button>
        </div>
      </header>

      {!agentRunId ? (
        <EmptyState
          icon={Bot}
          title={t.agents.emptyTitle}
          description={t.agents.emptyBody}
          primaryAction={{
            label: t.agents.startProfile,
            onClick: () => datasets[0] && startMutation.mutate(datasets[0].id),
            disabled: !canMutate || datasets.length === 0,
            disabledReason: t.state.disabledTooltip,
          }}
        />
      ) : (
        <div className="grid min-w-0 flex-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
          {/* Left: agent state + conversation summary + trace */}
          <div className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Status agent</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {runQuery.isLoading ? (
                  <BlockSkeleton height={60} />
                ) : run ? (
                  <>
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge status={run.status} />
                      <Badge variant="outline">{run.agent_type}</Badge>
                      <span className="text-xs text-muted-foreground">
                        {t.agents.elapsed}: {formatDateTime(run.started_at)}
                      </span>
                    </div>

                    <div className="space-y-1">
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-muted-foreground">{t.agents.tokensUsed}</span>
                        <span className="tabular text-foreground">
                          {formatNumber(run.tokens_used)} / {formatNumber(run.budget_tokens)}
                        </span>
                      </div>
                      <Progress value={tokenRatio * 100} aria-label={`Token terpakai ${formatPercent(tokenRatio, 0)}`} />
                      {tokenRatio >= 0.8 ? (
                        <p className="text-[11px] font-medium text-warning-text">{t.agents.budgetWarning}</p>
                      ) : null}
                    </div>

                    {run.status === "waiting_approval" ? (
                      <p className="rounded-md bg-warning/10 px-2 py-1.5 text-xs font-medium text-warning-text">
                        {t.agents.waitingApproval}
                      </p>
                    ) : null}
                    {run.status === "insufficient_evidence" ? (
                      <p className="rounded-md bg-warning/10 px-2 py-1.5 text-xs font-medium text-warning-text">
                        {t.agents.insufficientEvidence}
                      </p>
                    ) : null}
                    {run.error_message ? (
                      <p className="text-xs text-muted-foreground">{run.error_message}</p>
                    ) : null}
                  </>
                ) : null}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{t.agents.trace}</CardTitle>
              </CardHeader>
              <CardContent>
                <ol className="space-y-1.5 text-xs">
                  {(eventsQuery.data ?? []).length === 0 ? (
                    <li className="text-muted-foreground">Belum ada langkah tercatat.</li>
                  ) : (
                    (eventsQuery.data ?? []).map((event) => (
                      <li key={event.id} className="flex items-start gap-2">
                        <StatusBadge
                          status={
                            event.status === "invalid"
                              ? "failed"
                              : event.status === "empty"
                                ? "needs_attention"
                                : "succeeded"
                          }
                          showIcon
                        />
                        <div>
                          <p className="font-medium text-foreground">{event.step}</p>
                          <p className="text-muted-foreground">{formatDateTime(event.at)}</p>
                        </div>
                      </li>
                    ))
                  )}
                </ol>
              </CardContent>
            </Card>
          </div>

          {/* Right: proposals */}
          <Card className="flex min-h-0 flex-col">
            <CardHeader>
              <CardTitle>
                {t.agents.proposals} ({proposals.length})
              </CardTitle>
            </CardHeader>
            <CardContent className="min-h-0 flex-1 p-0">
              <ScrollArea className="h-[560px] px-4 py-3">
                {proposalsQuery.isLoading ? (
                  <BlockSkeleton height={200} />
                ) : proposalsQuery.isError ? (
                  <ErrorState
                    compact
                    kind={classifyError(proposalsQuery.error).kind}
                    message={classifyError(proposalsQuery.error).message}
                    requestId={classifyError(proposalsQuery.error).requestId}
                    onRetry={() => void proposalsQuery.refetch()}
                  />
                ) : proposals.length === 0 ? (
                  <p className="py-10 text-center text-sm text-muted-foreground">
                    Belum ada usulan. Jalankan profiler pada dataset yang memiliki data.
                  </p>
                ) : (
                  <ul className="space-y-3">
                    {proposals.map((proposal) => {
                      const payload = proposal.payload as Record<string, unknown>;
                      const beforeSamples = (payload.before_samples as string[] | undefined) ?? [];
                      const afterSamples = (payload.after_samples as string[] | undefined) ?? [];
                      const decided = proposal.status !== "pending";
                      return (
                        <li key={proposal.id}>
                          <Card>
                            <CardHeader className="flex flex-row items-start justify-between gap-2">
                              <div className="min-w-0">
                                <CardTitle className="truncate">
                                  <code className="font-mono text-xs text-primary">{String(payload.op)}</code>{" "}
                                  pada <span className="font-mono">{String(payload.column)}</span>
                                </CardTitle>
                                <p className="mt-1 text-xs text-muted-foreground">
                                  {proposal.title}
                                </p>
                              </div>
                              <div className="flex shrink-0 flex-col items-end gap-1">
                                <RiskBadge risk={proposal.risk} />
                                <StatusBadge status={proposal.status} />
                              </div>
                            </CardHeader>
                            <CardContent className="space-y-2">
                              <p className="text-xs">
                                <span className="font-medium text-foreground">{t.agents.evidence}: </span>
                                <span className="text-muted-foreground">{evidenceText(proposal)}</span>
                              </p>

                              {proposal.confidence !== null ? (
                                <div className="space-y-1">
                                  <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                                    <span>{t.agents.confidence}</span>
                                    <span className="tabular">{formatPercent(proposal.confidence, 0)}</span>
                                  </div>
                                  <Progress value={proposal.confidence * 100} />
                                </div>
                              ) : null}

                              <ProposalDiff before={beforeSamples} after={afterSamples} />

                              <div className="flex items-center gap-2 pt-1">
                                <Button
                                  size="sm"
                                  disabled={!canMutate || decided || decideMutation.isPending}
                                  title={canMutate ? undefined : t.state.disabledTooltip}
                                  onClick={() => {
                                    /*
                                     * A destructive rule (e.g. drop_if) removes
                                     * rows. Approving it must not be a single
                                     * misclick, so it goes through the same
                                     * confirmation pattern as other destructive
                                     * actions. Low-risk rules stay one click.
                                     */
                                    if (proposal.risk === "destructive") {
                                      setApproveTarget(proposal);
                                    } else {
                                      decideMutation.mutate({ id: proposal.id, decision: "approve" });
                                    }
                                  }}
                                >
                                  <Check className="h-3.5 w-3.5" />
                                  {t.agents.approve}
                                </Button>
                                <Button
                                  size="sm"
                                  variant="outline"
                                  disabled={!canMutate || decided || decideMutation.isPending}
                                  title={canMutate ? undefined : t.state.disabledTooltip}
                                  onClick={() => {
                                    setRejectTarget(proposal);
                                    setRejectReason("");
                                  }}
                                >
                                  <X className="h-3.5 w-3.5" />
                                  {t.agents.reject}
                                </Button>
                                {proposal.decision_note ? (
                                  <span className="text-[11px] text-muted-foreground">
                                    {proposal.decision_note}
                                  </span>
                                ) : null}
                              </div>
                            </CardContent>
                          </Card>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </ScrollArea>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Dataset picker for starting a profile run. */}
      <Dialog open={datasetPickerOpen} onOpenChange={setDatasetPickerOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.agents.startProfile}</DialogTitle>
            <DialogDescription>
              Pilih dataset yang ingin dianalisis Schema Profiler. Agent hanya membaca sampel, tidak mengubah data.
            </DialogDescription>
          </DialogHeader>
          <ul className="max-h-80 space-y-1.5 overflow-y-auto">
            {datasets.map((dataset) => (
              <li key={dataset.id}>
                <button
                  type="button"
                  onClick={() => {
                    setDatasetPickerOpen(false);
                    startMutation.mutate(dataset.id);
                  }}
                  className="flex w-full items-center justify-between gap-2 rounded-lg border border-border px-3 py-2 text-left text-sm transition-colors hover:bg-muted"
                >
                  <span className="min-w-0">
                    <span className="block truncate font-medium text-foreground">{dataset.name}</span>
                    <span className="block font-mono text-[11px] text-muted-foreground">
                      {dataset.layer} Â· {formatNumber(dataset.row_count)} {t.common.rows}
                    </span>
                  </span>
                  <Badge variant="outline">{LAYER_LABEL[dataset.layer] ?? dataset.layer}</Badge>
                </button>
              </li>
            ))}
          </ul>
        </DialogContent>
      </Dialog>

      {/* Reject needs an optional reason so the decision trail stays useful. */}
      <Dialog open={rejectTarget !== null} onOpenChange={(open) => !open && setRejectTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.agents.reject}</DialogTitle>
            <DialogDescription>
              Usulan <span className="font-mono text-foreground">{String(rejectTarget?.payload.op)}</span> pada{" "}
              <span className="font-mono text-foreground">{String(rejectTarget?.payload.column)}</span> akan ditandai
              ditolak. Alasan Anda tercatat di jejak keputusan.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="reject-reason">{t.agents.rejectReason}</Label>
            <Textarea
              id="reject-reason"
              value={rejectReason}
              onChange={(event) => setRejectReason(event.target.value)}
              rows={3}
              maxLength={500}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRejectTarget(null)}>
              {t.common.cancel}
            </Button>
            <Button
              variant="destructive"
              disabled={decideMutation.isPending}
              onClick={() =>
                rejectTarget &&
                decideMutation.mutate({
                  id: rejectTarget.id,
                  decision: "reject",
                  note: rejectReason.trim() || undefined,
                })
              }
            >
              {t.agents.reject}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={approveTarget !== null} onOpenChange={(open) => { if (!open) setApproveTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t.agents.approveDestructiveTitle}</AlertDialogTitle>
            <AlertDialogDescription>{t.agents.approveDestructiveBody}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={decideMutation.isPending}>{t.common.cancel}</AlertDialogCancel>
            <Button
              variant="destructive"
              disabled={decideMutation.isPending}
              onClick={() => {
                if (!approveTarget) return;
                decideMutation.mutate({ id: approveTarget.id, decision: "approve" });
                setApproveTarget(null);
              }}
            >
              <Check className="h-4 w-4" aria-hidden="true" />
              {t.agents.approve}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Sticky footer: approval progress + apply */}
      {agentRunId ? (
        <div className="fixed inset-x-0 bottom-0 z-20 border-t border-border bg-background/95 backdrop-blur-none">
          <div className="page-container flex flex-wrap items-center justify-between gap-2 py-2">
            <p className="text-sm">
              <span className="tabular font-semibold text-foreground">
                {approved} {t.common.of} {proposals.length}
              </span>{" "}
              <span className="text-muted-foreground">{t.agents.approvedCount}</span>
            </p>
            <Button
              onClick={() => applyMutation.mutate()}
              disabled={
                !canMutate ||
                approved === 0 ||
                pipelineId === "" ||
                applyMutation.isPending ||
                proposals.some((proposal) => proposal.risk === "destructive" && proposal.status === "pending")
              }
              title={
                !canMutate
                  ? t.state.disabledTooltip
                  : pipelineId === ""
                    ? "Pilih pipeline tujuan terlebih dahulu."
                    : proposals.some(
                          (proposal) => proposal.risk === "destructive" && proposal.status === "pending",
                        )
                      ? "Putuskan usulan destruktif terlebih dahulu."
                      : undefined
              }
            >
              {applyMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
              {t.agents.applyApproved}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}







