"use client";

import Link from "next/link";

import { AdminGate } from "@/components/domain/admin/admin-gate";
import { QualityPanel } from "@/components/domain/admin/quality-panel";
import { Button } from "@/components/ui/button";
import { t } from "@/lib/i18n/id";

/** Quality gates for the active project (`/admin/quality`). */
export default function AdminQualityPage() {
  return (
    <div className="page-container space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-bold tracking-tight">{t.adminHub.qualityTitle}</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{t.adminHub.qualityBody}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button asChild variant="ghost" size="sm">
            <Link href="/data-contract">{t.nav.contract}</Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link href="/admin">{t.adminHub.title}</Link>
          </Button>
        </div>
      </header>

      <AdminGate>
        <QualityPanel />
      </AdminGate>
    </div>
  );
}