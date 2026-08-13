import { cn } from "@/utils/cn";

interface TabItem {
  key: string;
  label: string;
  count?: number;
}

interface TabsProps {
  items: TabItem[];
  activeKey: string;
  onChange: (key: string) => void;
}

export function Tabs({ items, activeKey, onChange }: TabsProps) {
  return (
    <div role="tablist" className="flex items-center gap-1 border-b border-border">
      {items.map((item) => {
        const isActive = item.key === activeKey;
        return (
          <button
            key={item.key}
            role="tab"
            aria-selected={isActive}
            onClick={() => onChange(item.key)}
            className={cn(
              "relative flex items-center gap-1.5 px-3 py-2.5 text-sm font-medium transition-colors",
              isActive ? "text-ink" : "text-ink-muted hover:text-ink",
            )}
          >
            {item.label}
            {item.count !== undefined && (
              <span className="rounded-full bg-slate-100 px-1.5 text-xs text-ink-muted">{item.count}</span>
            )}
            {isActive && <span className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-accent" />}
          </button>
        );
      })}
    </div>
  );
}
