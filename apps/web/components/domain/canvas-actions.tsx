"use client";

import { createContext, useContext } from "react";

/**
 * Actions a node/edge may trigger inside the canvas.
 *
 * Kept in React context instead of `node.data` because putting callbacks in
 * node data forces a brand-new node object on every render, which makes React
 * Flow rebuild its node store each time and silently detaches d3 drag handlers
 * (nodes become impossible to move). Context keeps node objects stable.
 */
export interface CanvasActions {
  readOnly: boolean;
  /**
   * How many nodes are selected right now. NodeToolbar is suppressed when this
   * is above 1, so a multi-select never stacks three identical toolbars.
   */
  selectionCount: number;
  duplicateNode: (nodeId: string) => void;
  deleteNode: (nodeId: string) => void;
  insertNodeOnEdge: (edgeId: string) => void;
  deleteEdge: (edgeId: string) => void;
}

const CanvasActionsContext = createContext<CanvasActions | null>(null);

export const CanvasActionsProvider = CanvasActionsContext.Provider;

export function useCanvasActions(): CanvasActions | null {
  return useContext(CanvasActionsContext);
}
