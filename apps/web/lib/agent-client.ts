"use client";

import { api } from "@/lib/api";
import type { SqlAnswer } from "@/lib/types";

function csrfFromCookie(): string {
  if (typeof document === "undefined") return "";
  return (
    document.cookie
      .split("; ")
      .find((part) => part.startsWith("dl_csrf="))
      ?.split("=")[1] ?? ""
  );
}

/**
 * CSRF header helper shared by every POST that is not a simple JSON body
 * (currently only endpoints routed through `apiFetch`).
 */
export function csrfHeaders(): Record<string, string> {
  const token = csrfFromCookie();
  return token ? { "X-CSRF-Token": decodeURIComponent(token) } : {};
}

/**
 * Apply approved agent proposals to a pipeline (creates a new version).
 *
 * Routed through `api.agents.apply` rather than a hand-written `fetch`: that
 * hand-written call used to bypass the demo short-circuit in `apiFetch` and
 * would have hit the real backend while the UI was in demo mode.
 */
export async function applyAgentProposals(agentRunId: string, pipelineId: string): Promise<{
  pipeline_id: string;
  pipeline_version_id: string;
  version: number;
  applied_proposals: number;
}> {
  return api.agents.apply(agentRunId, pipelineId);
}

/** Extracted for reuse by the Ask Data page (Phase 5). */
export type AskResult = { agent_run: { id: string; thread_id: string }; answer: SqlAnswer };

export async function askData(projectId: string, question: string): Promise<AskResult> {
  return api.agents.ask(projectId, { question });
}
