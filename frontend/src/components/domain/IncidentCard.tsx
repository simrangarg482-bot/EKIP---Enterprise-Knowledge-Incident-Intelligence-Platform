import { Link } from "react-router-dom";
import type { Incident } from "@/types/incident";
import { SeverityBadge } from "@/components/data/SeverityBadge";
import { StatusBadge } from "@/components/data/StatusBadge";
import { formatRelativeTime } from "@/utils/date";

export function IncidentCard({ incident }: { incident: Incident }) {
  return (
    <Link
      to={`/incidents/${incident.id}`}
      className="flex flex-col gap-2 rounded-lg border border-border bg-surface px-4 py-3.5 shadow-subtle transition-colors hover:border-accent-border hover:bg-accent-subtle/40"
    >
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-ink-muted">{incident.displayId}</span>
        <SeverityBadge severity={incident.severity} />
        <StatusBadge status={incident.status} />
      </div>
      <p className="text-sm font-medium text-ink">{incident.title}</p>
      <div className="flex items-center justify-between text-xs text-ink-subtle">
        <span>{incident.service}</span>
        <span>{formatRelativeTime(incident.createdAt)}</span>
      </div>
    </Link>
  );
}
