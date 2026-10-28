"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import { FileCheck2, ShieldCheck, SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { EmptyState } from "@/components/domain/empty-state";
import { ErrorState } from "@/components/domain/error-state";
import { BlockSkeleton, LoadingAnnouncer } from "@/components/domain/state-loading";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useProject } from "@/components/providers/project-provider";
import { api, classifyError } from "@/lib/api";
import { formatPercent } from "@/lib/format";
import { t } from "@/lib/i18n/id";
import type { PipelineVersion } from "@/lib/types";

interface ValidationRule {
  kind: string;
  column: string;
  severity: string;
}

function extractRules(version: PipelineVersion | null): ValidationRule[] {
  if (!version) return [];
  const rules: ValidationRule[] = [];
  for (const node of version.dag.nodes) {
    if (node.type !== "validate") continue;
    const params = node.params as { rules?: ValidationRule[] };
    for (const rule of params.rules ?? []) {
      rules.push({
        kind: String(rule.kind ?? "unknown"),
        column: String(rule.column ?? "-"),
        severity: String(rule.severity ?? "error"),
      });
    }
  }
  return rules;
}

/**
 * Data Contract (`/data-contract`).
 *
 * The page that answers "why does this exist when cleaning is a commodity":
 * it PRINTS the rules a dataset must satisfy before it may be used. A
 * spreadsheet has no such document; its rules live in someone's head and
 * change silently.
 *
 * The gate rules are the fixed, code-evaluated A0 set. The row rules are read
 * from the current version of each pipeline's validate nodes. The project
 * policy block shows the actual threshold the gate uses, so the contract is a
 * statement about this project, not a generic brochure.
 */
export default function DataContractPage() {
  const { activeProject, activeProjectId } = useProject();

  const pipelinesQuery = useQuery({
    queryKey: ["pipelines", activeProjectId],
    queryFn: () => api.pipelines.list(activeProjectId as string),
    enabled: Boolean(activeProjectId),
  });

  const settingsQuery = useQuery({
    queryKey: ["project-settings", activeProjectId],
    queryFn: () => api.projects.settings(activeProjectId as string),
    enabled: Boolean(activeProjectId),
  });

  const pipelines = useMemo(() => pipelinesQuery.data ?? [], [pipelinesQuery.data]);

  // The contract lives in each pipeline's current version (its validate nodes),
  // so every pipeline needs its own versions call. `useQueries` keeps them in
  // the cache individually, exactly like the canvas page does.
  const versionQueries = useQueries({
    queries: pipelines.map((pipeline) => ({
      queryKey: ["pipeline-versions", pipeline.id],
      queryFn: () => api.pipelines.versions(pipeline.id),
      enabled: Boolean(pipeline.id),
    })),
  });

  const rulesByPipeline = pipelines.map((pipeline, index) => {
    const version = versionQueries[index]?.data?.[0] ?? null;
    return { pipeline, version, rules: extractRules(version) };
  });

  if (!activeProjectId) {
    return (
      <div className="page-container">
        <EmptyState
          title={t.explorer.contractNoProjectTitle}
          description={t.explorer.contractNoProjectBody}
          primaryAction={{ label: t.dashboard.createFirst, href: "/projects" }}
        />
      </div>
    );
  }

  const settings = settingsQuery.data ?? null;

  return (
    <div className="page-container space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-bold tracking-tight">{t.explorer.contractTitle}</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{t.explorer.contractBody}</p>
        </div>
        {activeProject ? (
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            {t.explorer.contractScope}
            <Badge variant="outline">{activeProject.name}</Badge>
          </div>
        ) : null}
      </header>

      <section className="rounded-card border border-border bg-background p-4 shadow-card">
        <div className="flex items-start gap-2.5">
          <SlidersHorizontal className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-foreground">{t.explorer.contractPolicy}</h2>
            <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted-foreground">
              {t.explorer.contractPolicyBody}
            </p>
          </div>
        </div>

        {settingsQuery.isLoading ? (
          <div className="mt-4 space-y-2">
            <LoadingAnnouncer />
            <Skeleton className="h-14 w-full" />
          </div>
        ) : settingsQuery.isError ? (
          <p role="alert" className="mt-3 text-xs text-destructive-text">
            {t.explorer.contractSettingsError}
          </p>
        ) : settings ? (
          <dl className="mt-4 grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3">
            <div className="bg-background p-3">
              <dt className="text-[11px] font-medium text-muted-foreground">
                {t.explorer.contractThreshold}
              </dt>
              <dd className="tabular mt-1 text-lg font-bold text-foreground">
                {formatPercent(settings.max_quarantine_ratio, 2)}
              </dd>
            </div>
            <div className="bg-background p-3">
              <dt className="text-[11px] font-medium text-muted-foreground">
                {t.explorer.contractApproval}
              </dt>
              <dd className="mt-1 text-sm font-semibold text-foreground">
                {settings.require_approval_for_destructive
                  ? t.explorer.contractRequired
                  : t.explorer.contractNotRequired}
              </dd>
            </div>
            <div className="bg-background p-3">
              <dt className="text-[11px] font-medium text-muted-foreground">
                {t.explorer.contractFreshness}
              </dt>
              <dd className="tabular mt-1 text-lg font-bold text-foreground">
                {settings.freshness_threshold_minutes}{" "}
                <span className="text-xs font-medium text-muted-foreground">
                  {t.explorer.contractMinutes}
                </span>
              </dd>
            </div>
          </dl>
        ) : null}
      </section>

      <section className="rounded-card border border-border bg-background shadow-card">
        <div className="border-b border-border p-4">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <ShieldCheck className="h-4 w-4 text-primary" aria-hidden="true" />
            {t.explorer.contractGate}
          </h2>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted-foreground">
            {t.explorer.contractGateNote}
          </p>
        </div>
        <div className="p-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t.explorer.contractRuleKind}</TableHead>
                <TableHead>{t.explorer.contractRule}</TableHead>
                <TableHead>{t.explorer.contractMeaning}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {t.explorer.contractGateRules.map((gate) => (
                <TableRow key={gate.rule}>
                  <TableCell className="font-mono text-xs">{gate.rule}</TableCell>
                  <TableCell className="text-sm font-medium text-foreground">{gate.title}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{gate.body}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </section>

      <section className="rounded-card border border-border bg-background shadow-card">
        <div className="border-b border-border p-4">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <FileCheck2 className="h-4 w-4 text-primary" aria-hidden="true" />
            {t.explorer.contractRules}
          </h2>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted-foreground">
            {t.explorer.contractRuleNote}
          </p>
        </div>

        <div className="space-y-4 p-4">
          {pipelinesQuery.isLoading ? (
            <div className="space-y-2">
              <LoadingAnnouncer />
              <BlockSkeleton height={140} />
            </div>
          ) : pipelinesQuery.isError ? (
            <ErrorState
              compact
              kind={classifyError(pipelinesQuery.error).kind}
              message={classifyError(pipelinesQuery.error).message}
              requestId={classifyError(pipelinesQuery.error).requestId}
              onRetry={() => void pipelinesQuery.refetch()}
            />
          ) : pipelines.length === 0 ? (
            <EmptyState
              compact
              title={t.explorer.contractNoPipelineTitle}
              description={t.explorer.contractNoPipelineBody}
              primaryAction={{ label: t.pipelines.title, href: "/pipelines" }}
            />
          ) : (
            rulesByPipeline.map(({ pipeline, version, rules }, index) => {
              const versionQuery = versionQueries[index];
              return (
                <div key={pipeline.id} className="rounded-lg border border-border">
                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2.5">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-foreground">{pipeline.name}</p>
                      <p className="mt-0.5 text-[11px] text-muted-foreground">
                        {version
                          ? `${t.explorer.contractVersion} ${version.version} ${t.explorer.contractCurrent}`
                          : t.explorer.contractNoRules}
                      </p>
                    </div>
                    <Badge variant="outline">
                      {t.explorer.contractVersion} {pipeline.current_version ?? "-"}
                    </Badge>
                  </div>

                  {versionQuery?.isLoading ? (
                    <div className="p-3">
                      <Skeleton className="h-24 w-full" />
                    </div>
                  ) : versionQuery?.isError ? (
                    <div className="p-3">
                      <ErrorState
                        compact
                        kind={classifyError(versionQuery.error).kind}
                        message={t.explorer.contractVersionError}
                        requestId={classifyError(versionQuery.error).requestId}
                        onRetry={() => void versionQuery.refetch()}
                      />
                    </div>
                  ) : rules.length === 0 ? (
                    <div className="px-3 py-3">
                      <p className="text-xs text-muted-foreground">{t.explorer.contractNoRules}</p>
                      <Link
                        href={`/pipelines/${pipeline.id}`}
                        className="mt-2 inline-flex min-h-8 items-center text-xs font-medium text-primary underline underline-offset-4 hover:text-primary/80"
                      >
                        {t.explorer.contractEnforcedVersions}
                      </Link>
                    </div>
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>{t.explorer.contractRuleKind}</TableHead>
                          <TableHead>{t.explorer.contractRuleColumn}</TableHead>
                          <TableHead>{t.explorer.contractRuleSeverity}</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {rules.map((rule, ruleIndex) => (
                          <TableRow key={`${pipeline.id}-${ruleIndex}`}>
                            <TableCell className="font-mono text-xs">{rule.kind}</TableCell>
                            <TableCell className="font-mono text-xs">{rule.column}</TableCell>
                            <TableCell>
                              <Badge variant={rule.severity === "error" ? "destructive" : "warning"}>
                                {rule.severity}
                              </Badge>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  )}
                </div>
              );
            })
          )}
        </div>
      </section>

      <section className="rounded-card border border-border bg-background p-4 shadow-card">
        <h2 className="text-sm font-semibold text-foreground">{t.explorer.contractEnforcedTitle}</h2>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted-foreground">
          {t.explorer.contractEnforcedBody}
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button asChild variant="outline" size="sm">
            <Link href="/runs">{t.explorer.contractEnforcedRuns}</Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link href="/data">{t.explorer.contractEnforcedQuarantine}</Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link href="/pipelines">{t.explorer.contractEnforcedVersions}</Link>
          </Button>
        </div>
      </section>
    </div>
  );
}
