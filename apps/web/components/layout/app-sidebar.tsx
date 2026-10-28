"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  Bot,
  Cpu,
  Database,
  FolderKanban,
  Gauge,
  History,
  MessageSquareCode,
  FileCheck2,
  ScrollText,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Table2,
  Users,
  Workflow,
} from "lucide-react";

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMobile,
  SidebarNav,
  useSidebar,
} from "@/components/ui/sidebar";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { BrandMark } from "@/components/domain/brand-mark";
import { useSession } from "@/components/providers/session-provider";
import { t } from "@/lib/i18n/id";
import { cn } from "@/lib/utils";

type NavItem = { href: string; label: string; icon: typeof Gauge };

/**
 * Two scopes, deliberately separated.
 *
 * "Ruang kerja" is everything a tenant user can do (any role). "Administrasi
 * instalasi" is deployment-wide control (LLM provider, installation admins)
 * and is therefore NOT a tenant role: it is shown only to owner/admin because
 * those are the roles the page itself accepts, and every mutation there is
 * re-checked server-side against the installation-admin flag.
 */
const WORKSPACE_ITEMS: NavItem[] = [
  { href: "/dashboard", label: t.nav.dashboard, icon: Gauge },
  { href: "/projects", label: t.nav.projects, icon: FolderKanban },
  { href: "/sources", label: t.nav.sources, icon: Database },
  { href: "/pipelines", label: t.nav.pipelines, icon: Workflow },
  { href: "/runs", label: t.nav.runs, icon: History },
  { href: "/data", label: t.nav.explorer, icon: Table2 },
  { href: "/data-contract", label: t.nav.contract, icon: FileCheck2 },
  { href: "/agents", label: t.nav.agents, icon: Bot },
  { href: "/ask", label: t.nav.ask, icon: MessageSquareCode },
  { href: "/usage", label: t.nav.usage, icon: BarChart3 },
  { href: "/settings", label: t.nav.settings, icon: Settings },
];

const ADMIN_ITEMS: NavItem[] = [
  { href: "/admin", label: t.adminHub.title, icon: ShieldCheck },
  { href: "/admin/team", label: t.adminHub.teamTitle, icon: Users },
  { href: "/admin/quality", label: t.adminHub.qualityTitle, icon: SlidersHorizontal },
  { href: "/admin/audit", label: t.adminHub.auditTitle, icon: ScrollText },
  { href: "/admin/installation", label: t.adminHub.llmTitle, icon: Cpu },
];

/**
 * One nav tree, two presentations. The collapsed icon rail wraps each item in
 * a tooltip (hover AND focus) so shortcuts stay discoverable without
 * expanding; labels live in the DOM either way.
 */
function NavList({ items, forceCompact = false }: { items: NavItem[]; forceCompact?: boolean }) {
  const pathname = usePathname();
  const { collapsed } = useSidebar();
  const compact = collapsed || forceCompact;

  return (
    <SidebarMenu>
      {items.map((item) => {
        const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
        const Icon = item.icon;
        const button = (
          <SidebarMenuButton asChild isActive={active} className={cn(compact && "justify-center px-0")}>
            <Link href={item.href} aria-current={active ? "page" : undefined} title={compact ? item.label : undefined}>
              <Icon aria-hidden="true" />
              <span className={cn(compact && "sr-only")}>{item.label}</span>
            </Link>
          </SidebarMenuButton>
        );

        return (
          <SidebarMenuItem key={item.href}>
            {compact ? (
              <Tooltip>
                <TooltipTrigger asChild>{button}</TooltipTrigger>
                <TooltipContent side="right">{item.label}</TooltipContent>
              </Tooltip>
            ) : (
              button
            )}
          </SidebarMenuItem>
        );
      })}
    </SidebarMenu>
  );
}

/**
 * Group heading plus an explicit scope tag, so the split is never ambiguous.
 *
 * The demo badge deliberately does NOT live here. It used to be repeated on
 * every group label, and the collapsed rail repeated it again next to the
 * brand: at 64px those copies collided with the mark. The persistent banner is
 * the single place that announces demo mode; the sidebar only names scope.
 */
function ScopeLabel({
  label,
  scope,
  compact,
}: {
  label: string;
  scope: string;
  compact: boolean;
}) {
  return (
    <SidebarGroupLabel className={cn(compact && "sr-only")}>
      <span className="flex w-full items-center gap-1.5">
        <span className="truncate">{label}</span>
        <span className="shrink-0 rounded border border-border bg-muted px-1 py-px text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          {scope}
        </span>
      </span>
    </SidebarGroupLabel>
  );
}

function Brand({ forceCompact = false }: { forceCompact?: boolean }) {
  const { collapsed } = useSidebar();
  const compact = collapsed || forceCompact;
  return (
    <Link
      href="/dashboard"
      className={cn(
        "flex items-center gap-2 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        compact && "justify-center",
      )}
      title={compact ? t.app.name : undefined}
    >
      <BrandMark size={28} className="shrink-0" />
      {compact ? null : <span className="text-sm font-bold tracking-tight text-foreground">{t.app.name}</span>}
    </Link>
  );
}

/** Both scopes in one tree; `showAdmin` is the only thing that varies. */
function NavTree({ forceCompact = false, showAdmin }: { forceCompact?: boolean; showAdmin: boolean }) {
  const { collapsed } = useSidebar();
  const compact = collapsed || forceCompact;

  return (
    <>
      <SidebarGroup>
        <ScopeLabel
          label={t.nav.workspaceGroup}
          scope={t.nav.workspaceScope}
          compact={compact}
        />
        <NavList items={WORKSPACE_ITEMS} forceCompact={forceCompact} />
      </SidebarGroup>

      {showAdmin ? (
        <SidebarGroup>
          <ScopeLabel label={t.nav.adminGroup} scope={t.nav.adminScope} compact={compact} />
          <NavList items={ADMIN_ITEMS} forceCompact={forceCompact} />
        </SidebarGroup>
      ) : null}
    </>
  );
}

/**
 * Sidebar: 240px expanded (>=1024px), 64px icon rail when collapsed or on
 * 768-1023px. Below 768px it becomes an off-canvas sheet (PRD 12.5).
 */
export function AppSidebar() {
  const { collapsed } = useSidebar();
  const { session } = useSession();
  const role = session?.membership.role;
  const showAdmin = role === "owner" || role === "admin";

  return (
    <>
      <Sidebar className="hidden lg:flex">
        <SidebarHeader className={cn(collapsed && "justify-center px-0")}>
          <Brand />
        </SidebarHeader>
        <SidebarContent>
          <SidebarNav>
            <NavTree showAdmin={showAdmin} />
          </SidebarNav>
        </SidebarContent>
<SidebarFooter>
          {collapsed ? null : (
            <div className="space-y-0.5 px-2 text-[11px] text-muted-foreground">
              <p>DataLoom v1.0 | mode terang</p>
              <p className="text-[10px]">{t.app.copyrightShort}</p>
            </div>
          )}
        </SidebarFooter>
      </Sidebar>

      {/* Medium screens: always the icon rail, independent of the toggle. */}
      <Sidebar variant="icon" className="hidden md:flex lg:hidden">
        <SidebarHeader className="justify-center px-0">
          <Brand forceCompact />
        </SidebarHeader>
        <SidebarContent>
          <SidebarNav>
            <NavTree forceCompact showAdmin={showAdmin} />
          </SidebarNav>
        </SidebarContent>
      </Sidebar>

      {/* Mobile: off-canvas drawer sharing the same navigation tree. */}
      <SidebarMobile title="Navigasi utama">
        <div className="flex h-14 items-center border-b border-sidebar-border px-4">
          <Brand />
        </div>
        <div className="p-2">
          <SidebarNav>
            <NavTree showAdmin={showAdmin} />
          </SidebarNav>
        </div>
      </SidebarMobile>
    </>
  );
}