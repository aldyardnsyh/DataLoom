"use client";

import { AppShell } from "@/components/layout/app-shell";
import { DemoProvider } from "@/components/providers/demo-provider";
import { QueryProvider } from "@/components/providers/query-provider";
import { ProjectProvider } from "@/components/providers/project-provider";
import { SessionProvider } from "@/components/providers/session-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

/**
 * Client-side providers for the authenticated app segment.
 * Kept in the (app) group so /login and /register stay provider-light.
 *
 * DemoProvider wraps SessionProvider (not the other way round): the session
 * query must see the demo flag on its very first fetch, otherwise `/auth/me`
 * would hit the real backend before the demo short-circuit is in effect.
 */
export default function AppGroupLayout({ children }: { children: React.ReactNode }) {
  return (
    <DemoProvider>
      <QueryProvider>
        <SessionProvider>
          <ProjectProvider>
            <TooltipProvider delayDuration={200}>
              <AppShell>{children}</AppShell>
              <Toaster />
            </TooltipProvider>
          </ProjectProvider>
        </SessionProvider>
      </QueryProvider>
    </DemoProvider>
  );
}
