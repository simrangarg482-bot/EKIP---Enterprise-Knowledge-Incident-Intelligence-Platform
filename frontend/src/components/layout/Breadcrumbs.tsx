import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";

export interface Breadcrumb {
  label: string;
  path?: string;
}

export function Breadcrumbs({ items }: { items: Breadcrumb[] }) {
  return (
    <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-xs text-ink-muted">
      {items.map((item, index) => (
        <span key={`${item.label}-${index}`} className="flex items-center gap-1.5">
          {index > 0 && <ChevronRight className="h-3 w-3 text-ink-subtle" />}
          {item.path ? (
            <Link to={item.path} className="hover:text-ink">
              {item.label}
            </Link>
          ) : (
            <span className="text-ink">{item.label}</span>
          )}
        </span>
      ))}
    </nav>
  );
}
