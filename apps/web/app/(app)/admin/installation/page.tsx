"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CircleCheck,
  CircleDashed,
  Cpu,
  Eye,
  EyeOff,
  Lock,
  PlugZap,
  UserPlus,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { notify } from "@/lib/notify";

import { EmptyState } from "@/components/domain/empty-state";
import { ErrorState } from "@/components/domain/error-state";
import { ModelAiStatusPanel, isModelAiConfigured, keyHintSuffix } from "@/components/domain/model-ai-status";
import { BlockSkeleton, LoadingAnnouncer } from "@/components/domain/state-loading";
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
import { Textarea } from "@/components/ui/textarea";
import { useSession } from "@/components/providers/session-provider";
import { api, classifyError } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import { t } from "@/lib/i18n/id";
import {
  buildUpdatePayload,
  EMPTY_INSTALLATION_FORM,
  optionalNumber,
  type InstallationFormState,
} from "@/lib/installation-form";
import type { InstallationLlmTestResult, InstallationLlmUpdate } from "@/lib/types";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * Installation admin page.
 *
 * Access: owner/admin of the organisation only. Everyone else gets an empty
 * state with no configuration data, so the page never leaks the shared model
 * configuration to a role that cannot use it.
 *
 * The API key is write-only from the browser's point of view: it is never
 * rendered after saving, and an empty input means "keep the stored key".
 */
export default function InstallationAdminPage() {
  const { session } = useSession();
  const queryClient = useQueryClient();

  const role = session?.membership.role ?? "viewer";
  const isOrgAdmin = role === "owner" || role === "admin";
  /**
   * Writing the deployment LLM configuration requires the installation-admin
   * flag, which no organization role grants. An org admin may READ the status
   * (the API allows it) but must not be shown a form that will always 403.
   */
  const canConfigure = Boolean(session?.is_install_admin);

  const [form, setForm] = useState<InstallationFormState>(EMPTY_INSTALLATION_FORM);
  const [showKey, setShowKey] = useState(false);
  const [testResult, setTestResult] = useState<InstallationLlmTestResult | null>(null);
  const [grantOpen, setGrantOpen] = useState(false);
  const [grantEmail, setGrantEmail] = useState("");
  const [grantReason, setGrantReason] = useState("");

  const statusQuery = useQuery({
    queryKey: ["installation", "llm"],
    queryFn: () => api.installation.status(),
    enabled: isOrgAdmin,
    staleTime: 30_000,
  });

  const status = statusQuery.data;

  // Prefill the form from the stored status.
  //
  // `base_url` is the exception: the status payload only carries the host
  // (`base_url_host`), never the full URL. Prefilling the input with a bare host
  // would turn "save" into "silently truncate the base URL", so the field stays
  // empty and shows the stored host as a placeholder instead. The API key is
  // likewise empty: it is not readable, and an empty box means "unchanged".
  useEffect(() => {
    if (!status) return;
    setForm({
      base_url: "",
      model_default: status.model_default ?? "",
      model_small: status.model_small ?? "",
      timeout_seconds:
        status.timeout_seconds === null || status.timeout_seconds === undefined
          ? ""
          : String(status.timeout_seconds),
      price_in_per_1k:
        status.price_in_per_1k === null || status.price_in_per_1k === undefined
          ? ""
          : String(status.price_in_per_1k),
      price_out_per_1k:
        status.price_out_per_1k === null || status.price_out_per_1k === undefined
          ? ""
          : String(status.price_out_per_1k),
      api_key: "",
    });
  }, [status]);

  const updateMutation = useMutation({
    mutationFn: (payload: InstallationLlmUpdate) => api.installation.update(payload),
    onSuccess: async () => {
      notify.success(t.installation.saveSuccess, { description: t.notify.installationSaved });
      setForm((current) => ({ ...current, api_key: "" }));
      setShowKey(false);
      await queryClient.invalidateQueries({ queryKey: ["installation", "llm"] });
    },
    onError: (error: Error) => notify.error(t.notify.failedTitle, { description: error.message || t.errors.generic }),
  });

  const testMutation = useMutation({
    mutationFn: () => api.installation.test(),
    onSuccess: (result) => setTestResult(result),
    onError: (error: unknown) => {
      const classified = classifyError(error);
      setTestResult({
        ok: false,
        latency_ms: null,
        error_code: classified.kind,
        error_message: classified.message,
      });
    },
  });

  const grantMutation = useMutation({
    mutationFn: () =>
      api.installation.grantAdmin({
        email: grantEmail.trim(),
        reason: grantReason.trim() === "" ? undefined : grantReason.trim(),
      }),
    onSuccess: () => {
      notify.success(t.installation.grantSuccess, { description: t.notify.adminGranted });
      setGrantOpen(false);
      setGrantEmail("");
      setGrantReason("");
    },
    onError: (error: Error) => notify.error(t.notify.failedTitle, { description: error.message || t.errors.generic }),
  });

  // Non-admins see a plain refusal. No query is fired (see `enabled` above), so
  // there is no configuration payload in the browser to leak.
  if (!isOrgAdmin) {
    return (
      <div className="page-container space-y-4">
        <header>
          <h1 className="text-xl font-bold tracking-tight">{t.installation.title}</h1>
        </header>
        <EmptyState
          icon={Lock}
          title={t.installation.noAccessTitle}
          description={t.installation.noAccessBody}
          secondaryAction={{ label: t.adminHub.backToWorkspace, href: "/dashboard" }}
        />
      </div>
    );
  }

  const configured = status ? isModelAiConfigured(status) : false;
  const apiKeySuffix = keyHintSuffix(status?.api_key_hint);
  const timeoutValue = optionalNumber(form.timeout_seconds);
  const timeoutInvalid = timeoutValue !== undefined && (timeoutValue < 1 || timeoutValue > 300);
  const priceInInvalid = (optionalNumber(form.price_in_per_1k) ?? 0) < 0;
  const priceOutInvalid = (optionalNumber(form.price_out_per_1k) ?? 0) < 0;
  const formInvalid = timeoutInvalid || priceInInvalid || priceOutInvalid;

  return (
    <div className="page-container space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-bold tracking-tight">{t.installation.title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t.installation.subtitle}</p>
        </div>
        <Button variant="outline" size="sm" asChild>
          <Link href="/admin">{t.adminHub.title}</Link>
        </Button>
      </header>

      {/* The scope warning sits above the form, before anything is editable. */}
      <div
        role="status"
        className="flex items-start gap-2 rounded-card border border-warning/30 bg-warning/10 px-3 py-2 text-sm"
      >
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden="true" />
        <div className="min-w-0">
          <p className="font-medium text-warning-text">{t.installation.warningTitle}</p>
          <p className="mt-0.5 text-xs text-warning-text">{t.installation.warningBody}</p>
        </div>
      </div>

      {statusQuery.isLoading ? (
        <div className="space-y-3">
          <LoadingAnnouncer />
          <BlockSkeleton height={360} />
          <BlockSkeleton height={120} />
        </div>
      ) : statusQuery.isError ? (
        <ErrorState
          kind={classifyError(statusQuery.error).kind}
          title={t.installation.loadError}
          message={classifyError(statusQuery.error).message}
          requestId={classifyError(statusQuery.error).requestId}
          onRetry={() => void statusQuery.refetch()}
        />
      ) : !status ? (
        // The query resolved without a payload: show a retry surface instead of
        // a form over undefined data, where "save" would look like it worked.
        <EmptyState
          icon={Cpu}
          title={t.modelAi.emptyTitle}
          description={t.modelAi.emptyBody}
          secondaryAction={{ label: t.common.retry, onClick: () => void statusQuery.refetch() }}
        />
      ) : (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center gap-2">
                {t.installation.formTitle}
                {configured ? (
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
                {canConfigure ? null : (
                  <Badge variant="outline" className="gap-1">
                    <Lock className="h-3 w-3" aria-hidden="true" />
                    {t.installation.readOnlyBadge}
                  </Badge>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent>
              {canConfigure ? null : (
                <div
                  role="status"
                  className="mb-4 flex items-start gap-2 rounded-card border border-info/30 bg-info/10 px-3 py-2 text-sm text-info"
                >
                  <Lock className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                  <span>{t.installation.readOnlyBody}</span>
                </div>
              )}
              <form
                className="space-y-4"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (formInvalid) return;
                  updateMutation.mutate(buildUpdatePayload(form));
                }}
              >
                <div className="grid min-w-0 gap-4 md:grid-cols-2">
                  <div className="space-y-1.5 md:col-span-2">
                    <Label htmlFor="llm-base-url">{t.installation.baseUrl}</Label>
                    <Input
                      id="llm-base-url"
                      value={form.base_url}
                      onChange={(event) => setForm({ ...form, base_url: event.target.value })}
                      placeholder={status?.base_url_host || "https://api.openai.com/v1"}
                      autoComplete="off"
                      spellCheck={false}
                      disabled={updateMutation.isPending || !canConfigure}
                      aria-describedby="llm-base-url-hint"
                    />
                    <p id="llm-base-url-hint" className="text-[11px] text-muted-foreground">
                      {t.installation.baseUrlHint}
                      {status?.base_url_host ? (
                        <>
                          {" "}
                          {t.installation.baseUrlCurrent}{" "}
                          <span className="font-mono">{status.base_url_host}</span>.
                        </>
                      ) : null}
                    </p>
                  </div>

                  <div className="space-y-1.5">
                    <Label htmlFor="llm-model-default">{t.installation.modelDefault}</Label>
                    <Input
                      id="llm-model-default"
                      value={form.model_default}
                      onChange={(event) => setForm({ ...form, model_default: event.target.value })}
                      autoComplete="off"
                      spellCheck={false}
                      disabled={updateMutation.isPending || !canConfigure}
                      aria-describedby="llm-model-default-hint"
                    />
                    <p id="llm-model-default-hint" className="text-[11px] text-muted-foreground">
                      {t.installation.modelDefaultHint}
                    </p>
                  </div>

                  <div className="space-y-1.5">
                    <Label htmlFor="llm-model-small">{t.installation.modelSmall}</Label>
                    <Input
                      id="llm-model-small"
                      value={form.model_small}
                      onChange={(event) => setForm({ ...form, model_small: event.target.value })}
                      autoComplete="off"
                      spellCheck={false}
                      disabled={updateMutation.isPending || !canConfigure}
                      aria-describedby="llm-model-small-hint"
                    />
                    <p id="llm-model-small-hint" className="text-[11px] text-muted-foreground">
                      {t.installation.modelSmallHint}
                    </p>
                  </div>

                  <div className="space-y-1.5">
                    <Label htmlFor="llm-timeout">{t.installation.timeout}</Label>
                    <Input
                      id="llm-timeout"
                      type="number"
                      min={1}
                      max={300}
                      step={1}
                      value={form.timeout_seconds}
                      onChange={(event) => setForm({ ...form, timeout_seconds: event.target.value })}
                      disabled={updateMutation.isPending || !canConfigure}
                      aria-invalid={timeoutInvalid}
                      aria-describedby="llm-timeout-hint"
                      className="tabular"
                    />
                    <p
                      id="llm-timeout-hint"
                      className={
                        timeoutInvalid ? "text-[11px] text-destructive-text" : "text-[11px] text-muted-foreground"
                      }
                    >
                      {t.installation.timeoutHint}
                    </p>
                  </div>

                  <div className="space-y-1.5">
                    <Label htmlFor="llm-price-in">{t.installation.priceIn}</Label>
                    <Input
                      id="llm-price-in"
                      type="number"
                      min={0}
                      step="0.0001"
                      value={form.price_in_per_1k}
                      onChange={(event) => setForm({ ...form, price_in_per_1k: event.target.value })}
                      disabled={updateMutation.isPending || !canConfigure}
                      aria-invalid={priceInInvalid}
                      aria-describedby="llm-price-hint"
                      className="tabular"
                    />
                  </div>

                  <div className="space-y-1.5">
                    <Label htmlFor="llm-price-out">{t.installation.priceOut}</Label>
                    <Input
                      id="llm-price-out"
                      type="number"
                      min={0}
                      step="0.0001"
                      value={form.price_out_per_1k}
                      onChange={(event) => setForm({ ...form, price_out_per_1k: event.target.value })}
                      disabled={updateMutation.isPending || !canConfigure}
                      aria-invalid={priceOutInvalid}
                      aria-describedby="llm-price-hint"
                      className="tabular"
                    />
                  </div>

                  <p id="llm-price-hint" className="text-[11px] text-muted-foreground md:col-span-2">
                    {t.installation.priceHint}
                  </p>

                  <div className="space-y-1.5 md:col-span-2">
                    <Label htmlFor="llm-api-key">{t.installation.apiKey}</Label>
                    <div className="relative">
                      <Input
                        id="llm-api-key"
                        type={showKey ? "text" : "password"}
                        value={form.api_key}
                        onChange={(event) => setForm({ ...form, api_key: event.target.value })}
                        placeholder={t.installation.apiKeyPlaceholder}
                        autoComplete="new-password"
                        spellCheck={false}
                        disabled={updateMutation.isPending || !canConfigure}
                        aria-describedby="llm-api-key-hint"
                        className="pr-10 font-mono"
                      />
                      <button
                        type="button"
                        onClick={() => setShowKey((current) => !current)}
                        className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                        aria-label={showKey ? t.installation.hideKey : t.installation.showKey}
                        aria-pressed={showKey}
                      >
                        {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </button>
                    </div>
                    <p id="llm-api-key-hint" className="text-[11px] text-muted-foreground">
                      {t.installation.apiKeyHint}
                    </p>
                    <p className="text-[11px] text-muted-foreground">
                      {status?.api_key_configured
                        ? `${t.modelAi.apiKeySaved}${apiKeySuffix ? ` ${apiKeySuffix}` : ""}).`
                        : t.modelAi.apiKeyMissing}
                    </p>
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <Button type="submit" disabled={updateMutation.isPending || formInvalid || !canConfigure}>
                    {updateMutation.isPending ? "Menyimpan…" : t.installation.save}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => testMutation.mutate()}
                    disabled={testMutation.isPending || !canConfigure}
                  >
                    <PlugZap className="h-4 w-4" aria-hidden="true" />
                    {testMutation.isPending ? t.installation.testing : t.installation.test}
                  </Button>
                  <p className="text-[11px] text-muted-foreground">{t.installation.testHint}</p>
                </div>

                {/* Test result: success names the latency, failure names the
                    code AND the message. Both use icon + text, never colour
                    alone. */}
                <div aria-live="polite">
                  {testMutation.isPending ? (
                    <p className="text-xs text-muted-foreground">{t.installation.testing}</p>
                  ) : testResult ? (
                    testResult.ok ? (
                      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-success/30 bg-success/10 px-3 py-2 text-sm">
                        <CircleCheck className="h-4 w-4 shrink-0 text-success" aria-hidden="true" />
                        <span className="font-medium text-success-text">{t.installation.testSuccess}</span>
                        <span className="text-xs text-success-text">
                          {t.installation.testLatency}: {formatDuration(testResult.latency_ms)}
                        </span>
                      </div>
                    ) : (
                      <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm">
                        <AlertTriangle
                          className="mt-0.5 h-4 w-4 shrink-0 text-destructive"
                          aria-hidden="true"
                        />
                        <div className="min-w-0">
                          <p className="font-medium text-destructive-text">{t.installation.testFailed}</p>
                          <p className="mt-0.5 break-words text-xs text-destructive-text">
                            {testResult.error_code ? (
                              <span className="font-mono">
                                {t.installation.testErrorCode}: {testResult.error_code}
                              </span>
                            ) : null}
                            {testResult.error_message ? (
                              <span className={testResult.error_code ? "ml-2" : ""}>
                                {testResult.error_message}
                              </span>
                            ) : null}
                          </p>
                        </div>
                      </div>
                    )
                  ) : (
                    <p className="text-xs text-muted-foreground">{t.installation.testNeverRun}</p>
                  )}
                </div>
              </form>
            </CardContent>
          </Card>

          <ModelAiStatusPanel status={status} />

          <Card>
            <CardHeader>
              <CardTitle>{t.installation.grantTitle}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-muted-foreground">{t.installation.grantBody}</p>
              <Button variant="outline" onClick={() => setGrantOpen(true)} disabled={!canConfigure}>
                <UserPlus className="h-4 w-4" aria-hidden="true" />
                {t.installation.grantButton}
              </Button>
            </CardContent>
          </Card>
        </>
      )}

      <Dialog
        open={grantOpen}
        onOpenChange={(open) => {
          setGrantOpen(open);
          if (!open) {
            setGrantEmail("");
            setGrantReason("");
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.installation.grantTitle}</DialogTitle>
            <DialogDescription>{t.installation.grantBody}</DialogDescription>
          </DialogHeader>

          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="grant-email">{t.installation.grantEmail}</Label>
              <Input
                id="grant-email"
                type="email"
                value={grantEmail}
                onChange={(event) => setGrantEmail(event.target.value)}
                autoComplete="off"
                disabled={grantMutation.isPending}
                aria-describedby="grant-email-hint"
                aria-invalid={grantEmail.trim() !== "" && !EMAIL_PATTERN.test(grantEmail.trim())}
              />
              <p
                id="grant-email-hint"
                className={
                  grantEmail.trim() !== "" && !EMAIL_PATTERN.test(grantEmail.trim())
                    ? "text-[11px] text-destructive-text"
                    : "text-[11px] text-muted-foreground"
                }
              >
                {grantEmail.trim() !== "" && !EMAIL_PATTERN.test(grantEmail.trim())
                  ? t.installation.grantInvalidEmail
                  : t.installation.grantEmailHint}
              </p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="grant-reason">{t.installation.grantReason}</Label>
              <Textarea
                id="grant-reason"
                value={grantReason}
                onChange={(event) => setGrantReason(event.target.value)}
                rows={3}
                maxLength={500}
                disabled={grantMutation.isPending}
                aria-describedby="grant-reason-hint"
              />
              <p id="grant-reason-hint" className="text-[11px] text-muted-foreground">
                {t.installation.grantReasonHint}
              </p>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setGrantOpen(false)} disabled={grantMutation.isPending}>
              {t.common.cancel}
            </Button>
            <Button
              onClick={() => grantMutation.mutate()}
              disabled={!canConfigure || !EMAIL_PATTERN.test(grantEmail.trim()) || grantMutation.isPending}
            >
              {grantMutation.isPending ? "Memproses…" : t.installation.grantSubmit}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

