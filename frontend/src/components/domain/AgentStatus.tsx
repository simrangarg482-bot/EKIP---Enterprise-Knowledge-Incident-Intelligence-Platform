import type { AgentStats } from "@/types/agent";
import { StatusBadge } from "@/components/data/StatusBadge";
import { formatDurationMs, formatPercent } from "@/utils/format";
import { formatRelativeTime } from "@/utils/date";
import { cn } from "@/utils/cn";

interface AgentStatusProps {
  agent: AgentStats;
  onClick?: (agent: AgentStats) => void;
}

export function AgentStatusCard({ agent, onClick }: AgentStatusProps) {
  return (
    <button
      type="button"
      onClick={() => onClick?.(agent)}
      className={cn(
        "flex w-full flex-col gap-2.5 rounded-lg border border-border bg-surface px-4 py-3.5 text-left shadow-subtle transition-colors",
        onClick && "hover:border-accent-border hover:bg-accent-subtle/40",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-semibold text-ink">{agent.name}</p>
        <StatusBadge status={agent.status} />
      </div>
      <p className="text-xs text-ink-muted">{agent.description}</p>
      <dl className="mt-1 grid grid-cols-2 gap-2 text-xs">
        <div>
          <dt className="text-ink-subtle">Avg time</dt>
          <dd className="font-medium text-ink">{formatDurationMs(agent.avgExecutionTimeMs)}</dd>
        </div>
        <div>
          <dt className="text-ink-subtle">Success rate</dt>
          <dd className="font-medium text-ink">{formatPercent(agent.successRate)}</dd>
        </div>
        <div>
          <dt className="text-ink-subtle">Avg confidence</dt>
          <dd className="font-medium text-ink">{formatPercent(agent.avgConfidence)}</dd>
        </div>
        <div>
          <dt className="text-ink-subtle">Last run</dt>
          <dd className="font-medium text-ink">
            {agent.lastExecutionAt ? formatRelativeTime(agent.lastExecutionAt) : "—"}
          </dd>
        </div>
      </dl>
    </button>
  );
}
