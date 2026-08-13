import type { ISODateString, UUID } from "./common";

export type AgentExecutionStatus = "success" | "failure" | "running";

export interface AgentStageDefinition {
  key: string;
  name: string;
  description: string;
}

export interface AgentStats {
  key: string;
  name: string;
  description: string;
  status: "healthy" | "degraded" | "offline";
  lastExecutionAt?: ISODateString;
  avgExecutionTimeMs: number;
  successRate: number;
  avgConfidence: number;
  executionsLast24h: number;
}

export interface AgentExecution {
  id: UUID;
  agentKey: string;
  status: AgentExecutionStatus;
  startedAt: ISODateString;
  durationMs: number;
  confidence?: number;
  incidentId?: string;
  summary?: string;
}
