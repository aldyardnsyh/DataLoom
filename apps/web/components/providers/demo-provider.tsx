"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { usePathname } from "next/navigation";

import { resetDemoDb } from "@/lib/demo/backend";
import { isDemoMode, setDemoMode } from "@/lib/demo/mode";

interface DemoContextValue {
  /** True when every API call is served by the in-browser demo backend. */
  demo: boolean;
  /** Leave demo mode and return to the public landing page. */
  exitDemo: () => void;
  /** Throw away demo mutations and start over from the fixtures. */
  resetDemo: () => void;
}

const DemoContext = createContext<DemoContextValue | null>(null);

/**
 * DemoProvider owns the demo lifecycle for the authenticated app segment.
 *
 * It reads `window.location.search` directly instead of `useSearchParams`: the
 * latter forces the whole route into dynamic rendering (and needs a Suspense
 * boundary), while the demo flag is only ever needed on the client after
 * hydration. That keeps every page statically renderable.
 */
export function DemoProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [demo, setDemo] = useState(false);

  useEffect(() => {
    // Pick up a shared `?mode=demo` link (or a reload while in demo).
    if (typeof window !== "undefined" && new URLSearchParams(window.location.search).get("mode") === "demo") {
      if (!isDemoMode()) setDemoMode(true);
    }
    setDemo(isDemoMode());
  }, [pathname]);

  const exitDemo = useCallback(() => {
    setDemoMode(false);
    resetDemoDb();
    setDemo(false);
    /*
     * A full navigation, not router.push: the react-query cache still holds
     * demo fixtures (projects, members, usage) and `dataloom.activeProjectId`
     * may point at a demo project id. Both would leak into the next real
     * session. Reloading is the only way to drop every in-memory cache without
     * threading a reset through each provider.
     */
    try {
      window.localStorage.removeItem("dataloom.activeProjectId");
    } catch {
      // Storage can be unavailable; the reload still clears the query cache.
    }
    window.location.assign("/");
  }, []);

  const resetDemo = useCallback(() => {
    resetDemoDb();
    // Remount so every react-query entry refetches from restored fixtures
    // instead of replaying the mutations held in the cache.
    window.location.reload();
  }, []);

  const value = useMemo(() => ({ demo, exitDemo, resetDemo }), [demo, exitDemo, resetDemo]);

  useEffect(() => {
    if (!demo) return;
    const previous = document.title;
    document.title = `${previous} (Demo)`;
    return () => {
      document.title = previous;
    };
  }, [demo, pathname]);

  return <DemoContext.Provider value={value}>{children}</DemoContext.Provider>;
}

export function useDemo(): DemoContextValue {
  const context = useContext(DemoContext);
  if (!context) throw new Error("useDemo harus dipakai di dalam <DemoProvider>");
  return context;
}