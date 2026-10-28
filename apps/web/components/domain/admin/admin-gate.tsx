"use client";

import { Lock } from "lucide-react";

import { EmptyState } from "@/components/domain/empty-state";
import { useSession } from "@/components/providers/session-provider";
import { t } from "@/lib/i18n/id";

/**
 * Role gate for the /admin segment.
 *
 * Owner and admin are the only organization roles that may see administration
 * screens. This is a UX gate, NOT the security boundary: every mutation on
 * these pages is re-checked by the API (org role for team/quality/audit,
 * installation-admin flag for the LLM provider). Hiding the UI only prevents a
 * member from walking into a wall of 403s.
 */
export function AdminGate({ children }: { children: React.ReactNode }) {
  const { session, loading } = useSession();
  const role = session?.membership.role;

  if (loading) return null;

  if (role !== "owner" && role !== "admin") {
    return (
      <EmptyState
        icon={Lock}
        title={t.adminHub.noAccessTitle}
        description={t.adminHub.noAccessBody}
        secondaryAction={{ label: t.installation.backToSettings, href: "/settings" }}
      />
    );
  }

  return <>{children}</>;
}