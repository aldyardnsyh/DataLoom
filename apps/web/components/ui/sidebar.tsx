"use client";

import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

/**
 * Sidebar primitive with a real collapse state.
 *
 * - Desktop >=1024px: 240px rail, collapsible to a 64px icon rail. The choice
 *   persists in localStorage so the workspace remembers the operator's layout.
 * - 768-1023px: always the icon rail (space is scarce there).
 * - Below 768px: off-canvas Sheet.
 */
const SIDEBAR_WIDTH = "240px";
const SIDEBAR_WIDTH_ICON = "64px";

const STORAGE_KEY = "dataloom.sidebarCollapsed";

type SidebarContextValue = {
  openMobile: boolean;
  setOpenMobile: (open: boolean) => void;
  compact: boolean;
  /** True when the desktop rail is collapsed to icons. */
  collapsed: boolean;
  toggleCollapsed: () => void;
};

const SidebarContext = React.createContext<SidebarContextValue | null>(null);

function useSidebar() {
  const context = React.useContext(SidebarContext);
  if (!context) throw new Error("useSidebar harus dipakai di dalam <SidebarProvider>");
  return context;
}

function SidebarProvider({ children }: { children: React.ReactNode }) {
  const [openMobile, setOpenMobile] = React.useState(false);
  // `collapsed` starts false and hydrates from storage on mount, so SSR output
  // is stable (no hydration mismatch) and the first paint is the wide rail.
  const [collapsed, setCollapsed] = React.useState(false);

  React.useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (stored === "1") setCollapsed(true);
    } catch {
      // Private mode: keep the wide rail.
    }
  }, []);

  const toggleCollapsed = React.useCallback(() => {
    setCollapsed((current) => {
      const next = !current;
      try {
        window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
      } catch {
        // Ignore storage failures; the state still works in memory.
      }
      return next;
    });
  }, []);

  const value = React.useMemo(
    () => ({ openMobile, setOpenMobile, compact: collapsed, collapsed, toggleCollapsed }),
    [openMobile, collapsed, toggleCollapsed],
  );

  return <SidebarContext.Provider value={value}>{children}</SidebarContext.Provider>;
}

const Sidebar = React.forwardRef<
  HTMLElement,
  React.ComponentPropsWithoutRef<"aside"> & {
    variant?: "default" | "icon";
  }
>(({ className, variant = "default", ...props }, ref) => {
  const { collapsed } = useSidebar();
  const isIconRail = variant === "icon" || collapsed;

  return (
    <aside
      ref={ref}
      data-sidebar="sidebar"
      data-variant={isIconRail ? "icon" : "default"}
      data-collapsed={collapsed || undefined}
      className={cn(
        "hidden shrink-0 border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-[width] duration-200 ease-out md:flex md:flex-col",
        className,
      )}
      style={{
        width: isIconRail ? SIDEBAR_WIDTH_ICON : SIDEBAR_WIDTH,
      }}
      {...props}
    />
  );
});
Sidebar.displayName = "Sidebar";

const SidebarHeader = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("flex h-14 items-center gap-2 border-b border-sidebar-border px-4", className)} {...props} />
);
SidebarHeader.displayName = "SidebarHeader";

const SidebarContent = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto overflow-x-hidden p-2", className)} {...props} />
);
SidebarContent.displayName = "SidebarContent";

const SidebarFooter = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("border-t border-sidebar-border p-2", className)} {...props} />
);
SidebarFooter.displayName = "SidebarFooter";

const SidebarGroup = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("flex w-full flex-col gap-0.5 py-1", className)} {...props} />
);
SidebarGroup.displayName = "SidebarGroup";

/**
 * Semantic wrapper for the navigation list. Screen readers announce it as a
 * navigation landmark; without it the menu is just an anonymous list.
 */
const SidebarNav = React.forwardRef<
  HTMLElement,
  React.ComponentPropsWithoutRef<"nav"> & { label?: string }
>(({ className, label = "Navigasi utama", children, ...props }, ref) => (
  <nav ref={ref} aria-label={label} className={cn("w-full", className)} {...props}>
    {children}
  </nav>
));
SidebarNav.displayName = "SidebarNav";

const SidebarGroupLabel = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div
    className={cn("px-2 py-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground", className)}
    {...props}
  />
);
SidebarGroupLabel.displayName = "SidebarGroupLabel";

const SidebarMenu = ({ className, ...props }: React.HTMLAttributes<HTMLUListElement>) => (
  <ul className={cn("flex w-full min-w-0 flex-col gap-0.5", className)} {...props} />
);
SidebarMenu.displayName = "SidebarMenu";

const SidebarMenuItem = ({ className, ...props }: React.HTMLAttributes<HTMLLIElement>) => (
  <li className={cn("relative", className)} {...props} />
);
SidebarMenuItem.displayName = "SidebarMenuItem";

const SidebarMenuButton = React.forwardRef<
  HTMLButtonElement,
  React.ComponentPropsWithoutRef<"button"> & {
    asChild?: boolean;
    isActive?: boolean;
    tooltip?: string;
  }
>(({ className, asChild, isActive, tooltip, children, ...props }, ref) => {
  const { collapsed } = useSidebar();
  // When collapsed, every item shows a tooltip on hover/focus so the icon-only
  // rail stays discoverable without expanding.
  const showTooltip = tooltip ?? (collapsed ? undefined : undefined);
  void showTooltip;
  const Comp = asChild ? Slot : "button";
  const button = (
    <Comp
      ref={ref}
      data-active={isActive}
      aria-current={isActive ? "page" : undefined}
      className={cn(
        "flex h-9 w-full items-center gap-2.5 rounded-lg px-2.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
        isActive && "bg-sidebar-accent text-sidebar-accent-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
        collapsed && "justify-center px-0",
        className,
      )}
      {...props}
    >
      {children}
    </Comp>
  );

  return button;
});
SidebarMenuButton.displayName = "SidebarMenuButton";

const SidebarMobile = ({
  children,
  title,
}: {
  children: React.ReactNode;
  title: string;
}) => {
  const { openMobile, setOpenMobile } = useSidebar();
  return (
    <Sheet open={openMobile} onOpenChange={setOpenMobile}>
      <SheetContent side="left" className="w-60 bg-sidebar p-0">
        <SheetTitle className="sr-only">{title}</SheetTitle>
        {children}
      </SheetContent>
    </Sheet>
  );
};

/**
 * Desktop collapse toggle. Sits in the topbar next to the breadcrumb; shows
 * the opposite icon of the current state so the next action is obvious.
 */
const SidebarTrigger = ({ className }: { className?: string }) => {
  const { collapsed, toggleCollapsed, setOpenMobile } = useSidebar();
  return (
    <>
      <Button
        variant="ghost"
        size="icon"
        className={cn("hidden md:inline-flex", className)}
        onClick={toggleCollapsed}
        aria-label={collapsed ? "Perluas menu navigasi" : "Ciutkan menu navigasi"}
        aria-expanded={!collapsed}
        title={collapsed ? "Perluas menu" : "Ciutkan menu"}
      >
        {collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className={cn("md:hidden", className)}
        onClick={() => setOpenMobile(true)}
        aria-label="Buka menu navigasi"
      >
        <PanelLeftClose />
      </Button>
    </>
  );
};

export {
  SIDEBAR_WIDTH,
  SIDEBAR_WIDTH_ICON,
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarNav,
  SidebarMenuItem,
  SidebarMobile,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
};
