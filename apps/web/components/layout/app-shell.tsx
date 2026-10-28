"use client";

import { useEffect, useRef, useState } from "react";

import { AppSidebar } from "@/components/layout/app-sidebar";
import { AppTopbar } from "@/components/layout/app-topbar";
import { DemoBanner } from "@/components/layout/demo-banner";
import { AuthGate, CommandPalette } from "@/components/layout/auth-gate";
import { SidebarProvider } from "@/components/ui/sidebar";

/**
 * AppShell: left sidebar (240px) + topbar + content (PRD 12.4).
 * Responsible ONLY for chrome; each page renders its own header and states.
 *
 * Chrome height is MEASURED, not assumed. Pages that fill the viewport (the
 * pipeline canvas) used to hardcode `calc(100vh - 3.5rem)`; the demo banner
 * adds ~50px above the topbar, so that page became taller than the viewport and
 * the whole app grew a scrollbar the moment demo mode was on. Publishing the
 * real height as `--app-chrome` keeps those pages exact whether or not the
 * banner is present, and stays correct if the topbar ever changes.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const [searchOpen, setSearchOpen] = useState(false);
  const chromeRef = useRef<HTMLDivElement | null>(null);
  /** 3.5rem = the topbar height, used until the first measurement lands. */
  const [chromeHeight, setChromeHeight] = useState(56);

  useEffect(() => {
    const element = chromeRef.current;
    if (!element) return;

    const measure = () => {
      const next = Math.round(element.getBoundingClientRect().height);
      setChromeHeight((current) => (current === next ? current : next));
    };

    measure();

    // ResizeObserver covers the banner appearing/disappearing (enter/exit demo)
    // and the topbar wrapping on narrow screens. The listener fallback keeps
    // older browsers working without silently reverting to the hardcoded value.
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }

    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return (
    <SidebarProvider>
      <AuthGate>
        <div
          className="flex min-h-dvh bg-background"
          style={{ "--app-chrome": `${chromeHeight}px` } as React.CSSProperties}
        >
          <AppSidebar />
          <div className="flex min-w-0 flex-1 flex-col">
            <div ref={chromeRef} className="shrink-0">
              {/* Demo banner sits above the topbar so it is impossible to miss. */}
              <DemoBanner />
              <AppTopbar onOpenSearch={() => setSearchOpen(true)} />
            </div>
            <main id="main-content" className="flex-1">
              {children}
            </main>
          </div>
        </div>
        <CommandPalette open={searchOpen} onOpenChange={setSearchOpen} />
      </AuthGate>
    </SidebarProvider>
  );
}