"use client";

import Link from "next/link";

import { AdminGate } from "@/components/domain/admin/admin-gate";
import { AuditPanel } from "@/components/domain/admin/audit-panel";
import { Button } from "@/components/ui/button";
import { t } from "@/lib/i18n/id";

/** Organization audit log (`/admin/audit`). Read-only. */
export default function AdminAuditPage() {
  return (
    <div className="page-container space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-bold tracking-tight">{t.adminHub.auditTitle}</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{t.adminHub.auditBody}</p>
        </div>
        <Button asChild variant="outline" size="sm">
          <Link href="/admin">{t.adminHub.title}</Link>
        </Button>
      </header>

      <AdminGate>
        <AuditPanel />
      </AdminGate>
    </div>
  );
}