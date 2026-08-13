import type { ActivityEntry } from "@/types/activity";
import { minutesAgo } from "@/mocks/time";

export const mockActivity: ActivityEntry[] = [
  { id: "act-1", kind: "connector_sync", label: "GitHub connector sync completed", meta: "2,431 documents indexed", occurredAt: minutesAgo(12) },
  { id: "act-2", kind: "incident_update", label: "INC-1024 escalated to Critical", meta: "Payment API error rate exceeded threshold", occurredAt: minutesAgo(11) },
  { id: "act-3", kind: "agent_execution", label: "Investigation Agent executed", meta: "Generated hypotheses for INC-1024", occurredAt: minutesAgo(8) },
  { id: "act-4", kind: "knowledge_ingestion", label: "Confluence ingestion completed", meta: "18 new chunks processed", occurredAt: minutesAgo(5) },
  { id: "act-5", kind: "mcp_execution", label: "search_knowledge tool invoked", meta: "142 calls in the last hour", occurredAt: minutesAgo(3) },
];
