import { apiRequest, mockDelay } from "./client";
import { USE_MOCK_DATA } from "./config";
import type { AgentExecution, AgentStats } from "@/types/agent";
import { mockAgentExecutions, mockAgentStats } from "@/mocks/data/agents";

export async function listAgentStats(): Promise<AgentStats[]> {
  if (USE_MOCK_DATA) {
    return mockDelay(mockAgentStats);
  }
  return apiRequest<AgentStats[]>(`/observability/agents`);
}

export async function listAgentExecutions(agentKey?: string): Promise<AgentExecution[]> {
  if (USE_MOCK_DATA) {
    const executions = agentKey
      ? mockAgentExecutions.filter((e) => e.agentKey === agentKey)
      : mockAgentExecutions;
    return mockDelay(executions);
  }
  const params = agentKey ? `?agent=${encodeURIComponent(agentKey)}` : "";
  return apiRequest<AgentExecution[]>(`/observability/agents/executions${params}`);
}
