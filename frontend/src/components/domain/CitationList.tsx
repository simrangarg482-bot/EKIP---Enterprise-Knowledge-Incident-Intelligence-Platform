import type { LucideIcon } from "lucide-react";
import { Github, MessageSquare, FileText, Ticket, AlertCircle, Database, Link2 } from "lucide-react";
import type { CitationSource } from "@/types/incident";
import { formatRelativeTime } from "@/utils/date";

const SYSTEM_ICON: Record<CitationSource["system"], LucideIcon> = {
  github: Github,
  slack: MessageSquare,
  confluence: FileText,
  jira: Ticket,
  incident: AlertCircle,
  postgresql: Database,
  other: Link2,
};

export function CitationList({ sources }: { sources: CitationSource[] }) {
  if (sources.length === 0) return null;

  return (
    <div>
      <p className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-subtle">Based on</p>
      <ul className="flex flex-col gap-1.5">
        {sources.map((source) => {
          const Icon = SYSTEM_ICON[source.system];
          return (
            <li key={`${source.system}-${source.reference}`}>
              <a
                href={source.url ?? "#"}
                className="flex items-center gap-2 rounded-md border border-border bg-white px-2.5 py-1.5 text-xs text-ink hover:border-accent-border hover:bg-accent-subtle"
              >
                <Icon className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
                <span className="truncate">{source.label}</span>
                {source.timestamp && (
                  <span className="ml-auto shrink-0 text-ink-subtle">{formatRelativeTime(source.timestamp)}</span>
                )}
              </a>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
