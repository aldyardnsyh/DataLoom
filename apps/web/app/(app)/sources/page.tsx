"use client";

import { useMutation } from "@tanstack/react-query";
import { CloudUpload, Database, FileText, Globe, Layers, Plus, RotateCw, ShieldAlert } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { notify } from "@/lib/notify";

import { uploadFile, validateFile } from "@/lib/upload";
import { Progress } from "@/components/ui/progress";

import { ConfirmDialog } from "@/components/domain/confirm-dialog";
import { EmptyState } from "@/components/domain/empty-state";
import { ErrorState } from "@/components/domain/error-state";
import { StatusBadge } from "@/components/domain/status-badge";
import { CardGridSkeleton, LoadingAnnouncer } from "@/components/domain/state-loading";
import { Badge } from "@/components/ui/badge";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useCanMutate } from "@/components/providers/session-provider";
import { useProject } from "@/components/providers/project-provider";
import { useQueryClient, useQuery } from "@tanstack/react-query";
import { api, classifyError } from "@/lib/api";
import { isDemoMode } from "@/lib/demo/mode";
import { copyToClipboard, formatRelative } from "@/lib/format";
import { t } from "@/lib/i18n/id";
import type { ApiKeyCreated, Source, SourceType } from "@/lib/types";

/**
 * Filter strip above the source list. Icons + counts are not decoration: a row
 * of bare words on a white page was not recognised as a control, so each tab
 * now carries a type icon and the number of sources it will show.
 */
const SOURCE_TABS: { value: "all" | SourceType; label: string; icon: typeof Globe }[] = [
  { value: "all", label: t.sources.tabAll, icon: Layers },
  { value: "webhook", label: t.sources.tabWebhook, icon: Globe },
  { value: "csv", label: t.sources.tabCsv, icon: CloudUpload },
  { value: "api", label: t.sources.tabApi, icon: FileText },
  { value: "postgres", label: t.sources.tabPostgres, icon: Database },
];

const SOURCE_TYPE_META: Record<SourceType, { label: string; icon: typeof Globe; hint: string }> = {
  webhook: { label: t.sources.tabWebhook, icon: Globe, hint: "Kirim JSON lewat HTTP POST" },
  csv: { label: t.sources.tabCsv, icon: CloudUpload, hint: "Unggah berkas CSV/Parquet" },
  api: { label: t.sources.tabApi, icon: FileText, hint: "Polling REST terjadwal" },
  postgres: { label: t.sources.tabPostgres, icon: Database, hint: "Koneksi read-only" },
};

/**
 * Sources page (PRD 18.3 P4).
 * States: loading, empty, error, key revoked banner, connection error banner,
 * and disabled mutations for viewers.
 */
export default function SourcesPage() {
  const { activeProjectId, activeProject } = useProject();
  const canMutate = useCanMutate();
  const queryClient = useQueryClient();

  const [tab, setTab] = useState<string>("all");
  const [addOpen, setAddOpen] = useState(false);
  const [selectedType, setSelectedType] = useState<SourceType>("webhook");
  const [name, setName] = useState("");
  const [createdKey, setCreatedKey] = useState<ApiKeyCreated | null>(null);
  const [revoking, setRevoking] = useState<{ source: Source; keyId: string } | null>(null);

  // CSV upload state (FR-013): target source, chosen file, live progress.
  const [uploading, setUploading] = useState<Source | null>(null);
  const [uploadFileState, setUploadFileState] = useState<File | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const uploadMutation = useMutation({
    mutationFn: async () => {
      if (!uploading || !uploadFileState) throw new Error("Berkas belum dipilih.");
      return uploadFile(activeProjectId as string, uploadFileState, setUploadProgress);
    },
    onSuccess: async () => {
      /*
       * Demo mode does not transmit bytes (there is no object storage to talk
       * to). Saying "terunggah" there would be a lie, so the wording changes
       * instead of the behaviour.
       */
      if (isDemoMode()) {
        notify.info(t.sources.uploadDemoSuccess, { description: t.notify.uploadDemo });
      } else {
        notify.success(t.sources.uploadSuccess, { description: t.notify.uploadCompleted });
      }
      // A new Bronze dataset must show up in the Data Explorer without a manual
      // refresh: the previous handler reset the dialog but left the cache stale.
      await queryClient.invalidateQueries({ queryKey: ["datasets"] });
      setUploading(null);
      setUploadFileState(null);
      setUploadProgress(null);
      setUploadError(null);
    },
    onError: (error: Error) => {
      setUploadError(error.message);
      setUploadProgress(null);
    },
  });

  const pickFile = (file: File | undefined) => {
    if (!file) return;
    setUploadError(validateFile(file));
    setUploadFileState(file);
  };

  const closeUpload = (open: boolean) => {
    if (!open) {
      if (uploadMutation.isPending) return; // never close under a live transfer
      setUploading(null);
      setUploadFileState(null);
      setUploadError(null);
      setUploadProgress(null);
    }
  };

  const sourcesQuery = useQuery({
    queryKey: ["sources", activeProjectId],
    queryFn: () => api.sources.list(activeProjectId as string),
    enabled: Boolean(activeProjectId),
  });

  const keysQuery = useQuery({
    queryKey: ["api-keys", activeProjectId],
    queryFn: () => api.keys.list(activeProjectId as string),
    enabled: Boolean(activeProjectId),
  });

  // Memoised: a fresh `[]` literal each render defeated the `filtered` memo.
  const sources = useMemo(() => sourcesQuery.data ?? [], [sourcesQuery.data]);
  const keys = useMemo(() => keysQuery.data ?? [], [keysQuery.data]);

  const filtered = useMemo(
    () => (tab === "all" ? sources : sources.filter((source) => source.type === tab)),
    [sources, tab],
  );

  const createMutation = useMutation({
    mutationFn: async () => {
      const config =
        selectedType === "webhook"
          ? { type: "webhook", max_rows_per_request: 1000, max_bytes_per_request: 262144 }
          : selectedType === "csv"
            ? { type: "csv", delimiter: ",", encoding: "utf-8", has_header: true, max_file_bytes: 524288000 }
            : selectedType === "api"
              ? {
                  type: "api",
                  url: "https://example.com/data.json",
                  method: "GET",
                  headers: {},
                  poll_interval_seconds: 3600,
                  timeout_seconds: 30,
                  max_response_bytes: 10485760,
                }
              : {
                  type: "postgres",
                  host: "db.example.com",
                  port: 5432,
                  database: "analytics",
                  username: "readonly",
                  password: "CHANGE_ME",
                  sslmode: "require",
                  schema_name: "public",
                  table: "orders",
                  poll_interval_seconds: 86400,
                };
      return api.sources.create(activeProjectId as string, { name: name.trim(), config });
    },
    onSuccess: async () => {
      notify.success(`Sumber "${name.trim()}" ditambahkan.`, {
        description: t.notify.sourceCreated,
      });
      setName("");
      setAddOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["sources", activeProjectId] });
    },
    onError: (error: Error) => notify.error(t.notify.failedTitle, { description: error.message }),
  });

  const createKeyMutation = useMutation({
    mutationFn: () => api.keys.create(activeProjectId as string, "Default key"),
    onSuccess: async (created) => {
      setCreatedKey(created);
      notify.success(t.sources.keyRotated, {
        description: `Prefix ${created.key.prefix}. ${t.notify.keyRotated}`,
      });
      await queryClient.invalidateQueries({ queryKey: ["api-keys", activeProjectId] });
    },
    onError: (error: Error) => notify.error(t.notify.failedTitle, { description: error.message }),
  });

  const rotateMutation = useMutation({
    mutationFn: (keyId: string) => api.keys.rotate(activeProjectId as string, keyId),
    onSuccess: async (created) => {
      setCreatedKey(created);
      notify.success(t.sources.keyRotated, {
        description: `Prefix ${created.key.prefix}. ${t.notify.keyRotated}`,
      });
      await queryClient.invalidateQueries({ queryKey: ["api-keys", activeProjectId] });
    },
    onError: (error: Error) => notify.error(t.notify.failedTitle, { description: error.message }),
  });

  const revokeMutation = useMutation({
    mutationFn: ({ keyId }: { keyId: string }) => api.keys.revoke(activeProjectId as string, keyId),
    onSuccess: async () => {
      notify.success(t.sources.keyRevoked, { description: t.notify.keyRevoked });
      setRevoking(null);
      await queryClient.invalidateQueries({ queryKey: ["api-keys", activeProjectId] });
    },
    onError: (error: Error) => notify.error(t.notify.failedTitle, { description: error.message }),
  });

  const copy = async (value: string) => {
    const ok = await copyToClipboard(value);
    if (ok) {
      notify.success(t.common.copied, {
        description: "Nilai disalin ke clipboard dan siap ditempel.",
        duration: 3_000,
      });
    } else {
      notify.error("Gagal menyalin", {
        description: "Browser menolak akses clipboard. Salin nilainya secara manual.",
      });
    }
  };

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

  if (sourcesQuery.isLoading) {
    return (
      <div className="page-container space-y-6">
        <LoadingAnnouncer />
        <CardGridSkeleton count={4} />
      </div>
    );
  }

  if (sourcesQuery.isError) {
    const kind = classifyError(sourcesQuery.error).kind;
    return (
      <div className="page-container">
        <ErrorState
          kind={kind}
          message={classifyError(sourcesQuery.error).message}
          requestId={classifyError(sourcesQuery.error).requestId}
          onRetry={() => void sourcesQuery.refetch()}
        />
      </div>
    );
  }

  const activeKey = keys.find((key) => key.revoked_at === null) ?? null;

  return (
    <div className="page-container space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight">{t.sources.title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {t.sources.subtitle} · <span className="font-medium text-foreground">{activeProject?.name}</span>
          </p>
        </div>
        <Button
          onClick={() => setAddOpen(true)}
          disabled={!canMutate}
          title={canMutate ? undefined : t.state.disabledTooltip}
        >
          <Plus className="h-4 w-4" />
          {t.sources.add}
        </Button>
      </header>

      {/* Revoked key banner: explicit state required by PRD 12.3. */}
      {keys.some((key) => key.revoked_at !== null) && activeKey === null ? (
        <div role="status" className="flex items-start gap-2 rounded-card border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning-text">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <span>{t.sources.revokedBanner}</span>
        </div>
      ) : null}

      {/* Connection error banner when any source is in error state. */}
      {sources.some((source) => source.status === "error") ? (
        <div role="status" className="flex items-start gap-2 rounded-card border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive-text">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <span>{t.sources.errorBanner}</span>
        </div>
      ) : null}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList aria-label={t.sources.filterLabel}>
          {SOURCE_TABS.map((item) => {
            const Icon = item.icon;
            const count =
              item.value === "all"
                ? sources.length
                : sources.filter((source) => source.type === item.value).length;
            return (
              <TabsTrigger key={item.value} value={item.value}>
                <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                {item.label}
                <span
                  className="tabular rounded bg-foreground/10 px-1 text-[10px] font-semibold"
                  aria-hidden="true"
                >
                  {count}
                </span>
              </TabsTrigger>
            );
          })}
        </TabsList>

        <TabsContent value={tab}>
          {filtered.length === 0 ? (
            <EmptyState
              icon={Globe}
              title={t.sources.emptyTitle}
              description={t.sources.emptyBody}
              primaryAction={{
                label: t.sources.add,
                onClick: () => setAddOpen(true),
                disabled: !canMutate,
                disabledReason: t.state.disabledTooltip,
              }}
            />
          ) : (
            <ul className="grid min-w-0 gap-4 lg:grid-cols-2">
              {filtered.map((source) => {
                const meta = SOURCE_TYPE_META[source.type];
                const Icon = meta.icon;
                return (
                  <li key={source.id}>
                    <Card>
                      <CardHeader className="flex flex-row items-start justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className="flex h-8 w-8 items-center justify-center rounded-md bg-accent text-accent-foreground">
                            <Icon className="h-4 w-4" aria-hidden="true" />
                          </span>
                          <div>
                            <CardTitle className="truncate">{source.name}</CardTitle>
                            <p className="text-xs text-muted-foreground">{meta.label}</p>
                          </div>
                        </div>
                        <StatusBadge status={source.status} />
                      </CardHeader>
                      <CardContent className="space-y-3 text-xs">
                        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-muted-foreground">
                          <span>
                            {t.sources.colLastData}:{" "}
                            <span className="text-foreground">{formatRelative(source.last_received_at)}</span>
                          </span>
                          <span className="font-mono">{source.slug}</span>
                        </div>

                        {source.type === "webhook" || source.type === "api" ? (
                          <div className="space-y-2 rounded-lg border border-border bg-muted p-2.5">
                            <p className="font-medium text-foreground">{t.sources.endpoint}</p>
                            <div className="flex items-center gap-2">
                              <code className="flex-1 truncate font-mono text-[11px] text-foreground">
                                POST /v1/ingest/{source.id}
                              </code>
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => copy(`/v1/ingest/${source.id}`)}
                              >
                                {t.sources.copyEndpoint}
                              </Button>
                            </div>
                            <p className="text-[11px] text-muted-foreground">{t.sources.endpointHint}</p>

                            <div className="flex flex-wrap items-center gap-2 pt-1">
                              <span className="text-[11px] text-muted-foreground">{t.sources.apiKeyPrefix}</span>
                              <Badge variant="outline" className="font-mono">
                                {activeKey ? `dl_${activeKey.prefix}…` : "Belum ada"}
                              </Badge>
                              <Button
                                variant="outline"
                                size="sm"
                                disabled={!canMutate || !activeKey || rotateMutation.isPending}
                                title={canMutate ? undefined : t.state.disabledTooltip}
                                onClick={() => activeKey && rotateMutation.mutate(activeKey.id)}
                              >
                                <RotateCw className="h-3.5 w-3.5" />
                                {t.sources.rotateKey}
                              </Button>
                              <Button
                                variant="ghost"
                                size="sm"
                                className="text-destructive-text hover:text-destructive-text"
                                disabled={!canMutate || !activeKey}
                                title={canMutate ? undefined : t.state.disabledTooltip}
                                onClick={() => {
                                  if (activeKey) setRevoking({ source, keyId: activeKey.id });
                                }}
                              >
                                {t.sources.revokeKey}
                              </Button>
                            </div>
                          </div>
                        ) : null}

                        {source.type === "csv" ? (
                          <div className="space-y-2">
                            <div className="rounded-lg border border-dashed border-border bg-muted p-4 text-center">
                              <CloudUpload className="mx-auto h-5 w-5 text-muted-foreground" aria-hidden="true" />
                              <p className="mt-2 font-medium text-foreground">{t.sources.uploadTitle}</p>
                              <p className="text-[11px] text-muted-foreground">{t.sources.uploadHint}</p>
                              <Button
                                variant="outline"
                                size="sm"
                                className="mt-2"
                                disabled={!canMutate}
                                title={canMutate ? undefined : t.state.disabledTooltip}
                                onClick={() => setUploading(source)}
                              >
                                {t.sources.uploadDrop}
                              </Button>
                            </div>
                            {uploadProgress !== null ? (
                              <div role="status">
                                <Progress value={uploadProgress * 100} aria-label="Progres unggah" />
                                <p className="mt-1 text-[11px] text-muted-foreground">
                                  Mengunggah: {Math.round(uploadProgress * 100)}%
                                </p>
                              </div>
                            ) : null}
                          </div>
                        ) : null}

                        {source.type === "postgres" || source.type === "api" ? (
                          <p className="text-[11px] text-muted-foreground">
                            Kredensial tersimpan terenkripsi dan tidak pernah dikirim ke browser.
                          </p>
                        ) : null}
                      </CardContent>
                    </Card>
                  </li>
                );
              })}
            </ul>
          )}
        </TabsContent>
      </Tabs>

      {/* Add source drawer with 4 type cards (PRD P4). */}
      <Sheet open={addOpen} onOpenChange={setAddOpen}>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>{t.sources.addTitle}</SheetTitle>
            <SheetDescription>{t.sources.subtitle}</SheetDescription>
          </SheetHeader>

          <div className="mt-4 space-y-3">
            <div className="grid grid-cols-2 gap-2">
              {(Object.keys(SOURCE_TYPE_META) as SourceType[]).map((type) => {
                const meta = SOURCE_TYPE_META[type];
                const Icon = meta.icon;
                const selected = selectedType === type;
                return (
                  <button
                    key={type}
                    type="button"
                    onClick={() => setSelectedType(type)}
                    aria-pressed={selected}
                    className={`rounded-lg border p-3 text-left transition-colors focus-visible:ring-2 focus-visible:ring-ring ${
                      selected ? "border-primary bg-accent" : "border-border hover:bg-muted"
                    }`}
                  >
                    <Icon className="h-4 w-4 text-primary" aria-hidden="true" />
                    <p className="mt-1.5 text-sm font-medium text-foreground">{meta.label}</p>
                    <p className="text-[11px] text-muted-foreground">{meta.hint}</p>
                  </button>
                );
              })}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="source-name">{t.common.name}</Label>
              <Input
                id="source-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={120}
              />
            </div>

            <p className="text-[11px] text-muted-foreground">
              Konfigurasi detail dapat dilengkapi setelah sumber dibuat.
            </p>
          </div>

          <SheetFooter className="mt-6">
            <Button variant="outline" onClick={() => setAddOpen(false)}>
              {t.common.cancel}
            </Button>
            <Button
              onClick={() => createMutation.mutate()}
              disabled={name.trim() === "" || createMutation.isPending || !canMutate}
            >
              {createMutation.isPending ? "Membuat…" : t.common.create}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      {/* One-time plaintext API key reveal (FR-004). */}
      <Sheet open={createdKey !== null} onOpenChange={(open) => !open && setCreatedKey(null)}>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>API key baru</SheetTitle>
            <SheetDescription>{createdKey?.warning}</SheetDescription>
          </SheetHeader>
          <div className="mt-4 space-y-2">
            <code className="block break-all rounded-lg border border-border bg-muted p-3 font-mono text-xs">
              {createdKey?.plaintext_key}
            </code>
            <Button variant="outline" size="sm" onClick={() => createdKey && copy(createdKey.plaintext_key)}>
              {t.common.copy}
            </Button>
          </div>
        </SheetContent>
      </Sheet>

      {/* CSV upload (FR-013): presigned two-step, bytes never touch the API. */}
      <Dialog open={uploading !== null} onOpenChange={closeUpload}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.sources.uploadTitle}</DialogTitle>
            <DialogDescription>
              Berkas akan masuk ke sumber <span className="font-medium text-foreground">{uploading?.name}</span> sebagai
              data Bronze. Maksimum 500 MB, format .csv atau .parquet.
            </DialogDescription>
          </DialogHeader>

          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,.parquet,text/csv,application/vnd.apache.parquet"
            className="sr-only"
            aria-label="Pilih berkas"
            onChange={(event) => pickFile(event.target.files?.[0])}
          />

          <div className="space-y-3">
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploadMutation.isPending}
              className="flex w-full flex-col items-center justify-center rounded-lg border border-dashed border-border bg-muted px-4 py-8 text-center transition-colors hover:bg-secondary disabled:opacity-60"
            >
              <CloudUpload className="h-6 w-6 text-muted-foreground" aria-hidden="true" />
              {uploadFileState ? (
                <>
                  <p className="mt-2 max-w-full truncate text-sm font-medium text-foreground">
                    {uploadFileState.name}
                  </p>
                  <p className="text-[11px] text-muted-foreground">
                    {Math.max(1, Math.round(uploadFileState.size / 1024))} KB · klik untuk mengganti
                  </p>
                </>
              ) : (
                <>
                  <p className="mt-2 text-sm font-medium text-foreground">{t.sources.uploadDrop}</p>
                  <p className="text-[11px] text-muted-foreground">{t.sources.uploadHint}</p>
                </>
              )}
            </button>

            {uploadError ? (
              <p role="alert" className="text-xs font-medium text-destructive-text">
                {uploadError}
              </p>
            ) : null}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => closeUpload(false)} disabled={uploadMutation.isPending}>
              {t.common.cancel}
            </Button>
            <Button
              onClick={() => uploadMutation.mutate()}
              disabled={!uploadFileState || uploadError !== null || uploadMutation.isPending || !canMutate}
            >
              {uploadMutation.isPending ? "Mengunggah…" : "Unggah"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={revoking !== null}
        onOpenChange={(open) => !open && setRevoking(null)}
        title={t.sources.revokeKey}
        description="Key yang dicabut langsung ditolak (401). Kiriman baru akan gagal sampai key baru dibuat."
        resourceName={revoking?.source.name ?? ""}
        confirmLabel={t.sources.revokeKey}
        onConfirm={() => revoking && revokeMutation.mutate({ keyId: revoking.keyId })}
        pending={revokeMutation.isPending}
      />
    </div>
  );
}






