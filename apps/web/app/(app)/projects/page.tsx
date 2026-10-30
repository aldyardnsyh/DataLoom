"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderKanban, MoreHorizontal, Plus, Search } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { notify } from "@/lib/notify";

import { ConfirmDialog } from "@/components/domain/confirm-dialog";
import { EmptyState } from "@/components/domain/empty-state";
import { ErrorState } from "@/components/domain/error-state";
import { StatusBadge } from "@/components/domain/status-badge";
import { CardGridSkeleton, LoadingAnnouncer } from "@/components/domain/state-loading";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useCanMutate } from "@/components/providers/session-provider";
import { useProject } from "@/components/providers/project-provider";
import { api, classifyError, type ErrorKind } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { t } from "@/lib/i18n/id";
import type { ProjectSummary } from "@/lib/types";

/**
 * Projects page (PRD 18.3 P3).
 * States: loading skeleton grid, empty card with dashed border, error, and
 * disabled destructive actions for viewers.
 */
export default function ProjectsPage() {
  const queryClient = useQueryClient();
  const { projects, loading, error: projectsError, refresh, setActiveProjectId } = useProject();
  const canMutate = useCanMutate();

  const [search, setSearch] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [deleting, setDeleting] = useState<ProjectSummary | null>(null);

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return projects;
    return projects.filter((project) => project.name.toLowerCase().includes(term));
  }, [projects, search]);

  const createMutation = useMutation({
    mutationFn: () => api.projects.create({ name: name.trim(), description: description.trim() || null }),
    onSuccess: () => {
      notify.success(`Proyek "${name.trim()}" dibuat.`, {
        description: t.notify.projectCreated,
      });
      setCreateOpen(false);
      setName("");
      setDescription("");
      refresh();
    },
    onError: (error: Error) => notify.error(t.notify.failedTitle, { description: error.message || t.projects.createFailed }),
  });

  const deleteMutation = useMutation({
    mutationFn: ({ id, confirmName }: { id: string; confirmName: string }) =>
      api.projects.remove(id, confirmName),
    onSuccess: () => {
      notify.success("Proyek dihapus.", { description: t.notify.projectDeleted });
      setDeleting(null);
      refresh();
    },
    onError: (error: Error) => notify.error(t.notify.failedTitle, { description: error.message }),
  });

  if (loading) {
    return (
      <div className="page-container space-y-6">
        <LoadingAnnouncer />
        <CardGridSkeleton count={6} />
      </div>
    );
  }

  // A failed projects query must never render as "create your first project":
  // the user's data would be replaced by a lie about what exists.
  if (projectsError) {
    return (
      <div className="page-container space-y-6">
        <header>
          <h1 className="text-xl font-bold tracking-tight">{t.projects.title}</h1>
        </header>
        <ErrorState
          kind={projectsError.kind as ErrorKind}
          message={projectsError.message}
          requestId={projectsError.requestId}
          onRetry={() => refresh()}
        />
      </div>
    );
  }

  return (
    <div className="page-container space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight">{t.projects.title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t.projects.subtitle}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button asChild variant="outline" size="sm">
            <Link href="/setup">{t.setup.title}</Link>
          </Button>
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t.projects.searchPlaceholder}
              className="w-56 pl-8"
              aria-label={t.projects.searchPlaceholder}
            />
          </div>
          <Button
            onClick={() => setCreateOpen(true)}
            disabled={!canMutate}
            title={canMutate ? undefined : t.state.disabledTooltip}
          >
            <Plus className="h-4 w-4" />
            {t.projects.new}
          </Button>
        </div>
      </header>

      {filtered.length === 0 ? (
        <EmptyState
          icon={FolderKanban}
          title={search ? "Tidak ada proyek yang cocok" : t.projects.emptyTitle}
          description={t.projects.emptyBody}
          primaryAction={
            search
              ? undefined
              : {
                  label: t.projects.new,
                  onClick: () => setCreateOpen(true),
                  disabled: !canMutate,
                  disabledReason: t.state.disabledTooltip,
                }
          }
        />
      ) : (
        <ul className="grid min-w-0 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {filtered.map((project) => (
            <li key={project.id}>
              <Card className="h-full">
                <CardHeader className="flex flex-row items-start justify-between gap-2">
                  <div className="min-w-0">
                    <CardTitle className="truncate">
                      <Link href="/dashboard" onClick={() => setActiveProjectId(project.id)} className="inline-flex min-h-9 items-center hover:underline">
                        {project.name}
                      </Link>
                    </CardTitle>
                    <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                      {project.description ?? "Tanpa deskripsi"}
                    </p>
                  </div>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon" aria-label={`Aksi untuk ${project.name}`}>
                        <MoreHorizontal className="h-4 w-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onSelect={() => setActiveProjectId(project.id)}>
                        Jadikan proyek aktif
                      </DropdownMenuItem>
                      <DropdownMenuItem asChild>
                        <Link href="/settings">{t.settings.tabGeneral}</Link>
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        className="text-destructive-text focus:text-destructive-text"
                        disabled={!canMutate}
                        onSelect={() => {
                          if (canMutate) setDeleting(project);
                        }}
                      >
                        {t.common.delete}
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </CardHeader>
                <CardContent className="space-y-2 text-xs">
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">{t.projects.lastRun}</span>
                    {project.last_run_status ? (
                      <StatusBadge status={project.last_run_status} />
                    ) : (
                      <span className="text-muted-foreground">{t.projects.noRuns}</span>
                    )}
                  </div>
                  <div className="flex items-center gap-3 text-muted-foreground">
                    <span>
                      <span className="tabular font-medium text-foreground">{project.source_count}</span> {t.projects.sourceCount}
                    </span>
                    <span>
                      <span className="tabular font-medium text-foreground">{project.pipeline_count}</span> {t.projects.pipelineCount}
                    </span>
                    <span className="ml-auto">{formatRelative(project.updated_at)}</span>
                  </div>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      )}

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.projects.newTitle}</DialogTitle>
            <DialogDescription>{t.projects.newDescription}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="project-name">{t.common.name}</Label>
              <Input
                id="project-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={120}
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="project-description">{t.common.description}</Label>
              <Textarea
                id="project-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                maxLength={500}
                rows={3}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              {t.common.cancel}
            </Button>
            <Button
              onClick={() => createMutation.mutate()}
              disabled={name.trim() === "" || createMutation.isPending}
            >
              {createMutation.isPending ? "Membuat…" : t.common.create}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        title={t.projects.deleteTitle}
        description={t.projects.deleteWarning}
        resourceName={deleting?.name ?? ""}
        onConfirm={() => {
          if (deleting) deleteMutation.mutate({ id: deleting.id, confirmName: deleting.name });
        }}
        pending={deleteMutation.isPending}
      />
    </div>
  );
}




