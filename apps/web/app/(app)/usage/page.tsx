"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, DollarSign, Hash, Timer } from "lucide-react";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, XAxis, YAxis } from "recharts";
import { notify } from "@/lib/notify";

import { BudgetMeter } from "@/components/domain/budget-meter";
import { EmptyState } from "@/components/domain/empty-state";
import { ErrorState } from "@/components/domain/error-state";
import { StatusBadge } from "@/components/domain/status-badge";
import { LoadingAnnouncer, TableSkeleton } from "@/components/domain/state-loading";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useProject } from "@/components/providers/project-provider";
import { useRole } from "@/components/providers/session-provider";
import { api, classifyError } from "@/lib/api";
import { formatDateTime, formatDuration, formatNumber, formatUsd } from "@/lib/format";
import { t } from "@/lib/i18n/id";

const COST_CHART: ChartConfig = { cost_usd: { label: "Biaya (USD)", color: "hsl(var(--primary))" } };
// Theme tokens, so the palette follows the product instead of Tailwind defaults.
const PIE_COLORS = [
  "hsl(var(--primary))",
  "hsl(var(--info))",
  "hsl(var(--success))",
  "hsl(var(--warning))",
  "hsl(var(--destructive))",
  "hsl(var(--muted-foreground))",
];

/**
 * Usage & Cost (PRD 18.3 P10).
 * Every figure is an aggregate the API computed from `llm_calls` (FR-036).
 * Zero-data states are explicit; latency shows "-" instead of 0.
 */
export default function UsagePage() {
  const { activeProjectId } = useProject();
  const role = useRole();
  // Budget is an organisation-level setting: only owner/admin may change it.
  const canSetBudget = role === "owner" || role === "admin";
  const queryClient = useQueryClient();

  const summaryQuery = useQuery({
    queryKey: ["usage", activeProjectId],
    queryFn: () => api.usage.summary(activeProjectId ?? undefined),
    enabled: Boolean(activeProjectId),
  });

  const callsQuery = useQuery({
    queryKey: ["llm-calls", activeProjectId],
    queryFn: () => api.usage.calls(activeProjectId ?? undefined),
    enabled: Boolean(activeProjectId),
  });

  const summary = summaryQuery.data;

  const [budget, setBudget] = useState<string>("");
  const [tokenLimit, setTokenLimit] = useState<string>("");

  /*
   * Parse once, validate once. `Number("abc")` is NaN and JSON.stringify(NaN)
   * is `null`, so an unguarded value reached the API as null (422); a negative
   * number was sent verbatim even though the contract forbids it.
   */
  const budgetValue = budget.trim() === "" ? (summary?.budget.monthly_budget_usd ?? 0) : Number(budget);
  const tokenValue = tokenLimit.trim() === "" ? (summary?.budget.per_run_token_limit ?? 20000) : Number(tokenLimit);
  const budgetInvalid =
    !Number.isFinite(budgetValue) || budgetValue < 0 || !Number.isFinite(tokenValue) || tokenValue < 1;

  const budgetMutation = useMutation({
    mutationFn: () =>
      api.usage.updateBudget({
        monthly_budget_usd: budgetValue,
        per_run_token_limit: Math.round(tokenValue),
      }),
    onSuccess: async () => {
      notify.success(t.usage.budgetSaved, { description: t.notify.budgetSaved });
      await queryClient.invalidateQueries({ queryKey: ["usage", activeProjectId] });
    },
    onError: (error: Error) => notify.error(t.notify.failedTitle, { description: error.message }),
  });

  if (!activeProjectId) {
    return (
      <div className="page-container">
        <EmptyState title={t.dashboard.noProjectsTitle} description={t.dashboard.noProjectsBody} />
      </div>
    );
  }

  if (summaryQuery.isLoading) {
    return (
      <div className="page-container space-y-6">
        <LoadingAnnouncer />
        <TableSkeleton />
      </div>
    );
  }

  if (summaryQuery.isError || !summary) {
    const kind = summaryQuery.isError ? classifyError(summaryQuery.error).kind : "generic";
    return (
      <div className="page-container">
        <ErrorState
          kind={kind}
          message={summaryQuery.isError ? classifyError(summaryQuery.error).message : undefined}
          requestId={summaryQuery.isError ? classifyError(summaryQuery.error).requestId : undefined}
          onRetry={() => void summaryQuery.refetch()}
        />
      </div>
    );
  }

  const dailyData = summary.daily.map((entry) => ({
    day: entry.day,
    cost_usd: Number(entry.cost_usd.toFixed(4)),
    tokens: entry.tokens,
  }));

  const modelData = summary.by_model.map((entry, index) => ({
    name: entry.model,
    value: entry.tokens_in + entry.tokens_out,
    fill: PIE_COLORS[index % PIE_COLORS.length],
  }));

  const totalTokens = summary.total_tokens_in + summary.total_tokens_out;

  return (
    <div className="page-container space-y-5">
      <header>
        <h1 className="text-xl font-bold tracking-tight">{t.usage.title}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t.usage.subtitle}</p>
      </header>

      {summary.total_calls === 0 ? (
        <EmptyState
          icon={Activity}
          title={t.usage.emptyTitle}
          description={t.usage.emptyBody}
          primaryAction={{ label: t.agents.startProfile, href: "/agents" }}
        />
      ) : null}

      <section className="grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Card>
          <CardContent className="p-4">
            <p className="flex items-center gap-1 text-xs text-muted-foreground">
              <Hash className="h-3.5 w-3.5" aria-hidden="true" /> {t.usage.tokensMonth}
            </p>
            <p className="mt-1 text-xl font-bold tabular">{formatNumber(totalTokens)}</p>
            <p className="text-[11px] text-muted-foreground">
              masuk {formatNumber(summary.total_tokens_in)} · keluar {formatNumber(summary.total_tokens_out)}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <p className="flex items-center gap-1 text-xs text-muted-foreground">
              <DollarSign className="h-3.5 w-3.5" aria-hidden="true" /> {t.usage.costMonth}
            </p>
            <p className="mt-1 text-xl font-bold tabular">{formatUsd(summary.total_cost_usd)}</p>
            <div className="mt-2">
              <BudgetMeter used={summary.budget.spent_usd} budget={summary.budget.monthly_budget_usd} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <p className="flex items-center gap-1 text-xs text-muted-foreground">
              <Activity className="h-3.5 w-3.5" aria-hidden="true" /> {t.usage.calls}
            </p>
            <p className="mt-1 text-xl font-bold tabular">{formatNumber(summary.total_calls)}</p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <p className="flex items-center gap-1 text-xs text-muted-foreground">
              <Timer className="h-3.5 w-3.5" aria-hidden="true" /> {t.usage.avgLatency}
            </p>
            <p className="mt-1 text-xl font-bold tabular">
              {summary.average_latency_ms === null ? "Belum ada" : formatDuration(summary.average_latency_ms)}
            </p>
            {summary.average_latency_ms === null ? (
              <p className="text-[11px] text-muted-foreground">Belum ada panggilan.</p>
            ) : null}
          </CardContent>
        </Card>
      </section>

      <section className="grid min-w-0 gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>{t.usage.costPerAgent}</CardTitle>
          </CardHeader>
          <CardContent>
            {dailyData.length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">Belum ada data biaya.</p>
            ) : (
              <ChartContainer config={COST_CHART} className="h-[260px] w-full">
                <BarChart data={dailyData}>
                  <CartesianGrid vertical={false} stroke="hsl(var(--border))" />
                  <XAxis dataKey="day" tickLine={false} axisLine={false} fontSize={11} />
                  <YAxis tickLine={false} axisLine={false} fontSize={11} width={64} />
                  <ChartTooltip content={<ChartTooltipContent />} />
                  <Bar dataKey="cost_usd" fill="var(--color-cost_usd)" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ChartContainer>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t.usage.tokensPerModel}</CardTitle>
          </CardHeader>
          <CardContent>
            {modelData.length === 0 ? (
              <p className="py-10 text-center text-sm text-muted-foreground">Belum ada data model.</p>
            ) : (
              <>
                <ChartContainer config={{}} className="mx-auto h-[200px] w-full">
                  <PieChart>
                    <Pie data={modelData} dataKey="value" nameKey="name" innerRadius={48} outerRadius={80}>
                      {modelData.map((entry) => (
                        <Cell key={entry.name} fill={entry.fill} />
                      ))}
                    </Pie>
                    <ChartTooltip content={<ChartTooltipContent />} />
                  </PieChart>
                </ChartContainer>
                <ul className="mt-2 space-y-1 text-xs">
                  {summary.by_model.map((entry, index) => (
                    <li key={entry.model} className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-1.5 truncate">
                        <span
                          className="h-2 w-2 shrink-0 rounded-full"
                          style={{ backgroundColor: PIE_COLORS[index % PIE_COLORS.length] }}
                          aria-hidden="true"
                        />
                        <span className="truncate font-mono">{entry.model}</span>
                      </span>
                      <span className="tabular text-muted-foreground">
                        {formatNumber(entry.tokens_in + entry.tokens_out)}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <CardTitle>{t.usage.recentCalls}</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {(callsQuery.data ?? []).length === 0 ? (
            <p className="px-4 py-10 text-center text-sm text-muted-foreground">{t.usage.emptyTitle}</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t.usage.colTime}</TableHead>
                  <TableHead>{t.usage.colAgent}</TableHead>
                  <TableHead>{t.usage.colNode}</TableHead>
                  <TableHead>{t.usage.colModel}</TableHead>
                  <TableHead className="text-right">{t.usage.colTokens}</TableHead>
                  <TableHead className="text-right">{t.usage.colLatency}</TableHead>
                  <TableHead className="text-right">{t.usage.colCost}</TableHead>
                  <TableHead>{t.common.status}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(callsQuery.data ?? []).slice(0, 50).map((call) => (
                  <TableRow key={call.id}>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {formatDateTime(call.at)}
                    </TableCell>
                    <TableCell className="text-xs">{call.agent_type ?? "Belum ada"}</TableCell>
                    <TableCell className="font-mono text-xs">{call.node}</TableCell>
                    <TableCell className="font-mono text-xs">{call.model}</TableCell>
                    <TableCell className="text-right tabular text-xs">
                      {formatNumber(call.tokens_in)}/{formatNumber(call.tokens_out)}
                    </TableCell>
                    <TableCell className="text-right tabular text-xs">{formatDuration(call.latency_ms)}</TableCell>
                    <TableCell className="text-right tabular text-xs">{formatUsd(call.cost_usd)}</TableCell>
                    <TableCell>
                      <StatusBadge status={call.error ? "failed" : "succeeded"} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t.usage.budget}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <Label htmlFor="budget-input">{t.usage.budget} (USD)</Label>
            <Input
              id="budget-input"
              type="number"
              min={0}
              aria-invalid={budgetInvalid}
              step="0.5"
              value={budget || String(summary.budget.monthly_budget_usd)}
              onChange={(event) => setBudget(event.target.value)}
              className="w-40"
              disabled={!canSetBudget}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="token-input">{t.usage.perRunLimit}</Label>
            <Input
              id="token-input"
              type="number"
              min={0}
              aria-invalid={budgetInvalid}
              step="1000"
              value={tokenLimit || String(summary.budget.per_run_token_limit)}
              onChange={(event) => setTokenLimit(event.target.value)}
              className="w-40"
              disabled={!canSetBudget}
            />
          </div>
          <Button
            onClick={() => budgetMutation.mutate()}
            disabled={!canSetBudget || budgetMutation.isPending || budgetInvalid}
            title={canSetBudget ? undefined : "Hanya owner/admin yang dapat mengubah anggaran."}
          >
            {t.common.save}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}





