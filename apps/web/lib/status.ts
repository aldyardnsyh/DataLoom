/**
 * Single source of truth for status -> presentation mapping.
 *
 * PRD 12.5: status is NEVER conveyed by colour alone: every entry returns an
 * icon + label + colour variant, and StatusBadge always renders icon + text.
 */
import {
  AlertTriangle,
  CircleCheck,
  CircleDashed,
  CircleSlash,
  Clock,
  Loader2,
  PauseCircle,
  RefreshCw,
  ShieldQuestion,
  XCircle,
  type LucideIcon,
} from "lucide-react";

import { statusLabel } from "@/lib/i18n/id";

export type StatusVariant =
  | "default"
  | "secondary"
  | "outline"
  | "success"
  | "warning"
  | "destructive"
  | "info"
  | "muted";

export interface StatusStyle {
  icon: LucideIcon;
  variant: StatusVariant;
  label: string;
  /** Spin the icon (running states) without colour being the only cue. */
  spin?: boolean;
}

const RUN_STATUS_STYLES: Record<string, StatusStyle> = {
  draft: { icon: CircleDashed, variant: "muted", label: statusLabel("draft") },
  validated: { icon: CircleCheck, variant: "info", label: statusLabel("validated") },
  queued: { icon: Clock, variant: "muted", label: statusLabel("queued") },
  running: { icon: Loader2, variant: "info", label: statusLabel("running"), spin: true },
  retrying: { icon: RefreshCw, variant: "warning", label: statusLabel("retrying"), spin: true },
  succeeded: { icon: CircleCheck, variant: "success", label: statusLabel("succeeded") },
  failed: { icon: XCircle, variant: "destructive", label: statusLabel("failed") },
  needs_attention: {
    icon: AlertTriangle,
    variant: "warning",
    label: statusLabel("needs_attention"),
  },
  cancelled: { icon: CircleSlash, variant: "muted", label: statusLabel("cancelled") },
  pending: { icon: Clock, variant: "muted", label: statusLabel("pending") },
  skipped: { icon: CircleSlash, variant: "muted", label: statusLabel("skipped") },
  approved: { icon: CircleCheck, variant: "success", label: statusLabel("approved") },
  rejected: { icon: XCircle, variant: "destructive", label: statusLabel("rejected") },
  connected: { icon: CircleCheck, variant: "success", label: statusLabel("connected") },
  active: { icon: CircleCheck, variant: "success", label: statusLabel("active") },
  error: { icon: XCircle, variant: "destructive", label: statusLabel("error") },
  paused: { icon: PauseCircle, variant: "muted", label: statusLabel("paused") },
  waiting_approval: {
    icon: Clock,
    variant: "warning",
    label: statusLabel("waiting_approval"),
  },
  insufficient_evidence: {
    icon: ShieldQuestion,
    variant: "warning",
    label: statusLabel("insufficient_evidence"),
  },
  budget_exceeded: {
    icon: AlertTriangle,
    variant: "destructive",
    label: "Anggaran habis",
  },
};

export function statusStyle(status: string): StatusStyle {
  return (
    RUN_STATUS_STYLES[status] ?? {
      icon: CircleDashed,
      variant: "muted",
      label: status,
    }
  );
}

/** Options for selects (roles, node types, ...). */
export const ROLE_OPTIONS = [
  { value: "owner", label: "Owner" },
  { value: "admin", label: "Admin" },
  { value: "editor", label: "Editor" },
  { value: "viewer", label: "Viewer" },
] as const;

export const LAYER_LABEL: Record<string, string> = {
  bronze: "Bronze",
  silver: "Silver",
  gold: "Gold",
  quarantine: "Karantina",
};
