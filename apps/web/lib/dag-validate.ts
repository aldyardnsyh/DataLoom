/**
 * Local (browser-side) DAG validator.
 *
 * The canonical semantics live in `packages/contracts/dataloom_contracts/pipelines.py`
 * (`PipelineDAG._validate_dag`). This module mirrors those rules so the editor
 * can flag problems while the user is still drawing edges instead of waiting
 * for a server round trip. The server stays authoritative: the page still calls
 * `POST /pipelines/validate` once the local check passes.
 *
 * Rules (a..h), same order as the contract:
 *   a. `nodes` must not be empty.
 *   b. node ids must be unique.
 *   c. every edge must reference existing nodes.
 *   d. no self-loop.
 *   e. exactly one entry node (no incoming edge) and it must be a `source`.
 *   f. acyclic (Kahn topological sort).
 *   g. no orphan node (every node reachable from the entry).
 *   h. at least one `sink` node.
 *
 * Every issue that belongs to a node carries `loc: ["nodes", index]`, which is
 * the format `dagToFlow` in `components/domain/pipeline-canvas.tsx` reads to
 * paint that node red. DAG-level problems (no source, no sink, no entry) use
 * `loc: []` because they belong to no single node.
 *
 * Complexity: O(V + E) time and space, no dependency added.
 */

import type { NodeType, PipelineDAGDTO, PipelineEdgeDTO, PipelineNodeDTO, ValidationIssue } from "@/lib/types";

export interface DagValidationResult {
  valid: boolean;
  issues: ValidationIssue[];
}

/** Human labels for node types, used inside Indonesian messages. */
const NODE_TYPE_LABEL: Record<NodeType, string> = {
  source: "Sumber",
  validate: "Validasi",
  clean: "Pembersihan",
  transform: "Transformasi",
  aggregate: "Agregasi",
  sink: "Gudang Data",
};

/** `label ?? id`, trimmed, so messages never read like a machine dump. */
function nodeName(node: PipelineNodeDTO): string {
  const label = typeof node.label === "string" ? node.label.trim() : "";
  return label.length > 0 ? label : node.id;
}

function typeLabel(type: NodeType): string {
  return NODE_TYPE_LABEL[type] ?? type;
}

/** Issue attached to a specific node, so the canvas can highlight it. */
function nodeIssue(node: PipelineNodeDTO, index: number, msg: string): ValidationIssue {
  return { loc: ["nodes", index], msg, node_ref: node.id };
}

/** Issue attached to the whole DAG, not to one node. */
function dagIssue(msg: string): ValidationIssue {
  return { loc: [], msg };
}

/** Keeps long node listings readable inside one console line. */
function formatNodeList(names: string[], max = 4): string {
  if (names.length <= max) return names.join(", ");
  return `${names.slice(0, max).join(", ")}, dan ${names.length - max} node lain`;
}

/** Iterative DFS (no recursion, so a long chain cannot overflow the stack). */
function reachableFrom(startId: string, adjacency: Map<string, string[]>): Set<string> {
  const reachable = new Set<string>();
  const stack: string[] = [startId];
  while (stack.length > 0) {
    const current = stack.pop();
    if (current === undefined || reachable.has(current)) continue;
    reachable.add(current);
    for (const next of adjacency.get(current) ?? []) stack.push(next);
  }
  return reachable;
}

/**
 * Nodes that belong to a cycle formed by TWO OR MORE nodes, i.e. members of a
 * strongly connected component of size > 1. Implemented as an iterative Tarjan
 * so a long chain cannot overflow the call stack; still O(V + E).
 *
 * Knowing the exact members matters: nodes merely *downstream* of a cycle also
 * never get scheduled, but they are not part of the loop and must not be blamed
 * for it. Self-loops are single-node components, so they are reported by rule
 * (d) and handled separately by the caller.
 */
function nodesOnCycles(nodeIds: string[], adjacency: Map<string, string[]>): Set<string> {
  const discovery = new Map<string, number>();
  const lowLink = new Map<string, number>();
  const onStack = new Set<string>();
  const stack: string[] = [];
  const onCycle = new Set<string>();
  let counter = 0;

  for (const root of nodeIds) {
    if (discovery.has(root)) continue;

    discovery.set(root, counter);
    lowLink.set(root, counter);
    counter += 1;
    stack.push(root);
    onStack.add(root);

    const frames: { id: string; children: string[]; next: number }[] = [
      { id: root, children: adjacency.get(root) ?? [], next: 0 },
    ];

    while (frames.length > 0) {
      const frame = frames[frames.length - 1];
      if (!frame) break;

      if (frame.next < frame.children.length) {
        const child = frame.children[frame.next];
        frame.next += 1;
        if (child === undefined) continue;

        if (!discovery.has(child)) {
          discovery.set(child, counter);
          lowLink.set(child, counter);
          counter += 1;
          stack.push(child);
          onStack.add(child);
          frames.push({ id: child, children: adjacency.get(child) ?? [], next: 0 });
        } else if (onStack.has(child)) {
          lowLink.set(frame.id, Math.min(lowLink.get(frame.id) ?? 0, discovery.get(child) ?? 0));
        }
        continue;
      }

      // Every child of this frame has been processed: close it.
      frames.pop();
      const frameLow = lowLink.get(frame.id) ?? 0;

      if (frameLow === discovery.get(frame.id)) {
        // `frame.id` is the root of one strongly connected component.
        const component: string[] = [];
        while (stack.length > 0) {
          const popped = stack.pop();
          if (popped === undefined) break;
          onStack.delete(popped);
          component.push(popped);
          if (popped === frame.id) break;
        }
        if (component.length > 1) {
          for (const id of component) onCycle.add(id);
        }
      }

      const parent = frames[frames.length - 1];
      if (parent) {
        lowLink.set(parent.id, Math.min(lowLink.get(parent.id) ?? 0, frameLow));
      }
    }
  }

  return onCycle;
}

export function validateDagLocally(dag: PipelineDAGDTO): DagValidationResult {
  // Defensive: the canvas is uncontrolled and `snapshot()` can be read before
  // the first commit, so never assume the arrays are there.
  const nodes: PipelineNodeDTO[] = Array.isArray(dag.nodes) ? dag.nodes : [];
  const edges: PipelineEdgeDTO[] = Array.isArray(dag.edges) ? dag.edges : [];
  const issues: ValidationIssue[] = [];

  // (a) a pipeline without nodes cannot be checked any further.
  if (nodes.length === 0) {
    return {
      valid: false,
      issues: [
        dagIssue("Pipeline belum punya node. Tambahkan minimal satu node Sumber dan satu node Gudang Data."),
      ],
    };
  }

  // (b) unique ids. Every check below indexes nodes by id, so duplicates are
  // fatal: report them and stop, exactly like the contract does.
  const indexById = new Map<string, number>();
  const nameById = new Map<string, string>();
  nodes.forEach((node, index) => {
    const firstIndex = indexById.get(node.id);
    if (firstIndex !== undefined) {
      issues.push({
        loc: ["nodes", index],
        msg: `Id node '${node.id}' dipakai lebih dari satu kali (pertama di posisi ${firstIndex + 1}). Setiap node harus punya id yang unik.`,
        node_ref: node.id,
      });
      return;
    }
    indexById.set(node.id, index);
    nameById.set(node.id, nodeName(node));
  });
  if (issues.length > 0) return { valid: false, issues };

  // (c) edges must point at real nodes and (d) must not be self-loops. The
  // adjacency list is built in the same pass so the whole walk stays O(V + E).
  const adjacency = new Map<string, string[]>();
  const indegree = new Map<string, number>();
  nodes.forEach((node) => {
    adjacency.set(node.id, []);
    indegree.set(node.id, 0);
  });

  const selfLoopIds = new Set<string>();
  edges.forEach((edge, index) => {
    const fromIndex = indexById.get(edge.from);
    const toIndex = indexById.get(edge.to);
    const position = index + 1;

    if (fromIndex === undefined) {
      issues.push({
        loc: ["edges", index, "from"],
        msg: `Edge ke-${position} berangkat dari node '${edge.from}' yang tidak ada di kanvas. Hapus edge ini atau tambahkan node tersebut lebih dulu.`,
      });
    }
    if (toIndex === undefined) {
      issues.push({
        loc: ["edges", index, "to"],
        msg: `Edge ke-${position} menuju node '${edge.to}' yang tidak ada di kanvas. Hapus edge ini atau tambahkan node tersebut lebih dulu.`,
      });
    }
    if (edge.from === edge.to && fromIndex !== undefined) {
      const node = nodes[fromIndex];
      if (node) {
        selfLoopIds.add(node.id);
        issues.push(
          nodeIssue(
            node,
            fromIndex,
            `Node '${nodeName(node)}' terhubung ke dirinya sendiri pada edge ke-${position}. Hapus edge tersebut karena alur harus maju ke node lain.`,
          ),
        );
      }
    }

    // Dangling edges are skipped so the graph walk never sees unknown ids.
    if (fromIndex === undefined || toIndex === undefined) return;
    const next = adjacency.get(edge.from);
    if (next) next.push(edge.to);
    indegree.set(edge.to, (indegree.get(edge.to) ?? 0) + 1);
  });

  // (e) exactly one entry node (no incoming edge), and it must be a source.
  const hasIncoming = new Set<string>();
  edges.forEach((edge) => hasIncoming.add(edge.to));
  const entries: { node: PipelineNodeDTO; index: number }[] = [];
  nodes.forEach((node, index) => {
    if (!hasIncoming.has(node.id)) entries.push({ node, index });
  });

  if (!nodes.some((node) => node.type === "source")) {
    issues.push(
      dagIssue("Pipeline belum punya node bertipe Sumber sebagai titik masuk. Tambahkan satu node Sumber."),
    );
  }

  if (entries.length === 0) {
    issues.push(
      dagIssue("Semua node punya edge masuk sehingga tidak ada titik awal. Kemungkinan edge membentuk siklus."),
    );
  } else if (entries.length === 1) {
    const only = entries[0];
    if (only && only.node.type !== "source") {
      issues.push(
        nodeIssue(
          only.node,
          only.index,
          `Titik masuk pipeline adalah node '${nodeName(only.node)}' bertipe ${typeLabel(only.node.type)}, padahal titik masuk harus node Sumber. Tambahkan node Sumber di depan alur ini.`,
        ),
      );
    }
  } else {
    // When exactly one entry is a source, that one is clearly the intended
    // entry: flag only the extras, so the fix is obvious.
    const sourceEntries = entries.filter((entry) => entry.node.type === "source");
    const flagged = sourceEntries.length === 1 ? entries.filter((entry) => entry.node.type !== "source") : entries;
    const onlyExtras = flagged.length !== entries.length;
    for (const entry of flagged) {
      issues.push(
        nodeIssue(
          entry.node,
          entry.index,
          onlyExtras
            ? `Node '${nodeName(entry.node)}' tidak punya edge masuk sehingga menjadi titik masuk tambahan. Hubungkan node ini dari node sebelumnya atau hapus node ini.`
            : `Node '${nodeName(entry.node)}' tidak punya edge masuk sehingga menjadi salah satu dari ${entries.length} titik masuk. Pipeline hanya boleh punya satu titik masuk dan titik itu harus node Sumber.`,
        ),
      );
    }
  }

  // (f) acyclic, using Kahn's algorithm. Nodes still holding a positive
  // indegree when the queue drains can never be scheduled.
  const remainingIndegree = new Map(indegree);
  const ready: string[] = [];
  for (const [id, degree] of remainingIndegree) {
    if (degree === 0) ready.push(id);
  }
  let scheduled = 0;
  while (ready.length > 0) {
    const current = ready.pop();
    if (current === undefined) break;
    scheduled += 1;
    for (const next of adjacency.get(current) ?? []) {
      const degree = (remainingIndegree.get(next) ?? 0) - 1;
      remainingIndegree.set(next, degree);
      if (degree === 0) ready.push(next);
    }
  }

  const blockedIds: string[] = [];
  if (scheduled !== nodes.length) {
    for (const [id, degree] of remainingIndegree) {
      if (degree > 0) blockedIds.push(id);
    }
  }

  const onCycle = blockedIds.length > 0 ? nodesOnCycles(nodes.map((node) => node.id), adjacency) : new Set<string>();
  /** Nodes already blamed by the cycle pass, so (g) does not repeat them. */
  const flaggedByCycle = new Set<string>();

  if (blockedIds.length > 0) {
    const cycleNames = [...onCycle].map((id) => nameById.get(id) ?? id);
    const cycleListing = formatNodeList(cycleNames);
    // A graph whose only loops are self-loops has no multi-node cycle to name.
    const loopPhrase =
      cycleListing.length > 0
        ? `alur sebelumnya membentuk siklus (${cycleListing})`
        : "ada edge yang menghubungkan node ke dirinya sendiri";

    for (const id of blockedIds) {
      const index = indexById.get(id);
      const node = index === undefined ? undefined : nodes[index];
      if (!node || index === undefined) continue;

      if (selfLoopIds.has(id)) {
        // Rule (d) already explained this node; do not blame it twice.
        flaggedByCycle.add(id);
        continue;
      }

      if (onCycle.has(id)) {
        const others = cycleNames.filter((name) => name !== (nameById.get(id) ?? id));
        const partners = others.length > 0 ? formatNodeList(others) : "node itu sendiri";
        issues.push(
          nodeIssue(
            node,
            index,
            `Node '${nodeName(node)}' terlibat siklus dengan ${partners}. Hapus minimal satu edge pada siklus agar alur menjadi asiklik.`,
          ),
        );
        flaggedByCycle.add(id);
      } else {
        issues.push(
          nodeIssue(
            node,
            index,
            `Node '${nodeName(node)}' tidak akan pernah dijalankan karena ${loopPhrase}. Hapus minimal satu edge pada siklus tersebut.`,
          ),
        );
        flaggedByCycle.add(id);
      }
    }
  }

  // (g) orphans: every node must be reachable from the single entry. Only
  // meaningful with exactly one entry, because with zero or several entries
  // there is no "the" entry to walk from and (e) already explained that. Nodes
  // the cycle pass already explained are skipped so one bad edge produces one
  // message per node.
  const entry = entries.length === 1 ? entries[0] : undefined;
  if (entry) {
    const reachable = reachableFrom(entry.node.id, adjacency);
    const entryName = nodeName(entry.node);
    nodes.forEach((node, index) => {
      if (reachable.has(node.id) || flaggedByCycle.has(node.id)) return;
      issues.push(
        nodeIssue(
          node,
          index,
          `Node '${nodeName(node)}' tidak terhubung ke titik masuk '${entryName}' sehingga tidak akan pernah dijalankan. Hubungkan dari node sebelumnya atau hapus node ini.`,
        ),
      );
    });
  }

  // (h) at least one sink, and every sink must actually be fed by an edge.
  if (!nodes.some((node) => node.type === "sink")) {
    issues.push(
      dagIssue("Pipeline belum punya node Gudang Data (sink) sebagai output. Tambahkan minimal satu node Gudang Data."),
    );
  } else if (nodes.length > 1) {
    /*
     * Mirrors contract rule (7): a sink needs an INCOMING edge. This used to
     * be skipped here because the server rule was itself wrong (it tested
     * outgoing edges), so local and server disagreed in the worst way: the
     * canvas said valid and the API rejected the save.
     */
    nodes.forEach((node, index) => {
      if (node.type !== "sink") return;
      if (hasIncoming.has(node.id)) return;
      issues.push(
        nodeIssue(
          node,
          index,
          `Node Gudang Data ${nodeName(node)} belum punya input. Hubungkan dari node sebelumnya.`,
        ),
      );
    });
  }

  return { valid: issues.length === 0, issues };
}
