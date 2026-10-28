"use client";

import { useQuery } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api, classifyError } from "@/lib/api";
import type { ProjectSummary } from "@/lib/types";

const STORAGE_KEY = "dataloom.activeProjectId";

interface ProjectContextValue {
  projects: ProjectSummary[];
  activeProject: ProjectSummary | null;
  activeProjectId: string | null;
  loading: boolean;
  /** Classified failure of the projects query, or null. Pages must show it. */
  error: { kind: string; message: string; requestId: string | null } | null;
  setActiveProjectId: (id: string) => void;
  refresh: () => void;
}

const ProjectContext = createContext<ProjectContextValue | null>(null);

/**
 * ProjectProvider keeps the selected project in memory + localStorage so every
 * page can scope its queries without re-asking the user.
 */
export function ProjectProvider({ children }: { children: React.ReactNode }) {
  const [activeProjectId, setActiveProjectIdState] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.projects.list(),
    staleTime: 30_000,
  });

  // Memoised: `?? []` creates a new array identity on every render, which made
  // all three effects/memos below re-run on every parent render.
  const projects = useMemo(() => query.data ?? [], [query.data]);

  // Restore the previously selected project on first load.
  useEffect(() => {
    if (activeProjectId !== null || projects.length === 0) return;
    const stored = typeof window !== "undefined" ? window.localStorage.getItem(STORAGE_KEY) : null;
    const valid = stored && projects.some((project) => project.id === stored) ? stored : null;
    setActiveProjectIdState(valid ?? projects[0]?.id ?? null);
  }, [projects, activeProjectId]);

  /**
   * Reconcile a stale selection.
   *
   * Deleting the active project used to leave its id in state AND in
   * localStorage, so `activeProject` became null and every scoped query
   * silently disabled itself. Falling back to the first remaining project (and
   * rewriting storage) keeps the workspace usable instead of looking empty.
   */
  useEffect(() => {
    if (query.isLoading || projects.length === 0) return;
    if (activeProjectId === null) return;
    if (projects.some((project) => project.id === activeProjectId)) return;

    const fallback = projects[0]?.id ?? null;
    setActiveProjectIdState(fallback);
    if (typeof window !== "undefined") {
      if (fallback) window.localStorage.setItem(STORAGE_KEY, fallback);
      else window.localStorage.removeItem(STORAGE_KEY);
    }
  }, [projects, activeProjectId, query.isLoading]);

  const setActiveProjectId = useCallback((id: string) => {
    if (typeof window !== "undefined") window.localStorage.setItem(STORAGE_KEY, id);
    setActiveProjectIdState(id);
  }, []);

  const activeProject = useMemo(
    () => projects.find((project) => project.id === activeProjectId) ?? null,
    [projects, activeProjectId],
  );

  const value = useMemo(
    () => ({
      projects,
      activeProject,
      activeProjectId: activeProject?.id ?? null,
      loading: query.isLoading,
      error: query.isError ? classifyError(query.error) : null,
      setActiveProjectId,
      refresh: () => void query.refetch(),
    }),
    [projects, activeProject, query, setActiveProjectId],
  );

  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
}

export function useProject(): ProjectContextValue {
  const context = useContext(ProjectContext);
  if (!context) throw new Error("useProject harus dipakai di dalam <ProjectProvider>");
  return context;
}
