import { Search, X } from "lucide-react";
import { cn } from "@/utils/cn";

interface SearchBarProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  autoFocus?: boolean;
}

export function SearchBar({ value, onChange, placeholder = "Search…", className, autoFocus }: SearchBarProps) {
  return (
    <div className={cn("relative", className)}>
      <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-subtle" />
      <input
        value={value}
        autoFocus={autoFocus}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="h-9 w-full rounded-md border border-border bg-white pl-9 pr-8 text-sm text-ink placeholder:text-ink-subtle focus-visible:border-accent"
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange("")}
          aria-label="Clear search"
          className="absolute right-2.5 top-1/2 -translate-y-1/2 text-ink-subtle hover:text-ink-muted"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}
