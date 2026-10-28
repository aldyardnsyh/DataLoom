"use client";

import * as React from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";

import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";

export interface DataTableColumn<T> {
  key: string;
  header: string;
  /** Fixed width in px; omit for flexible columns. */
  width?: number;
  align?: "left" | "right";
  mono?: boolean;
  sortable?: boolean;
  accessor?: (row: T) => string | number | null | undefined;
  cell?: (row: T, index: number) => React.ReactNode;
}

export interface DataTableProps<T> {
  columns: DataTableColumn<T>[];
  rows: T[];
  rowKey: (row: T, index: number) => string;
  /** Height of the scroll viewport, enables virtualisation. */
  height?: number;
  loading?: boolean;
  emptyMessage?: string;
  /** Banner text when the backend clamped the result set (1.000 rows). */
  truncationNotice?: string;
  className?: string;
  rowHeight?: number;
  /** Accessible name for the table; a data table without one is unlabelled. */
  label?: string;
}

type SortState = { key: string; direction: "asc" | "desc" } | null;

/**
 * DataTable: virtualised table for large result sets (PRD 12.4).
 *
 * Virtualisation is ALWAYS on (it is a no-op cost for small tables and the only
 * way to render a 1.000-row preview smoothly). Columns can sort client-side;
 * server-side paging is not used because the API returns a clamped preview.
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  height = 480,
  loading = false,
  emptyMessage = "Belum ada data.",
  truncationNotice,
  className,
  rowHeight = 40,
  label,
}: DataTableProps<T>) {
  const parentRef = React.useRef<HTMLDivElement>(null);
  const [sort, setSort] = React.useState<SortState>(null);

  const sortedRows = React.useMemo(() => {
    if (!sort) return rows;
    const column = columns.find((candidate) => candidate.key === sort.key);
    if (!column?.accessor) return rows;
    const accessor = column.accessor;
    const copy = [...rows];
    copy.sort((left, right) => {
      const a = accessor(left);
      const b = accessor(right);
      if (a === b) return 0;
      if (a === null || a === undefined) return 1;
      if (b === null || b === undefined) return -1;
      const result = typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b));
      return sort.direction === "asc" ? result : -result;
    });
    return copy;
  }, [rows, sort, columns]);

  const virtualizer = useVirtualizer({
    count: sortedRows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => rowHeight,
    overscan: 12,
  });

  const toggleSort = (column: DataTableColumn<T>) => {
    if (!column.sortable || !column.accessor) return;
    setSort((current) => {
      if (current?.key !== column.key) return { key: column.key, direction: "asc" };
      if (current.direction === "asc") return { key: column.key, direction: "desc" };
      return null;
    });
  };

  if (loading) {
    return (
      <div className={cn("space-y-2 rounded-lg border border-border p-3", className)}>
        {Array.from({ length: 8 }).map((_, index) => (
          <Skeleton key={index} className="h-8 w-full" />
        ))}
      </div>
    );
  }

  return (
    <div className={cn("overflow-hidden rounded-lg border border-border", className)}>
      {truncationNotice ? (
        <div className="border-b border-warning/30 bg-warning/10 px-3 py-1.5 text-xs font-medium text-warning-text">
          {truncationNotice}
        </div>
      ) : null}

      <div ref={parentRef} className="overflow-auto" style={{ height }}>
        <table className="w-full border-collapse text-sm" role="table" aria-label={label}>
          <thead className="sticky top-0 z-10 bg-muted">
            <tr>
              {columns.map((column) => {
                const isSorted = sort?.key === column.key;
                const SortIcon = isSorted ? (sort.direction === "asc" ? ArrowUp : ArrowDown) : ArrowUpDown;
                return (
                  <th
                    key={column.key}
                    scope="col"
                    aria-sort={column.sortable ? (isSorted ? (sort.direction === "asc" ? "ascending" : "descending") : "none") : undefined}
                    style={column.width ? { width: column.width, minWidth: column.width } : undefined}
                    className={cn(
                      "h-9 whitespace-nowrap border-b border-border px-2 text-left text-xs font-semibold uppercase tracking-[0.05em] text-muted-foreground",
                      column.align === "right" && "text-right",
                    )}
                  >
                    {column.sortable ? (
                      <button
                        type="button"
                        onClick={() => toggleSort(column)}
                        className={cn(
                          "inline-flex items-center gap-1 rounded px-0.5 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring",
                          column.align === "right" && "flex-row-reverse",
                        )}
                      >
                        {column.header}
                        <SortIcon className="h-3 w-3" aria-hidden="true" />
                      </button>
                    ) : (
                      column.header
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sortedRows.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="px-3 py-8 text-center text-sm text-muted-foreground">
                  {emptyMessage}
                </td>
              </tr>
            ) : (
              <>
                {/* spacer row keeps the header semantics simple while virtualising */}
                <tr style={{ height: virtualizer.getVirtualItems()[0]?.start ?? 0 }} aria-hidden="true">
                  <td colSpan={columns.length} />
                </tr>
                {virtualizer.getVirtualItems().map((virtualRow) => {
                  const row = sortedRows[virtualRow.index];
                  if (!row) return null;
                  return (
                    <tr
                      key={rowKey(row, virtualRow.index)}
                      style={{ height: rowHeight }}
                      className="border-b border-border hover:bg-muted/60"
                    >
                      {columns.map((column) => (
                        <td
                          key={column.key}
                          className={cn(
                            "max-w-[420px] truncate px-2 py-2 align-middle",
                            column.align === "right" && "text-right",
                            column.mono && "font-mono text-xs",
                          )}
                        >
                          {column.cell
                            ? column.cell(row, virtualRow.index)
                            : String(column.accessor?.(row) ?? "Belum ada")}
                        </td>
                      ))}
                    </tr>
                  );
                })}
                <tr style={{ height: virtualizer.getTotalSize() - (virtualizer.getVirtualItems().at(-1)?.end ?? 0) }} aria-hidden="true">
                  <td colSpan={columns.length} />
                </tr>
              </>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

