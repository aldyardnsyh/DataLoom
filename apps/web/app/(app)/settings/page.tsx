"use client";

import { useQuery } from "@tanstack/react-query";
import { Cpu, Eye, ShieldCheck } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/domain/empty-state";
import { ErrorState } from "@/components/domain/error-state";
import { ModelAiStatusPanel } from "@/components/domain/model-ai-status";
import { BlockSkeleton, LoadingAnnouncer } from "@/components/domain/state-loading";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useSession } from "@/components/providers/session-provider";
import { api, classifyError } from "@/lib/api";
import { t } from "@/lib/i18n/id";

/**
 * Personal settings.
 *
 * Scope rule: anything that changes what OTHER people can do (roles, quality
 * thresholds, audit trail, LLM provider) lives under /admin. What remains here
 * is the signed-in person's own context -- who they are, in which organization,
 * with which role -- plus a read-only view of the shared model configuration so
 * they can tell whether AI features are available.
 *
 * There is deliberately no tab bar: two sections do not need one, and the
 * previous six-tab screen made admin controls look like personal preferences.
 */
export default function SettingsPage() {
  const { session } = useSession();
  const role = session?.membership.role ?? "viewer";
  const isViewer = role === "viewer";
  const isOrgAdmin = role === "owner" || role === "admin";

  const modelAiQuery = useQuery({
    queryKey: ["installation", "llm"],
    queryFn: () => api.installation.status(),
    staleTime: 60_000,
  });

  return (
    <div className="page-container space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-bold tracking-tight">{t.settings.title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t.settings.subtitleUser}</p>
        </div>
        {isOrgAdmin ? (
          <Button asChild variant="outline" size="sm">
            <Link href="/admin">
              <ShieldCheck className="h-4 w-4" aria-hidden="true" />
              {t.adminHub.title}
            </Link>
          </Button>
        ) : null}
      </header>

      {isViewer ? (
        <div
          role="status"
          className="flex items-center gap-2 rounded-card border border-info/30 bg-info/10 px-3 py-2 text-sm text-info"
        >
          <Eye className="h-4 w-4" aria-hidden="true" />
          {t.settings.viewerBanner}
        </div>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>{t.settings.accountTitle}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="user-name">{t.settings.accountName}</Label>
            <Input id="user-name" defaultValue={session?.user.name ?? ""} readOnly disabled />
          </div>
          <div className="space-y-1">
            <Label htmlFor="user-email">Email</Label>
            <Input id="user-email" defaultValue={session?.user.email ?? ""} readOnly disabled />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="org-name">{t.settings.orgName}</Label>
              <Input id="org-name" defaultValue={session?.org.name ?? ""} readOnly disabled />
            </div>
            <div className="space-y-1">
              <Label>{t.settings.roleLabel}</Label>
              <div className="flex h-9 items-center gap-2 rounded-lg border border-input bg-muted px-3">
                <Badge variant="outline">{role}</Badge>
                <span className="text-xs text-muted-foreground">{t.settings.roleHint}</span>
              </div>
            </div>
          </div>
          <p className="text-[11px] text-muted-foreground">{t.settings.accountNote}</p>
        </CardContent>
      </Card>

      <section className="space-y-3">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Cpu className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            {t.modelAi.title}
          </h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{t.modelAi.subtitle}</p>
        </div>

        {modelAiQuery.isLoading ? (
          <div className="space-y-3">
            <LoadingAnnouncer />
            <BlockSkeleton height={280} />
            <BlockSkeleton height={120} />
          </div>
        ) : modelAiQuery.isError ? (
          <ErrorState
            compact
            kind={classifyError(modelAiQuery.error).kind}
            title={t.modelAi.errorTitle}
            message={classifyError(modelAiQuery.error).message}
            requestId={classifyError(modelAiQuery.error).requestId}
            onRetry={() => void modelAiQuery.refetch()}
          />
        ) : modelAiQuery.data ? (
          <>
            <ModelAiStatusPanel status={modelAiQuery.data} />
            <p className="text-[11px] text-muted-foreground">{t.modelAi.readOnlyNote}</p>
          </>
        ) : (
          <EmptyState compact icon={Cpu} title={t.modelAi.emptyTitle} description={t.modelAi.emptyHint} />
        )}
      </section>
    </div>
  );
}