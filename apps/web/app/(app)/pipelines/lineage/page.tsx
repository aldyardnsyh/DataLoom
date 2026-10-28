"use client";

import { useQuery } from "@tanstack/react-query";
import { GitBranch, Loader2 } from "lucide-react";

import { EmptyState } from "@/components/domain/empty-state";
import { ErrorState } from "@/components/domain/error-state";
import { LineageGraphView } from "@/components/domain/lineage-graph";
import { LoadingAnnouncer } from "@/components/domain/state-loading";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useProject } from "@/components/providers/project-provider";
import { api, classifyError } from "@/lib/api";
import { t } from "@/lib/i18n/id";

/** Standalone lineage page (FR-027); the Data Explorer embeds the same view. */
export default function LineagePage() {
  const { activeProjectId } = useProject();
  const query = useQuery({
    queryKey: ["lineage", activeProjectId],
    queryFn: () => api.pipelines.lineage(activeProjectId as string),
    enabled: Boolean(activeProjectId),
  });

  if (!activeProjectId) {
    return (
      <div className="page-container">
        <EmptyState title={t.dashboard.noProjectsTitle} description={t.dashboard.noProjectsBody} />
      </div>
    );
  }

  if (query.isLoading) {
    return (
      <div className="page-container">
        <LoadingAnnouncer />
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> {t.common.loading}
        </div>
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

  return (
    <div className="page-container space-y-4">
      <header>
        <h1 className="flex items-center gap-2 text-xl font-bold tracking-tight">
          <GitBranch className="h-5 w-5 text-primary" aria-hidden="true" />
          {t.explorer.lineageTitle}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">{t.explorer.subtitle}</p>
      </header>

      <Card>
        <CardHeader className="flex flex-row items-center gap-2">
          <CardTitle>source → bronze → silver → gold</CardTitle>
          <Badge variant="outline">{query.data?.nodes.length ?? 0} node</Badge>
        </CardHeader>
        <CardContent>{query.data ? <LineageGraphView graph={query.data} /> : null}</CardContent>
      </Card>
    </div>
  );
}
