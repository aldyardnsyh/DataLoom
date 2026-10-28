"use client";

import { useMutation } from "@tanstack/react-query";
import { BarChart3, HelpCircle, Loader2, Play, ShieldCheck, Table2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";

import { EmptyState } from "@/components/domain/empty-state";
import { ErrorState } from "@/components/domain/error-state";
import { SqlViewer } from "@/components/domain/sql-viewer";
import { DataTable } from "@/components/domain/data-table";
import { LoadingAnnouncer } from "@/components/domain/state-loading";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { Input } from "@/components/ui/input";

import { useProject } from "@/components/providers/project-provider";
import { askData } from "@/lib/agent-client";
import { classifyError } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n/id";
import type { SqlAnswer } from "@/lib/types";
import { cn } from "@/lib/utils";

const EXAMPLE_CHIPS = [
  t.ask.examples.revenue,
  t.ask.examples.topCustomers,
  t.ask.examples.rowsBySource,
];

const chartConfig: ChartConfig = {
  value: { label: "Nilai", color: "hsl(var(--primary))" },
};

/** Convert the first two columns of a result into chart data (numeric only). */
function toChartData(answer: SqlAnswer): { label: string; value: number }[] {
  if (answer.columns.length < 2) return [];
  const valueColumn = answer.columns[1];
  if (!valueColumn) return [];
  const valueIndex = answer.columns.findIndex((column) => column.name === valueColumn.name);
  const labelColumn = answer.columns[0];
  if (valueIndex <= 0 || !labelColumn) return [];

  return answer.rows
    .map((row) => {
      const label = row[0];
      const value = row[valueIndex];
      return {
        label: label === null || label === undefined ? "Belum ada" : String(label),
        value: typeof value === "number" ? value : Number(value),
      };
    })
    .filter((entry) => Number.isFinite(entry.value))
    .slice(0, 30);
}

/**
 * Ask Data / Chat-to-SQL (PRD 18.3 P9).
 * Variants: result, rejected, clarification needed, loading, empty, disabled.
 */
export default function AskDataPage() {
  const { activeProjectId } = useProject();

  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<SqlAnswer | null>(null);
  const [view, setView] = useState<"table" | "chart">("table");

  const askMutation = useMutation({
    mutationFn: (text: string) => askData(activeProjectId as string, text),
    onSuccess: (data) => {
      setAnswer(data.answer);
      setView("table");
    },
  });

  const submit = (text: string) => {
    const trimmed = text.trim();
    if (trimmed.length < 3) return;
    setQuestion(trimmed);
    setAnswer(null);
    askMutation.mutate(trimmed);
  };

  const rows = useMemo(() => {
    if (!answer) return [];
    return answer.rows.map((row) => {
      const record: Record<string, unknown> = {};
      answer.columns.forEach((column, index) => {
        record[column.name] = row[index];
      });
      return record;
    });
  }, [answer]);

  const chartData = useMemo(() => (answer ? toChartData(answer) : []), [answer]);

  const columns = useMemo(() => {
    if (!answer) return [];
    return answer.columns.map((column, index) => ({
      key: column.name,
      header: column.name,
      mono: column.dtype !== "string",
      accessor: (row: Record<string, unknown>) => {
        const value = row[column.name];
        return typeof value === "number" ? value : String(value ?? "");
      },
      cell: (row: Record<string, unknown>) => {
        const value = row[column.name];
        if (value === null || value === undefined) {
          return <span className="text-muted-foreground">NULL</span>;
        }
        return typeof value === "number" ? (
          <span className="tabular">{formatNumber(value)}</span>
        ) : (
          String(value)
        );
      },
      align: column.dtype === "integer" || column.dtype === "float" ? ("right" as const) : ("left" as const),
      _index: index,
    }));
  }, [answer]);

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

  return (
    <div className="page-container space-y-5">
      <header>
        <h1 className="text-xl font-bold tracking-tight">{t.ask.title}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t.ask.subtitle}</p>
      </header>

      {/* Question input */}
      <Card>
        <CardContent className="space-y-3 p-4">
          <label htmlFor="ask-input" className="text-sm font-medium">
            {t.ask.inputLabel}
          </label>
          <form
            className="flex flex-col gap-2 sm:flex-row"
            onSubmit={(event) => {
              event.preventDefault();
              submit(question);
            }}
          >
            <Input
              id="ask-input"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder={t.ask.inputPlaceholder}
              maxLength={2000}
              disabled={askMutation.isPending}
              aria-describedby="ask-hint"
            />
            <Button
              type="submit"
              disabled={question.trim().length < 3 || askMutation.isPending}
            >
              {askMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              {askMutation.isPending ? t.ask.generating : t.ask.submit}
            </Button>
          </form>
          <div className="flex flex-wrap gap-1.5" id="ask-hint">
            {EXAMPLE_CHIPS.map((chip) => (
              <button
                key={chip}
                type="button"
                onClick={() => {
                  setQuestion(chip);
                  submit(chip);
                }}
                disabled={askMutation.isPending}
                className="rounded-full border border-border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
              >
                {chip}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      {askMutation.isPending ? (
        <Card>
          <CardContent className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
            <LoadingAnnouncer label={t.ask.generating} />
            <Loader2 className="h-4 w-4 animate-spin" />
            {t.ask.generating}
          </CardContent>
        </Card>
      ) : null}

      {askMutation.isError ? (
        <ErrorState
          kind={classifyError(askMutation.error).kind}
          message={classifyError(askMutation.error).message}
          requestId={classifyError(askMutation.error).requestId}
          onRetry={() => submit(question)}
        />
      ) : null}

      {!answer && !askMutation.isPending && !askMutation.isError ? (
        <EmptyState
          icon={HelpCircle}
          title={t.ask.emptyTitle}
          description={t.ask.emptyBody}
        />
      ) : null}

      {answer ? (
        <>
          {answer.status === "rejected" ? (
            <Card className="border-destructive/30">
              <CardHeader className="flex flex-row items-center gap-2">
                <Badge variant="destructive" className="gap-1">
                  <ShieldCheck className="h-3 w-3" aria-hidden="true" />
                  {t.ask.rejectedTitle}
                </Badge>
                {answer.rejection_code ? (
                  <code className="font-mono text-[11px] text-muted-foreground">{answer.rejection_code}</code>
                ) : null}
              </CardHeader>
              <CardContent className="text-sm text-muted-foreground">
                <p className="font-medium text-foreground">{t.ask.rejectedBody}</p>
                {answer.explanation ? <p className="mt-1">{answer.explanation}</p> : null}
              </CardContent>
            </Card>
          ) : null}

          {answer.status === "needs_clarification" ? (
            <Card className="border-warning/30">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Badge variant="warning" className="gap-1">
                    <HelpCircle className="h-3 w-3" aria-hidden="true" />
                    {t.ask.clarifyTitle}
                  </Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <p className="text-muted-foreground">{t.ask.clarifyBody}</p>
                {answer.open_questions?.map((questionItem, index) => (
                  <p key={index} className="rounded-lg border border-border bg-muted px-3 py-2 text-foreground">
                    {questionItem}
                  </p>
                ))}
                <div className="flex flex-wrap gap-2 pt-1">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      const next = `${question}. ${answer.open_questions?.[0] ?? ""}`;
                      setQuestion(next);
                      submit(next);
                    }}
                  >
                    Tambahkan jawaban dan coba lagi
                  </Button>
                </div>
              </CardContent>
            </Card>
          ) : null}

          {answer.status === "ok" ? (
            <>
              <Card>
                <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
                  <CardTitle className="flex items-center gap-2">
                    {t.ask.sql}
                    <Badge variant="success" className="gap-1">
                      <ShieldCheck className="h-3 w-3" aria-hidden="true" />
                      {t.ask.safeBadge}
                    </Badge>
                  </CardTitle>
                  <div className="flex items-center gap-1">
                    <Button
                      variant={view === "table" ? "secondary" : "ghost"}
                      size="sm"
                      onClick={() => setView("table")}
                      aria-pressed={view === "table"}
                    >
                      <Table2 className="h-3.5 w-3.5" />
                      {t.ask.tableToggle}
                    </Button>
                    <Button
                      variant={view === "chart" ? "secondary" : "ghost"}
                      size="sm"
                      onClick={() => setView("chart")}
                      aria-pressed={view === "chart"}
                      disabled={chartData.length === 0}
                      title={chartData.length === 0 ? "Hasil tidak memiliki kolom numerik." : undefined}
                    >
                      <BarChart3 className="h-3.5 w-3.5" />
                      {t.ask.chartToggle}
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  <SqlViewer sql={answer.sql} />

                  {answer.assumptions.length > 0 ? (
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        {t.ask.assumptions}
                      </p>
                      <ul className="mt-1 list-inside list-disc text-xs text-muted-foreground">
                        {answer.assumptions.map((item, index) => (
                          <li key={index}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between gap-2">
                  <CardTitle>
                    {t.ask.result} <span className="tabular text-muted-foreground">({formatNumber(answer.row_count)})</span>
                  </CardTitle>
                  <p className="text-[11px] text-muted-foreground">{t.ask.rowLimit}</p>
                </CardHeader>
                <CardContent className="p-0">
                  {view === "table" ? (
                    <DataTable
                      columns={columns.map((column) => {
                        const { _index, ...rest } = column as typeof column & { _index?: number };
                        return rest;
                      })}
                      rows={rows}
                      rowKey={(row, index) => `${index}-${String(Object.values(row)[0] ?? index)}`}
                      height={360}
                      truncationNotice={answer.truncated ? t.explorer.truncatedBanner : undefined}
                      emptyMessage="Query berhasil tetapi tidak mengembalikan baris."
                    />
                  ) : (
                    <div className="p-4">
                      {/* The table showed the truncation banner but the chart
                          did not, so a clamped result looked complete here. */}
                      {answer.truncated ? (
                        <p
                          role="status"
                          className="mb-2 rounded-lg border border-warning/30 bg-warning/10 px-3 py-1.5 text-[11px] text-warning-text"
                        >
                          {t.explorer.truncatedBanner}
                        </p>
                      ) : null}
                      <ChartContainer config={chartConfig} className="h-[320px] w-full">
                        <BarChart data={chartData} margin={{ top: 8, right: 8, bottom: 8, left: 8 }}>
                          <CartesianGrid vertical={false} stroke="hsl(var(--border))" />
                          <XAxis dataKey="label" tickLine={false} axisLine={false} fontSize={11} />
                          <YAxis tickLine={false} axisLine={false} fontSize={11} width={56} />
                          <ChartTooltip content={<ChartTooltipContent />} />
                          <Bar dataKey="value" fill="var(--color-value)" radius={[4, 4, 0, 0]} />
                        </BarChart>
                      </ChartContainer>
                    </div>
                  )}
                </CardContent>
              </Card>

              {answer.explanation ? (
                <p className={cn("text-sm text-muted-foreground")}>{answer.explanation}</p>
              ) : null}
            </>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

