import { Link } from "react-router-dom";
import type { KnowledgeDocument } from "@/types/knowledge";
import { Badge } from "@/components/ui/Badge";
import { formatRelativeTime } from "@/utils/date";
import { titleCase } from "@/utils/format";

export function KnowledgeCard({ document }: { document: KnowledgeDocument }) {
  return (
    <Link
      to={`/knowledge/${document.id}`}
      className="flex flex-col gap-2 rounded-lg border border-border bg-surface px-4 py-3.5 shadow-subtle transition-colors hover:border-accent-border hover:bg-accent-subtle/40"
    >
      <div className="flex items-center gap-2">
        <Badge tone="neutral">{titleCase(document.source)}</Badge>
        <Badge tone={document.status === "published" ? "success" : "warning"}>
          {titleCase(document.status)}
        </Badge>
      </div>
      <p className="text-sm font-medium text-ink">{document.title ?? "(untitled)"}</p>
      {document.content && <p className="line-clamp-2 text-xs text-ink-muted">{document.content}</p>}
      <div className="flex items-center justify-end text-xs text-ink-subtle">
        <span>Updated {formatRelativeTime(document.updatedAt)}</span>
      </div>
    </Link>
  );
}
