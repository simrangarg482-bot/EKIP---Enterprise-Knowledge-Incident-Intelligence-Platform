import type { LucideIcon } from "lucide-react";
import {
  CircleDot,
  ArrowRightLeft,
  AlertTriangle,
  UserPlus,
  MessageSquare,
  Bot,
  Plug,
  CheckCircle2,
} from "lucide-react";
import type { TimelineEntry, TimelineEventType } from "@/types/incident";
import { formatDateTime, formatRelativeTime } from "@/utils/date";

const TYPE_ICON: Record<TimelineEventType, LucideIcon> = {
  created: CircleDot,
  status_change: ArrowRightLeft,
  severity_change: AlertTriangle,
  assignment: UserPlus,
  comment: MessageSquare,
  agent_execution: Bot,
  connector_event: Plug,
  resolution: CheckCircle2,
};

export function Timeline({ entries }: { entries: TimelineEntry[] }) {
  if (entries.length === 0) {
    return <p className="text-sm text-ink-muted">No timeline events yet.</p>;
  }

  return (
    <ol className="relative flex flex-col gap-5 border-l border-border pl-5">
      {entries.map((entry) => {
        const Icon = TYPE_ICON[entry.type];
        return (
          <li key={entry.id} className="relative">
            <span className="absolute -left-[1.6rem] flex h-5 w-5 items-center justify-center rounded-full border border-border bg-white">
              <Icon className="h-3 w-3 text-ink-muted" />
            </span>
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="text-sm font-medium text-ink">{entry.actor}</span>
              <time className="text-xs text-ink-subtle" title={formatDateTime(entry.createdAt)}>
                {formatRelativeTime(entry.createdAt)}
              </time>
            </div>
            <p className="mt-0.5 text-sm text-ink-muted">{entry.message}</p>
          </li>
        );
      })}
    </ol>
  );
}
