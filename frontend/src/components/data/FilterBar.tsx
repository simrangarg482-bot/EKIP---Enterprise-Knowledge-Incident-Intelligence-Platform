import type { ReactNode } from "react";
import { SlidersHorizontal, X } from "lucide-react";
import { Button } from "@/components/ui/Button";

interface FilterBarProps {
  children: ReactNode;
  activeCount?: number;
  onClear?: () => void;
}

export function FilterBar({ children, activeCount = 0, onClear }: FilterBarProps) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-white px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-xs font-medium text-ink-muted">
        <SlidersHorizontal className="h-3.5 w-3.5" />
        Filters
      </div>
      <div className="flex flex-1 flex-wrap items-center gap-2">{children}</div>
      {activeCount > 0 && onClear && (
        <Button variant="ghost" size="sm" onClick={onClear} className="gap-1">
          <X className="h-3 w-3" />
          Clear ({activeCount})
        </Button>
      )}
    </div>
  );
}
