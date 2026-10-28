"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api, ApiClientError } from "@/lib/api";
import { resetDemoDb } from "@/lib/demo/backend";
import { isDemoMode, setDemoMode } from "@/lib/demo/mode";
import type { SessionInfo } from "@/lib/types";

interface SessionContextValue {
  session: SessionInfo | null;
  loading: boolean;
  error: ApiClientError | null;
  refresh: () => Promise<void>;
  login: (email: string, password: string) => Promise<SessionInfo>;
  register: (input: {
    email: string;
    password: string;
    name: string;
    org_name: string;
  }) => Promise<SessionInfo>;
  logout: () => Promise<void>;
}

const SessionContext = createContext<SessionContextValue | null>(null);

/**
 * SessionProvider loads `/auth/me` once and exposes it to the shell.
 * A 401 is a normal signed-out state, not an error banner.
 */
export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiClientError | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const info = await api.auth.me();
      setSession(info);
    } catch (caught) {
      if (caught instanceof ApiClientError && caught.status === 401) {
        setSession(null);
      } else if (caught instanceof ApiClientError) {
        setError(caught);
      }
      setSession(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(async (email: string, password: string) => {
    const info = await api.auth.login({ email, password });
    setSession(info);
    return info;
  }, []);

  const register = useCallback(
    async (input: { email: string; password: string; name: string; org_name: string }) => {
      const info = await api.auth.register(input);
      setSession(info);
      return info;
    },
    [],
  );

  const logout = useCallback(async () => {
    try {
      await api.auth.logout();
    } finally {
      /*
       * Logging out of a demo must also END the demo. Otherwise the visitor is
       * bounced to /login while the demo flag is still set, and the next
       * "login" silently re-enters the demo with someone else's half-edited
       * fixtures — the opposite of what "logout" promises.
       */
      if (isDemoMode()) {
        setDemoMode(false);
        resetDemoDb();
      }
      setSession(null);
    }
  }, []);

  const value = useMemo(
    () => ({ session, loading, error, refresh, login, register, logout }),
    [session, loading, error, refresh, login, register, logout],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSession harus dipakai di dalam <SessionProvider>");
  return context;
}

/** Convenience: current role or "viewer" while unknown (fail closed in UI). */
export function useRole() {
  const { session } = useSession();
  return session?.membership.role ?? "viewer";
}

/** Viewer cannot mutate (FR-002); every action button checks this. */
export function useCanMutate() {
  const role = useRole();
  return role === "owner" || role === "admin" || role === "editor";
}
