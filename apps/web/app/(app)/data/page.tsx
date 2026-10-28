"use client";

import { useQuery } from "@tanstack/react-query";
import { Database, FileCheck2, Table2 } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { DataTable } from "@/components/domain/data-table";
import { EmptyState } from "@/components/domain/empty-state";
import { ErrorState } from "@/components/domain/error-state";
import { LoadingAnnouncer } from "@/components/domain/state-loading";
import { LineageGraphView } from "@/components/domain/lineage-graph";
import { QuarantineTab } from "@/components/domain/quarantine-tab";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useProject } from "@/components/providers/project-provider";
import { api, classifyError } from "@/lib/api";
import { formatNumber, formatPercent, formatRelative } from "@/lib/format";
import { LAYER_LABEL } from "@/lib/status";
import { t } from "@/lib/i18n/id";
import type { Dataset } from "@/lib/types";
import { cn } from "@/lib/utils";

const LAYERS = ["bronze", "silver", "gold", "quarantine"] as const;

/**
 * Data Explorer (PRD 18.3 P7).
 * Left tree grouped by layer; main area with Preview / Schema / Quality / Lineage.
 * Preview is capped at 1.000 rows by contract, surfaced as a banner.
 */
export default function DataExplorerPage() {
  const { activeProjectId } = useProject();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<string>("preview");

  const datasetsQuery = useQuery({
    queryKey: ["datasets", activeProjectId],
    queryFn: () => api.datasets.list(activeProjectId as string),
    enabled: Boolean(activeProjectId),
  });

  // Memoised: `?? []` allocates a NEW array on every render, which made the
  // `grouped` memo (and anything depending on it) recompute constantly.
  const datasets = useMemo(() => datasetsQuery.data ?? [], [datasetsQuery.data]);
  const grouped = useMemo(() => {
    const map: Record<string, Dataset[]> = {};
    for (const dataset of datasets) {
      map[dataset.layer] = map[dataset.layer] ?? [];
      map[dataset.layer]!.push(dataset);
    }
    return map;
  }, [datasets]);

  const selected = datasets.find((dataset) => dataset.id === selectedId) ?? datasets[0] ?? null;

  const previewQuery = useQuery({
    queryKey: ["dataset-preview", activeProjectId, selected?.id],
    queryFn: () => api.datasets.preview(activeProjectId as string, selected!.id, 1000),
    enabled: Boolean(activeProjectId && selected?.id),
  });

  const qualityQuery = useQuery({
    queryKey: ["dataset-quality", activeProjectId, selected?.id],
    queryFn: () => api.datasets.quality(activeProjectId as string, selected!.id),
    enabled: Boolean(activeProjectId && selected?.id),
  });

  const lineageQuery = useQuery({
    queryKey: ["lineage", activeProjectId],
    queryFn: () => api.pipelines.lineage(activeProjectId as string),
    enabled: Boolean(activeProjectId) && tab === "lineage",
  });

  // The threshold shown next to a run's quarantine ratio is the project's
  // actual gate setting, not a number copied into the component.
  const settingsQuery = useQuery({
    queryKey: ["project-settings", activeProjectId],
    queryFn: () => api.projects.settings(activeProjectId as string),
    enabled: Boolean(activeProjectId),
  });

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
        <div className="grid min-w-0 gap-4 lg:grid-cols-[220px_minmax(0,1fr)]">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
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

  if (datasets.length === 0) {
    return (
      <div className="page-container">
        <EmptyState
          icon={Table2}
          title={t.explorer.emptyTitle}
          description={t.explorer.emptyBody}
          primaryAction={{ label: t.sources.title, href: "/sources" }}
        />
      </div>
    );
  }

  const preview = previewQuery.data;

  return (
    <div className="page-container space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-bold tracking-tight">{t.explorer.title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t.explorer.subtitle}</p>
        </div>
        <Button asChild variant="outline" size="sm">
          <Link href="/data-contract">
            <FileCheck2 className="h-4 w-4" aria-hidden="true" />
            {t.nav.contract}
          </Link>
        </Button>
      </header>

      <div className="grid min-w-0 gap-4 lg:grid-cols-[230px_minmax(0,1fr)]">
        {/* Left tree */}
        <nav aria-label={t.explorer.selectDataset} className="space-y-3">
          {LAYERS.map((layer) => {
            const items = grouped[layer] ?? [];
            return (
              <div key={layer}>
                <p className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  <Badge variant="outline" className="px-1.5 py-0 text-[10px]">
                    {LAYER_LABEL[layer]}
                  </Badge>
                  {items.length}
                </p>
                <ul className="space-y-0.5">
                  {items.length === 0 ? (
                    <li className="px-1 py-0.5 text-[11px] text-muted-foreground">(kosong)</li>
                  ) : (
                    items.map((dataset) => (
                      <li key={dataset.id}>
                        <button
                          type="button"
                          onClick={() => {
                            setSelectedId(dataset.id);
                            // A rejected row is an operator's first question;
                            // opening it should land on the ledger, not on a
                            // preview that hides the reason code.
                            if (dataset.layer === "quarantine") setTab("quarantine");
                          }}
                          aria-current={selected?.id === dataset.id ? "true" : undefined}
                          className={cn(
                            "flex w-full items-center gap-1.5 truncate rounded-md px-2 py-1 text-left text-xs",
                            selected?.id === dataset.id
                              ? "bg-accent font-medium text-accent-foreground"
                              : "text-muted-foreground hover:bg-muted hover:text-foreground",
                          )}
                        >
                          <Database className="h-3 w-3 shrink-0" aria-hidden="true" />
                          <span className="truncate">{dataset.name}</span>
                        </button>
                      </li>
                    ))
                  )}
                </ul>
              </div>
            );
          })}
        </nav>

        {/* Main */}
        <div className="min-w-0 space-y-4">
          {selected ? (
            <>
              <Card>
                <CardHeader>
                  <CardTitle className="flex flex-wrap items-center gap-2">
                    {selected.name}
                    <Badge variant="outline">{LAYER_LABEL[selected.layer] ?? selected.layer}</Badge>
                  </CardTitle>
                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    <span>
                      {t.explorer.rowCount}:{" "}
                      <span className="tabular text-foreground">
                        {selected.row_count === null ? "Belum ada" : formatNumber(selected.row_count)}
                      </span>
                    </span>
                    <span>
                      {t.explorer.lastUpdated}: <span className="text-foreground">{formatRelative(selected.updated_at)}</span>
                    </span>
                  </div>
                </CardHeader>
                <CardContent>
                  {/* Data Explorer is read-only. Export is intentionally disabled      */}
                  {/* until a real export endpoint exists (no fake action).        */}
                  <div className="mb-3 flex justify-end">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled
                      title="Ekspor belum tersedia pada MVP."
                    >
                      Ekspor
                    </Button>
                  </div>

                  <Tabs value={tab} onValueChange={setTab}>
                    <TabsList>
                      <TabsTrigger value="preview">{t.explorer.tabPreview}</TabsTrigger>
                      <TabsTrigger value="schema">{t.explorer.tabSchema}</TabsTrigger>
                      <TabsTrigger value="quality">{t.explorer.tabQuality}</TabsTrigger>
                      <TabsTrigger value="quarantine">{t.explorer.tabQuarantine}</TabsTrigger>
                      <TabsTrigger value="lineage">{t.explorer.tabLineage}</TabsTrigger>
                    </TabsList>

                    <TabsContent value="preview">
                      {previewQuery.isLoading ? (
                        <Skeleton className="h-64 w-full" />
                      ) : previewQuery.isError ? (
                        <ErrorState
                          compact
                          message={
                            previewQuery.error instanceof Error
                              ? previewQuery.error.message
                              : t.errors.generic
                          }
                          onRetry={() => void previewQuery.refetch()}
                        />
                      ) : preview && preview.rows.length > 0 ? (
                        <DataTable
                          label={`Pratinjau ${selected?.name ?? "dataset"}`}
                          columns={preview.columns.map((column, columnIndex) => ({
                            key: column.name,
                            header: column.name,
                            // Positional rows (contract): the cell value is at
                            // the same index as its column definition.
                            cell: (row: (string | number | boolean | null)[]) => {
                              const value = row[columnIndex];
                              return value === null || value === undefined ? "" : String(value);
                            },
                          }))}
                          rows={preview.rows}
                          rowKey={(_, index) => String(index)}
                          height={420}
                          truncationNotice={preview.truncated ? t.explorer.truncatedBanner : undefined}
                          emptyMessage={t.explorer.emptyDataset}
                        />
                      ) : (
                        <EmptyState
                          compact
                          title={t.explorer.emptyDataset}
                          description="Pratinjau baris hanya tersedia untuk dataset gold pada warehouse. Dataset parquet menampilkan skema dan jumlah baris."
                        />
                      )}
                    </TabsContent>

                    <TabsContent value="schema">
                      {preview && preview.columns.length > 0 ? (
                        <table className="w-full border-collapse text-sm">
                          <thead>
                            <tr className="bg-muted text-left text-xs uppercase text-muted-foreground">
                              <th className="px-2 py-1.5">Kolom</th>
                              <th className="px-2 py-1.5">Tipe</th>
                            </tr>
                          </thead>
                          <tbody>
                            {preview.columns.map((column) => (
                              <tr key={column.name} className="border-t border-border">
                                <td className="px-2 py-1.5 font-mono text-xs">{column.name}</td>
                                <td className="px-2 py-1.5">
                                  <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]">
                                    {column.dtype}
                                  </code>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      ) : (
                        <p className="py-6 text-center text-sm text-muted-foreground">
                          Skema belum tersedia untuk dataset ini.
                        </p>
                      )}
                    </TabsContent>

                    <TabsContent value="quality">
                      {qualityQuery.isLoading ? (
                        <Skeleton className="h-40 w-full" />
                      ) : (qualityQuery.data?.columns ?? []).length === 0 ? (
                        <p className="py-6 text-center text-sm text-muted-foreground">
                          Statistik kualitas belum dihitung untuk dataset ini.
                        </p>
                      ) : (
                        <ul className="space-y-2">
                          {(qualityQuery.data?.columns ?? []).map((column) => (
                            <li key={column.name} className="rounded-lg border border-border p-2.5 text-xs">
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <span className="font-mono font-medium text-foreground">{column.name}</span>
                                <span className="text-muted-foreground">{column.dtype}</span>
                              </div>
                              <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground">
                                <span>
                                  {t.explorer.nullPercent}:{" "}
                                  <span className="tabular text-foreground">{formatPercent(column.null_ratio)}</span>
                                </span>
                                <span>
                                  {t.explorer.distinctCount}:{" "}
                                  <span className="tabular text-foreground">{formatNumber(column.distinct_count)}</span>
                                </span>
                              </div>
                            </li>
                          ))}
                        </ul>
                      )}
                    </TabsContent>

                    <TabsContent value="quarantine">
                      <QuarantineTab
                        projectId={activeProjectId}
                        datasets={datasets}
                        selected={selected}
                        threshold={settingsQuery.data?.max_quarantine_ratio ?? null}
                        onSelectDataset={(datasetId) => {
                          setSelectedId(datasetId);
                          setTab("quarantine");
                        }}
                      />
                    </TabsContent>

                    <TabsContent value="lineage">
                      {lineageQuery.isLoading ? (
                        <Skeleton className="h-48 w-full" />
                      ) : lineageQuery.data ? (
                        <LineageGraphView graph={lineageQuery.data} />
                      ) : (
                        <p className="py-6 text-center text-sm text-muted-foreground">
                          Lineage belum tersedia.
                        </p>
                      )}
                    </TabsContent>
                  </Tabs>
                </CardContent>
              </Card>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}




