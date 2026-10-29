"use client";

import { AlertTriangle, CheckCircle2, Info, Loader2, XCircle } from "lucide-react";
import { toast } from "sonner";

/**
 * One notification vocabulary for the whole app.
 *
 * `toast.success("Tersimpan")` is technically enough, but it gives the operator
 * no way to tell a completed write from a warning or a neutral demo message.
 * These helpers fix three things:
 *   1. a distinct icon and colour per type;
 *   2. a title plus an optional description, so the message says what changed;
 *   3. a type-specific duration, so an error stays long enough to read while a
 *      success does not camp on the screen.
 */
export interface NotifyOptions {
  description?: string;
  duration?: number;
  id?: string;
}

const icon = "h-4 w-4 shrink-0";

export const notify = {
  success(title: string, options: NotifyOptions = {}) {
    return toast.success(title, {
      description: options.description,
      id: options.id,
      duration: options.duration ?? 4_500,
      icon: <CheckCircle2 className={`${icon} text-success`} aria-hidden="true" />,
    });
  },

  error(title: string, options: NotifyOptions = {}) {
    return toast.error(title, {
      description: options.description,
      id: options.id,
      duration: options.duration ?? 9_000,
      icon: <XCircle className={`${icon} text-destructive`} aria-hidden="true" />,
    });
  },

  warning(title: string, options: NotifyOptions = {}) {
    return toast.warning(title, {
      description: options.description,
      id: options.id,
      duration: options.duration ?? 7_000,
      icon: <AlertTriangle className={`${icon} text-warning`} aria-hidden="true" />,
    });
  },

  info(title: string, options: NotifyOptions = {}) {
    return toast.info(title, {
      description: options.description,
      id: options.id,
      duration: options.duration ?? 6_000,
      icon: <Info className={`${icon} text-primary`} aria-hidden="true" />,
    });
  },

  loading(title: string, options: NotifyOptions = {}) {
    return toast.loading(title, {
      description: options.description,
      id: options.id,
      duration: options.duration ?? Infinity,
      icon: <Loader2 className={`${icon} animate-spin text-primary`} aria-hidden="true" />,
    });
  },

  dismiss(id?: string | number) {
    toast.dismiss(id);
  },
};