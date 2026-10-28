"use client";

import Link from "next/link";

import { AdminGate } from "@/components/domain/admin/admin-gate";
import { TeamPanel } from "@/components/domain/admin/team-panel";
import { Button } from "@/components/ui/button";
import { t } from "@/lib/i18n/id";

/** Team administration (`/admin/team`). */
export default function AdminTeamPage() {
  return (
    <div className="page-container space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-bold tracking-tight">{t.adminHub.teamTitle}</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{t.adminHub.teamBody}</p>
        </div>
        <Button asChild variant="outline" size="sm">
          <Link href="/admin">{t.adminHub.title}</Link>
        </Button>
      </header>

      <AdminGate>
        <TeamPanel />
      </AdminGate>
    </div>
  );
}