import { CircleCheck, CircleDashed, Cpu, Info } from "lucide-react";

import { EmptyState } from "@/components/domain/empty-state";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime, formatUsd } from "@/lib/format";
import { t } from "@/lib/i18n/id";
import type { InstallationLlmStatus } from "@/lib/types";

/**
 * Normalise the short key hint the API returns into a four character suffix.
 *
 * The contract only promises a hint, not its exact shape: it may arrive as
 * "abcd", "…abcd" or an already masked value such as "sk-…abcd". Whatever the
 * shape, at most the last four characters are rendered, so a payload that leaks
 * a longer fragment still cannot leak a usable key through this UI.
 */
export function keyHintSuffix(hint: string | null | undefined): string | null {
  if (!hint) return null;
  const cleaned = hint.trim().replace(/^[.\u2026\s]+/, "");
  const suffix = cleaned.slice(-4);
  return suffix === "" ? null : suffix;
}

/** True when the installation can actually call a model. */
export function isModelAiConfigured(status: InstallationLlmStatus): boolean {
  return status.api_key_configured && status.model_default.trim() !== "";
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 border-b border-border py-2 last:border-b-0 sm:flex-row sm:items-baseline sm:gap-4">
      <dt className="w-full shrink-0 text-xs text-muted-foreground sm:w-48">{label}</dt>
      <dd className="min-w-0 text-sm text-foreground">{children}</dd>
    </div>
  );
}

/**
 * Read-only summary of the installation wide AI model configuration.
 *
 * Shared by the "Model AI" tab on the settings page and by the admin
 * installation page, so both screens describe the same state with the same
 * words. It renders NO input and NO mutation control on purpose: every member
 * can read this panel, and only installation admins can change the values.
 */
export function ModelAiStatusPanel({ status }: { status: InstallationLlmStatus | undefined }) {
  // A missing payload means "nothing configured yet"; treat it as not
  // configured instead of forcing every caller to narrow the type.
  const suffix = keyHintSuffix(status?.api_key_hint);
  const configured = status ? isModelAiConfigured(status) : false;
  const data = status;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2">
            {t.modelAi.statusLabel}
            {data ? (
              <Badge variant="success" className="gap-1">
                <CircleCheck className="h-3 w-3" aria-hidden="true" />
                {t.modelAi.configured}
              </Badge>
            ) : (
              <Badge variant="warning" className="gap-1">
                <CircleDashed className="h-3 w-3" aria-hidden="true" />
                {t.modelAi.notConfigured}
              </Badge>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {data ? (
            <dl>
              <Row label={t.modelAi.providerLabel}>
                <span className="font-medium">{data.provider || t.common.unknown}</span>
              </Row>
              <Row label={t.modelAi.hostLabel}>
                <span className="font-mono text-xs">{data.base_url_host || t.common.unknown}</span>
              </Row>
              <Row label={t.modelAi.modelDefault}>
                <span className="font-mono text-xs">{data.model_default}</span>
              </Row>
              <Row label={t.modelAi.modelSmall}>
                <span className="font-mono text-xs">{data.model_small || t.modelAi.priceUnavailable}</span>
              </Row>
              <Row label={t.modelAi.apiKeyLabel}>
                <span className="inline-flex flex-wrap items-center gap-1.5">
                  <span>{t.modelAi.apiKeySavedShort}</span>
                  {suffix ? (
                    <span className="text-muted-foreground">
                      (berakhiran <span className="font-mono">{suffix}</span>)
                    </span>
                  ) : null}
                </span>
              </Row>
              <Row label={t.modelAi.priceLabel}>
                {data.prices_configured ? (
                  <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
                    <span>
                      <span className="text-xs text-muted-foreground">{t.modelAi.priceIn}: </span>
                      <span className="tabular">
                        {data.price_in_per_1k === null || status.price_in_per_1k === undefined
                          ? t.modelAi.priceUnavailable
                          : formatUsd(status.price_in_per_1k)}
                      </span>
                    </span>
                    <span>
                      <span className="text-xs text-muted-foreground">{t.modelAi.priceOut}: </span>
                      <span className="tabular">
                        {data.price_out_per_1k === null || status.price_out_per_1k === undefined
                          ? t.modelAi.priceUnavailable
                          : formatUsd(status.price_out_per_1k)}
                      </span>
                    </span>
                  </span>
                ) : (
                  <span className="text-muted-foreground">{t.modelAi.priceUnavailable}</span>
                )}
              </Row>
              <Row label={t.modelAi.lastUpdated}>
                {data.updated_at ? (
                  <span>
                    {formatDateTime(data.updated_at)}
                    {data.updated_by ? (
                      <span className="text-muted-foreground">
                        {" "}
                        {t.modelAi.updatedBy} {data.updated_by}
                      </span>
                    ) : null}
                  </span>
                ) : (
                  <span className="text-muted-foreground">{t.modelAi.neverUpdated}</span>
                )}
              </Row>
            </dl>
          ) : (
            <EmptyState
              compact
              icon={Cpu}
              title={t.modelAi.emptyTitle}
              description={t.modelAi.emptyBody}
            />
          )}

          <p className="mt-3 flex items-start gap-1.5 text-[11px] text-muted-foreground">
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            {t.modelAi.apiKeyStorageNote}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t.modelAi.usageTitle}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">{t.modelAi.usageBody}</p>
        </CardContent>
      </Card>
    </div>
  );
}



