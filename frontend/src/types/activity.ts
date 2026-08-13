import type { ISODateString } from "./common";

export type ActivityKind = "connector_sync" | "incident_update" | "agent_execution" | "knowledge_ingestion" | "mcp_execution";

export interface ActivityEntry {
  id: string;
  kind: ActivityKind;
  label: string;
  meta: string;
  occurredAt: ISODateString;
}
