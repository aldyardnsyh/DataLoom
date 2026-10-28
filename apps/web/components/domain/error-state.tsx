import { AlertOctagon, Ban, CloudOff, FileQuestion } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n/id";
import type { ErrorKind } from "@/lib/api";

const KIND_META: Record<
  ErrorKind,
  { title: string; icon: typeof AlertOctagon; tone: string; toneIcon: string }
> = {
  generic: { title: t.state.errorTitle, icon: AlertOctagon, tone: "destructive", toneIcon: "bg-destructive/10 text-destructive" },
  network: { title: "Koneksi terputus", icon: CloudOff, tone: "warning", toneIcon: "bg-warning/10 text-warning" },
  not_found: { title: "Data tidak ditemukan", icon: FileQuestion, tone: "muted", toneIcon: "bg-muted text-muted-foreground" },
  forbidden: { title: "Akses ditolak", icon: Ban, tone: "warning", toneIcon: "bg-warning/10 text-warning" },
  unauthorized: { title: "Sesi berakhir", icon: AlertOctagon, tone: "warning", toneIcon: "bg-warning/10 text-warning" },
};

const KIND_MESSAGE: Record<ErrorKind, string> = {
  generic: t.errors.generic,
  network: `${t.errors.network} Periksa koneksi Anda, lalu coba lagi.`,
  not_found: "Data ini tidak ada, sudah dihapus, atau bukan milik organisasi Anda.",
  forbidden: t.errors.forbidden,
  unauthorized: t.errors.unauthorized,
};

/**
 * ErrorState: the single failure pattern. The visual tone follows the error
 * kind (a 404 is not an alarm), and every state names the cause plus the
 * recovery. There is intentionally no fake support channel; the request id
 * is the correlation handle for whoever does support this deployment.
 */
export function ErrorState({
  kind = "generic",
  title,
  message,
  requestId,
  onRetry,
  className,
  compact = false,
}: {
  kind?: ErrorKind;
  title?: string;
  message?: string;
  requestId?: string | null;
  onRetry?: () => void;
  className?: string;
  compact?: boolean;
}) {
  const meta = KIND_META[kind];
  const Icon = meta.icon;
  const destructive = kind === "generic";

  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center justify-center rounded-card border px-6 text-center",
        destructive ? "border-destructive/30 bg-destructive/5" : "border-border bg-muted/50",
        compact ? "py-8" : "py-16",
        className,
      )}
    >
      <span
        className={cn("mb-3 flex h-10 w-10 items-center justify-center rounded-full", meta.toneIcon)}
        aria-hidden="true"
      >
        <Icon className="h-5 w-5" />
      </span>
      <h3 className="text-base font-semibold text-foreground">{title ?? meta.title}</h3>
      <p className="mt-1 max-w-md text-sm text-muted-foreground">
        {message ?? KIND_MESSAGE[kind]}
      </p>

      {requestId && requestId !== "unknown" ? (
        <p className="mt-2 font-mono text-xs text-muted-foreground">
          {t.common.requestId}: <span className="select-all text-foreground">{requestId}</span>
        </p>
      ) : null}

      {onRetry ? (
        <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
          <Button onClick={onRetry} variant={destructive ? "default" : "outline"}>
            {t.common.retry}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
