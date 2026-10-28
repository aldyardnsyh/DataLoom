/**
 * Demo mode flag.
 *
 * Why a module-level flag and not React state: `apiFetch` is a plain function
 * called from many places (react-query, event handlers, hooks) and it must know
 * synchronously whether the request may touch the network. React context is not
 * reachable from there, so the flag lives here and the provider mirrors it into
 * the component tree for rendering.
 *
 * The flag is mirrored into `sessionStorage` (NOT `localStorage`) so the demo
 * ends when the tab closes. A visitor who forgets they were in demo mode must
 * never come back to a half-empty dashboard on a later visit.
 */

const STORAGE_KEY = "dataloom.demo";

let active = false;

/**
 * Read the persisted flag once on module init (browser only).
 *
 * This MUST run at import time, not in a React effect: `apiFetch` is called by
 * the session query during the first render, and child effects run before
 * parent effects, so an effect-based flag would let that first `/auth/me`
 * reach the network. The shared-link `?mode=demo` case is covered here too, so
 * a reloaded or pasted demo URL never touches the real backend.
 */
if (typeof window !== "undefined") {
  try {
    const fromQuery = new URLSearchParams(window.location.search).get("mode") === "demo";
    active = fromQuery || window.sessionStorage.getItem(STORAGE_KEY) === "1";
    if (active) window.sessionStorage.setItem(STORAGE_KEY, "1");
  } catch {
    // sessionStorage can throw in private mode with cookies blocked; demo mode
    // then simply does not survive a reload, which is the safe default.
    active = false;
  }
}

export function isDemoMode(): boolean {
  return active;
}

export function setDemoMode(value: boolean): void {
  active = value;
  if (typeof window === "undefined") return;
  try {
    if (value) window.sessionStorage.setItem(STORAGE_KEY, "1");
    else window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Non-fatal: the in-memory flag is what gates the network.
  }
}
