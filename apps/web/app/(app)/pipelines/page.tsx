"use client";

import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Plus, Workflow } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/domain/empty-state";
import { ErrorState } from "@/components/domain/error-state";
import { CardGridSkeleton, LoadingAnnouncer } from "@/components/domain/state-loading";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useCanMutate } from "@/components/providers/session-provider";
import { useProject } from "@/components/providers/project-provider";
import { api, classifyError } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { t } from "@/lib/i18n/id";

/** Pipelines list with the explicit empty state from PRD 18.4. */
export default function PipelinesPage() {
  const { activeProjectId } = useProject();
  const canMutate = useCanMutate();

  const query = useQuery({
    queryKey: ["pipelines", activeProjectId],
    queryFn: () => api.pipelines.list(activeProjectId as string),
    enabled: Boolean(activeProjectId),
  });

  if (query.isLoading) {
    return (
      <div className="page-container space-y-6">
        <LoadingAnnouncer />
        <CardGridSkeleton count={3} />
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

  const pipelines = query.data ?? [];

  return (
    <div className="page-container space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight">{t.pipelines.title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t.pipelines.subtitle}</p>
        </div>
        {canMutate ? (
          <Button asChild>
            <Link href="/pipelines/new">
              <Plus className="h-4 w-4" />
              {t.pipelines.new}
            </Link>
          </Button>
        ) : (
          <Button disabled title={t.state.disabledTooltip}>
            <Plus className="h-4 w-4" />
            {t.pipelines.new}
          </Button>
        )}
      </header>

      {pipelines.length === 0 ? (
        <EmptyState
          icon={Workflow}
          title={t.pipelines.emptyTitle}
          description={t.pipelines.emptyBody}
          primaryAction={{
            label: t.pipelines.new,
            href: canMutate ? "/pipelines/new" : undefined,
            disabled: !canMutate,
            disabledReason: t.state.disabledTooltip,
          }}
          secondaryAction={{ label: t.pipelines.generateWithAi, href: "/pipelines/new?mode=ai" }}
        />
      ) : (
        <ul className="grid min-w-0 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {pipelines.map((pipeline) => (
            <li key={pipeline.id}>
              <Card className="h-full">
                <CardHeader>
                  <CardTitle className="truncate">
                    <Link href={`/pipelines/${pipeline.id}`} className="inline-flex min-h-9 items-center hover:underline">
                      {pipeline.name}
                    </Link>
                  </CardTitle>
                  <p className="line-clamp-2 text-xs text-muted-foreground">
                    {pipeline.description ?? "Tanpa deskripsi"}
                  </p>
                </CardHeader>
                <CardContent className="flex items-center justify-between text-xs text-muted-foreground">
                  <span>
                    {t.common.version} {pipeline.current_version ?? "Belum ada"}
                  </span>
                  <span>{formatRelative(pipeline.updated_at)}</span>
                  <Button asChild variant="ghost" size="sm">
                    <Link href={`/pipelines/${pipeline.id}`}>
                      <ExternalLink className="h-3.5 w-3.5" />
                      Buka kanvas
                    </Link>
                  </Button>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}


