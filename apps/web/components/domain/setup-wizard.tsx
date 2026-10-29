"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  CircleDashed,
  Copy,
  FileCheck2,
  KeyRound,
  Play,
  Plus,
  Radio,
  Rocket,
  Terminal,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { ErrorState } from "@/components/domain/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useProject } from "@/components/providers/project-provider";
import { useCanMutate } from "@/components/providers/session-provider";
import { api, classifyError } from "@/lib/api";
import { isDemoMode } from "@/lib/demo/mode";
import { copyToClipboard, formatPercent } from "@/lib/format";
import { t } from "@/lib/i18n/id";
import { notify } from "@/lib/notify";
import { goldDatasetName, starterDag } from "@/lib/starter-dag";
import type { ApiKeyCreated } from "@/lib/types";

type StepState = "done" | "next" | "todo";

/**
 * First-run setup.
 *
 * A production install should not require the operator to know the order of
 * the domain (project -> source -> key -> pipeline -> run) or to hand-write a
 * DAG before seeing a single row. This page performs those steps with real
 * API calls, in that order, and shows the endpoint and key exactly once so
 * data can be sent immediately.
 *
 * Nothing here is a mock: the same endpoints the products page uses.
 */
export function SetupWizard() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const canMutate = useCanMutate();
  const { projects, activeProjectId, setActiveProjectId } = useProject();

  const [projectName, setProjectName] = useState("");
  const [projectDescription, setProjectDescription] = useState("");
  const [sourceName, setSourceName] = useState("");
  const [apiKey, setApiKey] = useState<ApiKeyCreated | null>(null);

  const projectId = activeProjectId ?? projects[0]?.id ?? null;

  const sourcesQuery = useQuery({
    queryKey: ["sources", projectId],
    queryFn: () => api.sources.list(projectId as string),
    enabled: Boolean(projectId),
  });
  const pipelinesQuery = useQuery({
    queryKey: ["pipelines", projectId],
    queryFn: () => api.pipelines.list(projectId as string),
    enabled: Boolean(projectId),
  });
  const runsQuery = useQuery({
    queryKey: ["runs", projectId],
    queryFn: () => api.runs.list(projectId as string),
    enabled: Boolean(projectId),
  });
  const keysQuery = useQuery({
    queryKey: ["api-keys", projectId],
    queryFn: () => api.keys.list(projectId as string),
    enabled: Boolean(projectId),
  });
  const settingsQuery = useQuery({
    queryKey: ["project-settings", projectId],
    queryFn: () => api.projects.settings(projectId as string),
    enabled: Boolean(projectId),
  });
  const installationQuery = useQuery({
    queryKey: ["installation", "llm"],
    queryFn: () => api.installation.status(),
    retry: false,
  });

  const sources = sourcesQuery.data ?? [];
  const pipelines = pipelinesQuery.data ?? [];
  const runs = runsQuery.data ?? [];
  const keys = keysQuery.data ?? [];

  const createProject = useMutation({
    mutationFn: () =>
      api.projects.create({
        name: projectName.trim(),
        description: projectDescription.trim() || null,
      }),
    onSuccess: async (project) => {
      notify.success(t.setup.projectCreated, { description: t.notify.projectCreated });
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
      setActiveProjectId(project.id);
      setProjectName("");
      setProjectDescription("");
    },
    onError: (error: Error) =>
      notify.error(t.notify.failedTitle, { description: error.message }),
  });

  const createSource = useMutation({
    mutationFn: () =>
      api.sources.create(projectId as string, {
        name: sourceName.trim(),
        config: { type: "webhook" },
      }),
    onSuccess: async () => {
      notify.success(t.setup.sourceCreated, { description: t.notify.sourceCreated });
      setSourceName("");
      await queryClient.invalidateQueries({ queryKey: ["sources", projectId] });
    },
    onError: (error: Error) =>
      notify.error(t.notify.failedTitle, { description: error.message }),
  });

  const createKey = useMutation({
    mutationFn: () => api.keys.create(projectId as string, "Kunci ingest"),
    onSuccess: async (created) => {
      setApiKey(created);
      notify.success(t.setup.keyCreated, { description: t.setup.keyOnce });
      await queryClient.invalidateQueries({ queryKey: ["api-keys", projectId] });
    },
    onError: (error: Error) =>
      notify.error(t.notify.failedTitle, { description: error.message }),
  });

  const createPipeline = useMutation({
    mutationFn: () => {
      const source = sources[0];
      if (!source) throw new Error(t.setup.pipelineMissingSource);
      return api.pipelines.create(projectId as string, {
        name: "Pipeline pertama",
        description: "Sumber, validasi, dan sink Gold. Aturan kolom bisa ditambah di kanvas.",
        dag: starterDag(source.id, goldDatasetName(source.name)),
      });
    },
    onSuccess: async (pipeline) => {
      notify.success(t.setup.pipelineCreated, { description: t.notify.pipelineSaved });
      await queryClient.invalidateQueries({ queryKey: ["pipelines", projectId] });
      router.push(`/pipelines/${pipeline.id}`);
    },
    onError: (error: Error) =>
      notify.error(t.notify.failedTitle, { description: error.message }),
  });

  const startRun = useMutation({
    mutationFn: () => {
      const pipeline = pipelines[0];
      if (!pipeline) throw new Error(t.setup.runMissingPipeline);
      return api.pipelines.run(pipeline.id);
    },
    onSuccess: async (response) => {
      notify.success(t.setup.runStarted, { description: t.notify.runStarted });
      await queryClient.invalidateQueries({ queryKey: ["runs", projectId] });
      router.push(`/runs/${response.run.id}`);
    },
    onError: (error: Error) =>
      notify.error(t.notify.failedTitle, { description: error.message }),
  });

  const steps = useMemo(() => {
    const list: { key: string; state: StepState }[] = [
      { key: "project", state: projects.length > 0 ? "done" : "next" },
      { key: "source", state: sources.length > 0 ? "done" : projects.length > 0 ? "next" : "todo" },
      { key: "key", state: keys.length > 0 ? "done" : sources.length > 0 ? "next" : "todo" },
      { key: "pipeline", state: pipelines.length > 0 ? "done" : sources.length > 0 ? "next" : "todo" },
      { key: "run", state: runs.length > 0 ? "done" : pipelines.length > 0 ? "next" : "todo" },
    ];
    return list;
  }, [projects.length, sources.length, keys.length, pipelines.length, runs.length]);

  const doneCount = steps.filter((step) => step.state === "done").length;
  const stateOf = (key: string): StepState =>
    steps.find((step) => step.key === key)?.state ?? "todo";

  const snippet = apiKey
    ? `curl -X POST "${sources[0]?.ingest_path ?? "/v1/ingest/<source-id>"}" \\\n  -H "X-API-Key: ${apiKey.plaintext_key}" \\\n  -H "Content-Type: application/json" \\\n  -d '[{"order_id":"A-1","total":10}]'`
    : "";

  const loadError = [sourcesQuery, pipelinesQuery, runsQuery, keysQuery].find(
    (query) => query.isError,
  );

  return (
    <div className="space-y-4">
      <section className="rounded-card border border-border bg-background p-4 shadow-card">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-foreground">
              {t.setup.progress}: {doneCount}/{steps.length}
            </h2>
            <p className="mt-1 max-w-2xl text-xs leading-relaxed text-muted-foreground">
              {t.setup.subtitle}
            </p>
          </div>
          {isDemoMode() ? (
            <Badge variant="warning">{t.demo.badge}</Badge>
          ) : null}
        </div>
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden="true">
          <div
            className="h-full rounded-full bg-primary transition-[width]"
            style={{ width: `${Math.round((doneCount / steps.length) * 100)}%` }}
          />
        </div>
      </section>

      {loadError ? (
        <ErrorState
          compact
          kind={classifyError(loadError.error).kind}
          message={classifyError(loadError.error).message}
          requestId={classifyError(loadError.error).requestId}
          onRetry={() => void loadError.refetch()}
        />
      ) : null}

      <ol className="space-y-3" data-setup-steps>
        <SetupStep
          index={1}
          state={stateOf("project")}
          title={t.setup.projectTitle}
          body={t.setup.projectBody}
        >
          {projects.length > 0 ? (
            <p className="text-xs text-muted-foreground">
              {projects.map((project) => project.name).join(", ")}
            </p>
          ) : (
            <form
              className="space-y-3"
              onSubmit={(event) => {
                event.preventDefault();
                if (!canMutate || projectName.trim().length === 0) return;
                createProject.mutate();
              }}
            >
              <div className="space-y-1">
                <Label htmlFor="setup-project-name">{t.setup.projectNameLabel}</Label>
                <Input
                  id="setup-project-name"
                  value={projectName}
                  placeholder={t.setup.projectNamePlaceholder}
                  onChange={(event) => setProjectName(event.target.value)}
                  disabled={!canMutate}
                  required
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="setup-project-description">
                  {t.setup.projectDescriptionLabel}
                </Label>
                <Textarea
                  id="setup-project-description"
                  value={projectDescription}
                  placeholder={t.setup.projectDescriptionPlaceholder}
                  onChange={(event) => setProjectDescription(event.target.value)}
                  disabled={!canMutate}
                  rows={2}
                />
              </div>
              <Button type="submit" size="sm" disabled={!canMutate || projectName.trim().length === 0}>
                <Plus className="h-4 w-4" aria-hidden="true" />
                {t.setup.projectCreate}
              </Button>
            </form>
          )}
        </SetupStep>

        <SetupStep
          index={2}
          state={stateOf("source")}
          title={t.setup.sourceTitle}
          body={t.setup.sourceBody}
        >
          {sources.length > 0 ? (
            <p className="text-xs text-muted-foreground">
              {sources.map((source) => source.name).join(", ")}
            </p>
          ) : (
            <form
              className="space-y-3"
              onSubmit={(event) => {
                event.preventDefault();
                if (!canMutate || sourceName.trim().length === 0) return;
                createSource.mutate();
              }}
            >
              <div className="space-y-1">
                <Label htmlFor="setup-source-name">{t.setup.sourceNameLabel}</Label>
                <Input
                  id="setup-source-name"
                  value={sourceName}
                  placeholder={t.setup.sourceNamePlaceholder}
                  onChange={(event) => setSourceName(event.target.value)}
                  disabled={!canMutate || !projectId}
                  required
                />
              </div>
              <Button
                type="submit"
                size="sm"
                disabled={!canMutate || !projectId || sourceName.trim().length === 0}
              >
                <Radio className="h-4 w-4" aria-hidden="true" />
                {t.setup.sourceCreate}
              </Button>
            </form>
          )}
        </SetupStep>

        <SetupStep
          index={3}
          state={stateOf("key")}
          title={t.setup.endpointLabel}
          body={t.setup.keyOnce}
        >
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => createKey.mutate()}
                disabled={!canMutate || !projectId || sources.length === 0}
              >
                <KeyRound className="h-4 w-4" aria-hidden="true" />
                {t.setup.keyCreate}
              </Button>
              {keys.length > 0 ? (
                <Badge variant="outline">
                  {keys.length} {t.nav.sources.toLowerCase()}
                </Badge>
              ) : null}
            </div>

            {apiKey ? (
              <div className="space-y-2 rounded-lg border border-border bg-muted/40 p-3">
                <div className="space-y-1">
                  <p className="text-[11px] font-medium text-muted-foreground">
                    {t.setup.endpointLabel}
                  </p>
                  <code className="block break-all font-mono text-xs text-foreground">
                    {sources[0]?.ingest_path ?? "/v1/ingest/<source-id>"}
                  </code>
                </div>
                <div className="space-y-1">
                  <p className="text-[11px] font-medium text-muted-foreground">
                    {t.setup.snippetLabel}
                  </p>
                  <pre className="overflow-x-auto rounded-md bg-background p-2 font-mono text-[11px] leading-relaxed text-foreground">
                    {snippet}
                  </pre>
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={async () => {
                    const ok = await copyToClipboard(snippet);
                    if (ok) {
                      notify.success(t.common.copied, { duration: 3000 });
                    } else {
                      notify.error(t.notify.failedTitle, {
                        description: "Browser menolak akses clipboard.",
                      });
                    }
                  }}
                >
                  <Copy className="h-4 w-4" aria-hidden="true" />
                  {t.setup.snippetCopy}
                </Button>
              </div>
            ) : null}
          </div>
        </SetupStep>

        <SetupStep
          index={4}
          state={stateOf("pipeline")}
          title={t.setup.pipelineTitle}
          body={t.setup.pipelineBody}
        >
          <div className="space-y-2">
            <Button
              type="button"
              size="sm"
              onClick={() => createPipeline.mutate()}
              disabled={!canMutate || !projectId || sources.length === 0}
            >
              <Rocket className="h-4 w-4" aria-hidden="true" />
              {t.setup.pipelineCreate}
            </Button>
            {pipelines.length > 0 ? (
              <ul className="space-y-1">
                {pipelines.map((pipeline) => (
                  <li key={pipeline.id}>
                    <Link
                      href={`/pipelines/${pipeline.id}`}
                      className="inline-flex min-h-8 items-center text-xs font-medium text-primary underline underline-offset-4 hover:text-primary/80"
                    >
                      {pipeline.name}
                    </Link>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </SetupStep>

        <SetupStep
          index={5}
          state={stateOf("run")}
          title={t.setup.runTitle}
          body={t.setup.runBody}
        >
          <Button
            type="button"
            size="sm"
            onClick={() => startRun.mutate()}
            disabled={!canMutate || !projectId || pipelines.length === 0}
          >
            <Play className="h-4 w-4" aria-hidden="true" />
            {t.setup.runStart}
          </Button>
        </SetupStep>
      </ol>

      <section className="rounded-card border border-border bg-background p-4 shadow-card">
        <div className="flex items-start gap-2.5">
          <FileCheck2 className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-foreground">{t.setup.reviewTitle}</h2>
            <p className="mt-1 max-w-2xl text-xs leading-relaxed text-muted-foreground">
              {t.setup.reviewBody}
            </p>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button asChild variant="outline" size="sm">
            <Link href="/data">{t.setup.reviewData}</Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link href="/data-contract">{t.setup.reviewContract}</Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link href="/runs">{t.setup.reviewRuns}</Link>
          </Button>
        </div>
      </section>

      <section className="rounded-card border border-border bg-background p-4 shadow-card">
        <div className="flex items-start gap-2.5">
          <Terminal className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-foreground">{t.setup.configTitle}</h2>
            <p className="mt-1 max-w-2xl text-xs leading-relaxed text-muted-foreground">
              {t.setup.configBody}
            </p>
          </div>
        </div>

        <dl className="mt-3 grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 xl:grid-cols-4">
          <div className="bg-background p-3">
            <dt className="text-[11px] font-medium text-muted-foreground">
              {t.setup.configThreshold}
            </dt>
            <dd className="tabular mt-1 text-sm font-semibold text-foreground">
              {settingsQuery.data
                ? formatPercent(settingsQuery.data.max_quarantine_ratio, 2)
                : t.common.notAvailable}
            </dd>
          </div>
          <div className="bg-background p-3">
            <dt className="text-[11px] font-medium text-muted-foreground">
              {t.setup.configApproval}
            </dt>
            <dd className="mt-1 text-sm font-semibold text-foreground">
              {settingsQuery.data
                ? settingsQuery.data.require_approval_for_destructive
                  ? t.explorer.contractRequired
                  : t.explorer.contractNotRequired
                : t.common.notAvailable}
            </dd>
          </div>
          <div className="bg-background p-3">
            <dt className="text-[11px] font-medium text-muted-foreground">{t.setup.configAi}</dt>
            <dd className="mt-1 text-sm font-semibold text-foreground">
              {installationQuery.data?.api_key_configured
                ? t.setup.configAiReady
                : t.setup.configAiMissing}
            </dd>
          </div>
          <div className="bg-background p-3">
            <dt className="text-[11px] font-medium text-muted-foreground">
              {t.setup.configMembers}
            </dt>
            <dd className="mt-1 text-sm font-semibold text-foreground">
              <Link
                href="/admin/team"
                className="inline-flex min-h-8 items-center text-xs font-medium text-primary underline underline-offset-4 hover:text-primary/80"
              >
                {t.setup.configOpen}
              </Link>
            </dd>
          </div>
        </dl>
      </section>
    </div>
  );
}

function SetupStep({
  index,
  state,
  title,
  body,
  children,
}: {
  index: number;
  state: StepState;
  title: string;
  body: string;
  children: React.ReactNode;
}) {
  const label =
    state === "done" ? t.setup.statusDone : state === "next" ? t.setup.statusNext : t.setup.statusTodo;

  return (
    <li className="rounded-card border border-border bg-background p-4 shadow-card">
      <div className="flex items-start gap-3">
        <span
          className={
            state === "done"
              ? "flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-success/10 text-success"
              : state === "next"
                ? "flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary"
                : "flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground"
          }
        >
          {state === "done" ? (
            <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
          ) : state === "next" ? (
            <span className="text-xs font-bold tabular">{index}</span>
          ) : (
            <CircleDashed className="h-4 w-4" aria-hidden="true" />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-foreground">{title}</h2>
            <Badge variant={state === "done" ? "success" : state === "next" ? "default" : "outline"}>
              {label}
            </Badge>
          </div>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{body}</p>
          <div className="mt-3">{children}</div>
        </div>
      </div>
    </li>
  );
}
