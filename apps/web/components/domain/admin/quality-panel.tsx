"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { notify } from "@/lib/notify";

import { ErrorState } from "@/components/domain/error-state";
import { BlockSkeleton } from "@/components/domain/state-loading";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useProject } from "@/components/providers/project-provider";
import { useCanMutate } from "@/components/providers/session-provider";
import { api, classifyError } from "@/lib/api";
import { formatPercent } from "@/lib/format";
import { t } from "@/lib/i18n/id";
import type { ProjectSettings } from "@/lib/types";

/**
 * Quality gates: the thresholds a run must satisfy before gold is promoted.
 *
 * Scoped to the ACTIVE PROJECT (the header names it), which is why it is not
 * under "installation": changing these does not affect other projects or other
 * tenants. It is still an admin surface because a wrong threshold silently
 * changes what data reaches consumers.
 */
export function QualityPanel() {
  const { activeProject } = useProject();
  const canMutate = useCanMutate();
  const queryClient = useQueryClient();
  const [gateForm, setGateForm] = useState<ProjectSettings | null>(null);
  /**
   * Freshness needs its own text draft. Binding a number input straight to a
   * number makes the field impossible to clear: every keystroke re-parses and
   * the clamp writes back a value, so "3" becomes "13".
   */
  const [freshnessText, setFreshnessText] = useState("");
  const freshnessValue = Number(freshnessText);
  const freshnessInvalid = freshnessText.trim() === "" || !Number.isFinite(freshnessValue) || freshnessValue < 1;

  const gatesQuery = useQuery({
    queryKey: ["project-settings", activeProject?.id],
    queryFn: () => api.projects.settings(activeProject!.id),
    enabled: Boolean(activeProject?.id),
  });

  useEffect(() => {
    if (!gatesQuery.data) return;
    setGateForm(gatesQuery.data);
    setFreshnessText(String(gatesQuery.data.freshness_threshold_minutes));
  }, [gatesQuery.data]);

  const gatesMutation = useMutation({
    mutationFn: (settings: ProjectSettings) =>
      api.projects.updateSettings(activeProject!.id, settings),
    onSuccess: async () => {
      notify.success(t.settings.savedGates, { description: t.notify.gatesSaved });
      await queryClient.invalidateQueries({ queryKey: ["project-settings", activeProject?.id] });
    },
    onError: (error: Error) => notify.error(t.notify.failedTitle, { description: error.message }),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {t.settings.qualityTitle}
          {!canMutate ? <ShieldAlert className="h-4 w-4 text-warning" aria-hidden="true" /> : null}
        </CardTitle>
        <p className="mt-1 text-xs text-muted-foreground">
          {t.adminHub.qualityBody}{" "}
          {activeProject ? (
            <span className="font-medium text-foreground">{activeProject.name}</span>
          ) : null}
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        {gatesQuery.isLoading ? (
          <BlockSkeleton height={140} />
        ) : gatesQuery.isError ? (
          <ErrorState
            compact
            kind={classifyError(gatesQuery.error).kind}
            message={classifyError(gatesQuery.error).message}
            requestId={classifyError(gatesQuery.error).requestId}
            onRetry={() => void gatesQuery.refetch()}
          />
        ) : !gateForm ? null : (
          <>
            <div className="space-y-1">
              <Label htmlFor="quarantine-ratio">{t.settings.maxQuarantine}</Label>
              <div className="flex items-center gap-2">
                <Input
                  id="quarantine-ratio"
                  type="number"
                  min={0}
                  max={100}
                  step={0.5}
                  value={Number((gateForm.max_quarantine_ratio * 100).toFixed(2))}
                  onChange={(event) =>
                    setGateForm({
                      ...gateForm,
                      max_quarantine_ratio: Math.min(1, Math.max(0, Number(event.target.value) / 100)),
                    })
                  }
                  className="w-28"
                  disabled={!canMutate}
                />
                <span className="text-sm text-muted-foreground">%</span>
                <span className="text-xs text-muted-foreground">
                  saat ini {formatPercent(gateForm.max_quarantine_ratio)}
                </span>
              </div>
            </div>

            <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
              <Label htmlFor="require-approval" className="cursor-pointer">
                {t.settings.requireApproval}
              </Label>
              <Switch
                id="require-approval"
                checked={gateForm.require_approval_for_destructive}
                onCheckedChange={(checked) =>
                  setGateForm({ ...gateForm, require_approval_for_destructive: checked })
                }
                disabled={!canMutate}
              />
            </div>

            <div className="space-y-1">
              <Label htmlFor="freshness">{t.settings.freshness}</Label>
              <Input
                id="freshness"
                type="number"
                min={1}
                value={freshnessText}
                onChange={(event) => setFreshnessText(event.target.value)}
                onBlur={() => {
                  if (freshnessInvalid) return;
                  setGateForm({ ...gateForm, freshness_threshold_minutes: Math.round(freshnessValue) });
                }}
                className="w-40"
                aria-invalid={freshnessInvalid}
                disabled={!canMutate}
              />
            </div>

            <Button
              onClick={() => gatesMutation.mutate(gateForm)}
              disabled={!canMutate || gatesMutation.isPending || freshnessInvalid}
              title={canMutate ? undefined : t.state.disabledTooltip}
            >
              {t.settings.saveGates}
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  );
}

