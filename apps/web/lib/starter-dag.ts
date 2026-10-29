import type { PipelineDAGDTO } from "@/lib/types";

/**
 * Starter pipeline for the first run.
 *
 * The wizard must not invent column names it has never seen, so the template is
 * deliberately minimal and honest:
 *
 *   source -> validate (no rules yet) -> sink (gold)
 *
 * The validate node exists so the run produces a real gate checklist, and it
 * carries no rules because the column names are unknown until the first batch
 * arrives. The operator adds rules on the canvas once they see the data.
 */
export function starterDag(sourceId: string, datasetName: string): PipelineDAGDTO {
  return {
    schema_version: 1,
    nodes: [
      {
        id: "n_source",
        type: "source",
        label: "Sumber",
        params: { kind: "source", source_id: sourceId, batch_size_rows: 5000 },
        position: { x: 40, y: 140 },
      },
      {
        id: "n_validate",
        type: "validate",
        label: "Validasi",
        params: { kind: "validate", rules: [] },
        position: { x: 300, y: 140 },
      },
      {
        id: "n_sink",
        type: "sink",
        label: "Gold",
        params: {
          kind: "sink",
          layer: "gold",
          dataset_name: datasetName,
          write_mode: "overwrite",
        },
        position: { x: 560, y: 140 },
      },
    ],
    edges: [
      { id: "e_source_validate", from: "n_source", to: "n_validate" },
      { id: "e_validate_sink", from: "n_validate", to: "n_sink" },
    ],
    metadata: { created_by: "setup_wizard" },
  };
}

/** Gold dataset names must match the contract pattern `^[a-z0-9_]+$`. */
export function goldDatasetName(sourceName: string): string {
  const slug = sourceName
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 100);
  return `gold_${slug || "dataset"}`;
}
