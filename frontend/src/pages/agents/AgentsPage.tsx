import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowDown } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { AgentStatusCard } from "@/components/domain/AgentStatus";
import { Drawer } from "@/components/ui/Drawer";
import { StatusBadge } from "@/components/data/StatusBadge";
import { LoadingState } from "@/components/ui/LoadingState";
import { ErrorState } from "@/components/ui/ErrorState";
import { listAgentExecutions, listAgentStats } from "@/api/agents";
import { agentPipelineStages } from "@/mocks/data/agents";
import type { AgentStats } from "@/types/agent";
import { formatDateTime, formatRelativeTime } from "@/utils/date";
import { formatDurationMs, formatPercent } from "@/utils/format";

export function AgentsPage() {
  const [selectedAgent, setSelectedAgent] = useState<AgentStats | null>(null);
  const statsQuery = useQuery({ queryKey: ["agents", "stats"], queryFn: listAgentStats });
  const executionsQuery = useQuery({
    queryKey: ["agents", "executions", selectedAgent?.key],
    queryFn: () => listAgentExecutions(selectedAgent?.key),
    enabled: Boolean(selectedAgent),
  });

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Agents"
        description="EKIP's retrieval and reasoning pipeline, from query understanding to grounded answers."
      />

      <div className="rounded-lg border border-border bg-surface p-5 shadow-subtle">
        <div className="flex flex-col items-center gap-1">
          {agentPipelineStages.map((stage, index) => (
            <div key={stage.key} className="flex flex-col items-center gap-1">
              <div className="w-full max-w-xs rounded-md border border-border bg-slate-50 px-4 py-2.5 text-center">
                <p className="text-sm font-medium text-ink">{stage.name}</p>
              </div>
              {index < agentPipelineStages.length - 1 && (
                <ArrowDown className="h-4 w-4 text-ink-subtle" />
              )}
            </div>
          ))}
        </div>
      </div>

      {statsQuery.isLoading && <LoadingState label="Loading agent status…" />}
      {statsQuery.isError && <ErrorState onRetry={() => statsQuery.refetch()} />}

      {statsQuery.data && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {statsQuery.data.map((agent) => (
            <AgentStatusCard key={agent.key} agent={agent} onClick={setSelectedAgent} />
          ))}
        </div>
      )}

      <Drawer
        open={Boolean(selectedAgent)}
        onClose={() => setSelectedAgent(null)}
        title={selectedAgent ? `${selectedAgent.name} — execution history` : ""}
      >
        {executionsQuery.isLoading && <LoadingState label="Loading executions…" />}
        {executionsQuery.data && executionsQuery.data.length === 0 && (
          <p className="text-sm text-ink-muted">No recent executions for this agent.</p>
        )}
        {executionsQuery.data && executionsQuery.data.length > 0 && (
          <ul className="flex flex-col gap-3">
            {executionsQuery.data.map((execution) => (
              <li key={execution.id} className="rounded-md border border-border px-3 py-2.5">
                <div className="flex items-center justify-between gap-2">
                  <StatusBadge status={execution.status === "success" ? "healthy" : execution.status === "failure" ? "offline" : "degraded"} />
                  <span title={formatDateTime(execution.startedAt)} className="text-xs text-ink-subtle">
                    {formatRelativeTime(execution.startedAt)}
                  </span>
                </div>
                {execution.summary && <p className="mt-1.5 text-sm text-ink">{execution.summary}</p>}
                <div className="mt-1.5 flex gap-4 text-xs text-ink-muted">
                  <span>Duration: {formatDurationMs(execution.durationMs)}</span>
                  {execution.confidence !== undefined && <span>Confidence: {formatPercent(execution.confidence)}</span>}
                  {execution.incidentId && <span>Incident: {execution.incidentId}</span>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Drawer>
    </div>
  );
}
