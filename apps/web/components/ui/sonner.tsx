"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Info,
  Loader2,
  XCircle,
} from "lucide-react";
import { Toaster as Sonner, type ToasterProps } from "sonner";

/**
 * Toast host. Light theme only; uses our token colours.
 *
 * Each type gets its own border, surface, icon, and accessible label from the
 * notification helper in `lib/notify.tsx`. The host only supplies the shell so
 * a caller that still uses Sonner directly does not lose the visual system.
 */
export function Toaster(props: ToasterProps) {
  return (
    <Sonner
      theme="light"
      position="bottom-right"
      closeButton
      className="toaster group"
      icons={{
        success: <CheckCircle2 className="h-4 w-4 text-success" aria-hidden="true" />,
        error: <XCircle className="h-4 w-4 text-destructive" aria-hidden="true" />,
        warning: <AlertTriangle className="h-4 w-4 text-warning" aria-hidden="true" />,
        info: <Info className="h-4 w-4 text-primary" aria-hidden="true" />,
        loading: <Loader2 className="h-4 w-4 animate-spin text-primary" aria-hidden="true" />,
      }}
      toastOptions={{
        classNames: {
          toast:
            "group rounded-lg border border-border bg-background text-foreground shadow-pop text-sm",
          title: "text-sm font-semibold text-foreground",
          description: "text-xs leading-relaxed text-muted-foreground",
          success: "border-success/35 bg-success/5",
          error: "border-destructive/35 bg-destructive/5",
          warning: "border-warning/35 bg-warning/5",
          info: "border-primary/25 bg-primary/5",
          closeButton:
            "border-border bg-background text-muted-foreground hover:text-foreground",
          actionButton: "bg-primary text-primary-foreground",
          cancelButton: "bg-muted text-muted-foreground",
        },
      }}
      {...props}
    />
  );
}