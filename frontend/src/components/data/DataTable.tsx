import type { ReactNode } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { cn } from "@/utils/cn";
import { TableSkeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";

export interface DataTableColumn<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  sortable?: boolean;
  className?: string;
  headerClassName?: string;
}

interface DataTableProps<T> {
  columns: DataTableColumn<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  isLoading?: boolean;
  isError?: boolean;
  onRetry?: () => void;
  emptyTitle?: string;
  emptyDescription?: string;
  sortKey?: string;
  sortDir?: "asc" | "desc";
  onSortChange?: (key: string) => void;
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  isLoading,
  isError,
  onRetry,
  emptyTitle = "No results",
  emptyDescription = "Try adjusting your filters or search terms.",
  sortKey,
  sortDir,
  onSortChange,
}: DataTableProps<T>) {
  return (
    <div className="overflow-x-auto scrollbar-thin">
      <table className="w-full min-w-[720px] border-collapse text-left text-sm">
        <thead className="sticky top-0 z-10 bg-slate-50">
          <tr className="border-b border-border">
            {columns.map((col) => {
              const isSorted = sortKey === col.key;
              return (
                <th
                  key={col.key}
                  scope="col"
                  className={cn(
                    "whitespace-nowrap px-4 py-2.5 text-xs font-medium text-ink-muted",
                    col.sortable && "cursor-pointer select-none hover:text-ink",
                    col.headerClassName,
                  )}
                  onClick={() => col.sortable && onSortChange?.(col.key)}
                >
                  <span className="inline-flex items-center gap-1">
                    {col.header}
                    {col.sortable &&
                      (isSorted ? (
                        sortDir === "asc" ? (
                          <ArrowUp className="h-3 w-3" />
                        ) : (
                          <ArrowDown className="h-3 w-3" />
                        )
                      ) : (
                        <ArrowUpDown className="h-3 w-3 opacity-40" />
                      ))}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {!isLoading && !isError &&
            rows.map((row) => (
              <tr
                key={rowKey(row)}
                onClick={() => onRowClick?.(row)}
                className={cn("transition-colors", onRowClick && "cursor-pointer hover:bg-slate-50")}
              >
                {columns.map((col) => (
                  <td key={col.key} className={cn("whitespace-nowrap px-4 py-3 text-ink", col.className)}>
                    {col.render(row)}
                  </td>
                ))}
              </tr>
            ))}
        </tbody>
      </table>

      {isLoading && <TableSkeleton columns={columns.length} />}
      {!isLoading && isError && <ErrorState onRetry={onRetry} />}
      {!isLoading && !isError && rows.length === 0 && (
        <EmptyState title={emptyTitle} description={emptyDescription} />
      )}
    </div>
  );
}
