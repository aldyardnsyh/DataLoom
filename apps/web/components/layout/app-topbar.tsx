"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronDown, LogOut, Search, Settings2, UserRound } from "lucide-react";
import { useRouter } from "next/navigation";
import { Fragment, useState } from "react";

import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { useSession } from "@/components/providers/session-provider";
import { useProject } from "@/components/providers/project-provider";
import { t } from "@/lib/i18n/id";
import { cn } from "@/lib/utils";

const SEGMENT_LABEL: Record<string, string> = {
  dashboard: t.nav.dashboard,
  projects: t.nav.projects,
  sources: t.nav.sources,
  pipelines: t.nav.pipelines,
  runs: t.nav.runs,
  data: t.nav.explorer,
  "data-contract": t.nav.contract,
  agents: t.nav.agents,
  ask: t.nav.ask,
  usage: t.nav.usage,
  settings: t.nav.settings,
  admin: t.nav.admin,
  team: t.adminHub.teamTitle,
  quality: t.adminHub.qualityTitle,
  audit: t.adminHub.auditTitle,
  installation: t.nav.installation,
};

/** Topbar: breadcrumb, project switcher, search, notifications, user menu. */
export function AppTopbar({ onOpenSearch }: { onOpenSearch?: () => void }) {
  const pathname = usePathname();
  const router = useRouter();
  const { session, logout } = useSession();
  const { projects, activeProject, setActiveProjectId } = useProject();
  const [logoutPending, setLogoutPending] = useState(false);

  const segments = pathname.split("/").filter(Boolean);

  // Installation settings are org-level admin work, so the entry point lives in
  // the account menu and NOT in the main sidebar (it is not a daily workflow).
  const role = session?.membership.role;
  const isOrgAdmin = role === "owner" || role === "admin";


  const initials = session
    ? session.user.name
        .split(" ")
        .map((part) => part[0])
        .slice(0, 2)
        .join("")
        .toUpperCase()
    : "?";

  const handleLogout = async () => {
    setLogoutPending(true);
    try {
      await logout();
      // Back to the public landing, which is the real front door of the app.
      router.replace("/");
    } catch {
      router.replace("/login");
    } finally {
      setLogoutPending(false);
    }
  };

  return (
    <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-2 border-b border-border bg-background px-3 md:px-4">
      <SidebarTrigger />

      <Breadcrumb className="hidden sm:block">
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink asChild>
              <Link href="/dashboard">{t.app.name}</Link>
            </BreadcrumbLink>
          </BreadcrumbItem>
          {segments.map((segment, index) => {
            const href = `/${segments.slice(0, index + 1).join("/")}`;
            const isLast = index === segments.length - 1;
            const label = SEGMENT_LABEL[segment] ?? decodeURIComponent(segment);
            // The separator is a SIBLING of the item, never a child: an <li>
            // inside an <li> is invalid HTML and triggers a hydration error.
            return (
              <Fragment key={href}>
                <BreadcrumbSeparator />
                <BreadcrumbItem>
                  {isLast ? (
                    <BreadcrumbPage>{label}</BreadcrumbPage>
                  ) : (
                    <BreadcrumbLink asChild>
                      <Link href={href}>{label}</Link>
                    </BreadcrumbLink>
                  )}
                </BreadcrumbItem>
              </Fragment>
            );
          })}
        </BreadcrumbList>
      </Breadcrumb>

      <div className="ml-auto flex items-center gap-1.5">
        {/* Project switcher */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className="max-w-[8.5rem] gap-1.5 sm:max-w-[12rem] lg:max-w-[220px]"
            >
              <span className="truncate">{activeProject?.name ?? "Pilih proyek"}</span>
              <ChevronDown className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel>Proyek</DropdownMenuLabel>
            <DropdownMenuSeparator />
            {projects.length === 0 ? (
              <DropdownMenuItem disabled>Belum ada proyek</DropdownMenuItem>
            ) : (
              projects.map((project) => (
                <DropdownMenuItem
                  key={project.id}
                  onSelect={() => setActiveProjectId(project.id)}
                  className={cn(project.id === activeProject?.id && "bg-accent text-accent-foreground")}
                >
                  <span className="truncate">{project.name}</span>
                </DropdownMenuItem>
              ))
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <Link href="/projects">{t.nav.projects}</Link>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        <Button
          variant="ghost"
          size="icon"
          onClick={onOpenSearch}
          aria-label={t.nav.search}
          title={t.nav.search}
        >
          <Search className="h-4 w-4" />
        </Button>

        {/* No notification bell on purpose: there is no notification service
            in this MVP, and a button that opens nothing is a defect (R-26). */}

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" aria-label={t.nav.account}>
              <Avatar>
                <AvatarFallback>{initials}</AvatarFallback>
              </Avatar>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="flex flex-col">
              <span className="text-sm font-semibold text-foreground">{session?.user.name ?? "Pengguna"}</span>
              <span className="text-xs font-normal text-muted-foreground">{session?.user.email ?? ""}</span>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <Link href="/settings">
                <UserRound className="h-4 w-4" aria-hidden="true" />
                {t.nav.settings}
              </Link>
            </DropdownMenuItem>
            {isOrgAdmin ? (
              <DropdownMenuItem asChild>
                <Link href="/admin/installation">
                  <Settings2 className="h-4 w-4" aria-hidden="true" />
                  {t.nav.installationSettings}
                </Link>
              </DropdownMenuItem>
            ) : null}
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={handleLogout} disabled={logoutPending}>
              <LogOut className="h-4 w-4" aria-hidden="true" />
              {t.nav.logout}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}

