import type { ISODateString } from "./common";

export type McpParamType = "string" | "number" | "boolean" | "object" | "array";

export interface McpToolParameter {
  name: string;
  type: McpParamType;
  required: boolean;
  description: string;
  defaultValue?: string;
}

export type McpToolStatus = "available" | "unavailable" | "deprecated";

export interface McpTool {
  name: string;
  description: string;
  status: McpToolStatus;
  parameters: McpToolParameter[];
  lastExecutionAt?: ISODateString;
  avgLatencyMs?: number;
  callCountLast24h?: number;
}

export interface McpToolInvocationResult {
  toolName: string;
  input: Record<string, unknown>;
  output?: unknown;
  error?: string;
  durationMs: number;
  executedAt: ISODateString;
}
