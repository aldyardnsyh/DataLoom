"use client";

import { useEffect, useRef, useState } from "react";

import { api, DEMO_STREAM_PREFIX } from "@/lib/api";
import type { GateChecklist, RunNode, RunStatus, SseEvent } from "@/lib/types";

export type ConnectionState = "connecting" | "live" | "disconnected";

export interface LiveLogLine {
  seq: number;
  at: string;
  node_id: string;
  level: "debug" | "info" | "warning" | "error";
  message: string;
}

export interface LiveRunState {
  connection: ConnectionState;
  status: RunStatus | null;
  nodes: Map<string, Partial<RunNode> & { node_id: string }>;
  /** Live gate checklist, when the backend recomputes it mid-run. */
  gate: GateChecklist | null;
  events: { seq: number; at: string; text: string }[];
  logs: LiveLogLine[];
}

const TERMINAL: RunStatus[] = ["succeeded", "failed", "cancelled", "needs_attention"];

const INITIAL_STATE: LiveRunState = {
  connection: "connecting",
  status: null,
  nodes: new Map(),
  gate: null,
  events: [],
  logs: [],
};

/**
 * useRunStream subscribes to `GET /v1/runs/{id}/events`.
 *
 * The contract (`dataloom_contracts/runs.py`) emits exactly five event types:
 * `node_status`, `node_log`, `gate`, `run_status`, `heartbeat`. There is **no**
 * `done` event: a stream ends when a terminal `run_status` arrives, and this
 * hook closes the connection on it.
 *
 * Guarantees:
 * 1. **No stale merge.** Events that arrive while the connection is known to be
 *    disconnected are ignored, so a silent drop never looks like fresh data.
 * 2. **No duplicates.** `seq` is monotonic per run; anything at or below the
 *    last seen sequence is dropped (EventSource replays after reconnect).
 * 3. **No bleed between runs.** State is reset whenever `runId`/`enabled`
 *    changes, so run A's nodes never appear on run B's page.
 */
export function useRunStream(runId: string | null, enabled: boolean) {
  const [state, setState] = useState<LiveRunState>(INITIAL_STATE);

  const sourceRef = useRef<EventSource | null>(null);
  /** Highest `seq` accepted so far; -1 means "nothing yet". */
  const lastSeqRef = useRef(-1);
  /** Mirrors `state.connection` for the event handler (no stale closure). */
  const connectionRef = useRef<ConnectionState>("connecting");

  useEffect(() => {
    if (!runId || !enabled) return;
    if (typeof window === "undefined") return;

    // Fresh run (or a re-subscribe): forget the previous run's data entirely.
    lastSeqRef.current = -1;
    connectionRef.current = "connecting";
    setState(INITIAL_STATE);

    const url = api.runs.eventsUrl(runId);

    /*
     * Demo mode: there is no worker producing events, and opening an
     * EventSource would put the network back in play. Report "live" and stop —
     * the Run Monitor already renders the node timeline from the demo
     * `GET /runs/{id}` payload, so the screen is complete without a stream.
     */
    if (url.startsWith(DEMO_STREAM_PREFIX)) {
      connectionRef.current = "live";
      setState((current) => ({ ...current, connection: "live" }));
      return;
    }

    const source = new EventSource(url, { withCredentials: true });
    sourceRef.current = source;

    const close = () => {
      source.close();
      sourceRef.current = null;
    };

    const handle = (raw: MessageEvent<string>) => {
      let payload: SseEvent;
      try {
        payload = JSON.parse(raw.data) as SseEvent;
      } catch {
        return;
      }

      // (2) Replayed/duplicate events after a reconnect are not new facts.
      if (typeof payload.seq === "number") {
        if (payload.seq <= lastSeqRef.current) return;
        lastSeqRef.current = payload.seq;
      }

      // (1) A disconnected stream must not present stale data as current.
      if (connectionRef.current === "disconnected") return;

      if (payload.event === "node_status") {
        setState((current) => {
          const nodes = new Map(current.nodes);
          nodes.set(payload.node_id, {
            node_id: payload.node_id,
            node_type: payload.node_type,
            status: payload.status,
            retry_count: payload.retry_count,
            duration_ms: payload.duration_ms,
            rows_in: payload.rows_in ?? 0,
            rows_out: payload.rows_out ?? 0,
            rows_quarantined: payload.rows_quarantined ?? 0,
            error_code: payload.error_code,
            error_message: payload.error_message,
          });
          return {
            ...current,
            nodes,
            events: appendEvent(current.events, payload.seq, payload.emitted_at, `${payload.node_id} → ${payload.status}`),
          };
        });
        return;
      }

      if (payload.event === "node_log") {
        setState((current) => ({
          ...current,
          logs: [
            ...current.logs,
            {
              seq: payload.seq,
              at: payload.emitted_at,
              node_id: payload.node_id,
              level: payload.level,
              message: payload.message,
            },
          ].slice(-200),
        }));
        return;
      }

      if (payload.event === "gate") {
        setState((current) => ({ ...current, gate: payload.checklist }));
        return;
      }

      if (payload.event === "run_status") {
        const terminal = TERMINAL.includes(payload.status);
        setState((current) => ({
          ...current,
          status: payload.status,
          events: appendEvent(current.events, payload.seq, payload.emitted_at, `run → ${payload.status}`),
        }));
        // The contract has no `done` event: a terminal status IS the end.
        if (terminal) close();
        return;
      }

      // heartbeat
      setState((current) => current);
    };

    source.addEventListener("node_status", handle as EventListener);
    source.addEventListener("node_log", handle as EventListener);
    source.addEventListener("gate", handle as EventListener);
    source.addEventListener("run_status", handle as EventListener);
    source.addEventListener("heartbeat", handle as EventListener);

    source.onopen = () => {
      connectionRef.current = "live";
      setState((current) => ({ ...current, connection: "live" }));
    };

    source.onerror = () => {
      connectionRef.current = "disconnected";
      setState((current) => ({ ...current, connection: "disconnected" }));
      if (source.readyState === EventSource.CLOSED) close();
    };

    return () => {
      source.removeEventListener("node_status", handle as EventListener);
      source.removeEventListener("node_log", handle as EventListener);
      source.removeEventListener("gate", handle as EventListener);
      source.removeEventListener("run_status", handle as EventListener);
      source.removeEventListener("heartbeat", handle as EventListener);
      close();
    };
  }, [runId, enabled]);

  const isTerminal = state.status !== null && TERMINAL.includes(state.status);
  return { ...state, isTerminal };
}

function appendEvent(
  events: LiveRunState["events"],
  seq: number,
  at: string,
  text: string,
): LiveRunState["events"] {
  const next = [...events, { seq, at, text }];
  return next.slice(-200);
}