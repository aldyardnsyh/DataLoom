"use client";

import { Cpu, ScrollText, ShieldCheck, Users } from "lucide-react";
import Link from "next/link";

import { AdminGate } from "@/components/domain/admin/admin-gate";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { t } from "@/lib/i18n/id";

const SECTIONS = [
  {
    href: "/admin/team",
    icon: Users,
    title: t.adminHub.teamTitle,
    body: t.adminHub.teamBody,
  },
  {
    href: "/admin/quality",
    icon: ShieldCheck,
    title: t.adminHub.qualityTitle,
    body: t.adminHub.qualityBody,
  },
  {
    href: "/admin/audit",
    icon: ScrollText,
    title: t.adminHub.auditTitle,
    body: t.adminHub.auditBody,
  },
  {
    href: "/admin/installation",
    icon: Cpu,
    title: t.adminHub.llmTitle,
    body: t.adminHub.llmBody,
    restricted: true,
  },
] as const;

/**
 * Administration hub.
 *
 * A landing page instead of one giant tabbed screen: each section has a
 * different blast radius (members = access, quality = what data ships, audit =
 * read-only, LLM = whole deployment), and burying them as tabs made it easy to
 * change the wrong one. The cards state the scope in plain words.
 */
export default function AdminHubPage() {
  return (
    <div className="page-container space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-bold tracking-tight">{t.adminHub.title}</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{t.adminHub.subtitle}</p>
        </div>
        <Button asChild variant="outline" size="sm">
          <Link href="/dashboard">{t.adminHub.backToWorkspace}</Link>
        </Button>
      </header>

      <p
        role="note"
        className="rounded-card border border-border bg-muted px-3 py-2 text-xs text-muted-foreground"
      >
        {t.adminHub.scopeNote}
      </p>

      <AdminGate>
        <ul className="grid min-w-0 gap-4 sm:grid-cols-2">
          {SECTIONS.map((section) => {
            const Icon = section.icon;
            return (
              <li key={section.href}>
                <Card className="h-full">
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base">
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-accent text-accent-foreground">
                        <Icon className="h-4 w-4" aria-hidden="true" />
                      </span>
                      {section.title}
                      {"restricted" in section && section.restricted ? (
                        <Badge variant="outline" className="ml-auto">
                          installation admin
                        </Badge>
                      ) : null}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <p className="text-sm text-muted-foreground">{section.body}</p>
                    <Button asChild variant="outline" size="sm">
                      <Link href={section.href}>{t.adminHub.open}</Link>
                    </Button>
                  </CardContent>
                </Card>
              </li>
            );
          })}
        </ul>
      </AdminGate>
    </div>
  );
}