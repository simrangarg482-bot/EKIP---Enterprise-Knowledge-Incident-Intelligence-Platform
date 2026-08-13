import type { ISODateString, UUID } from "./common";

/** Mirrors `app.core.tenancy.schemas.ConnectorSource` plus `"manual"` (human/agent-proposed documents). */
export type KnowledgeSource =
  | "github"
  | "slack"
  | "manual"
  | "teams"
  | "azure_devops"
  | "jira"
  | "confluence"
  | "sharepoint"
  | "runbooks"
  | "monitoring";

/** Mirrors `app.shared.schemas.DocumentStatus` -- a rejected proposal is soft-deleted, not a third status value. */
export type DocumentStatus = "published" | "proposed";

/** Mirrors `app.core.knowledge.schemas.Document`. */
export interface KnowledgeDocument {
  id: UUID;
  organizationId: UUID;
  projectId: UUID;
  title: string | null;
  status: DocumentStatus;
  version: number;
  content: string | null;
  source: KnowledgeSource;
  sourceUrl: string | null;
  sourceIncidentId: UUID | null;
  createdAt: ISODateString;
  updatedAt: ISODateString;
}

export interface GapReport {
  id: UUID;
  topic: string;
  description: string;
  relatedIncidentIds: string[];
  detectedAt: ISODateString;
  severity: "high" | "medium" | "low";
}

export interface KnowledgeFilters {
  search?: string;
  source?: KnowledgeSource[];
  page?: number;
  pageSize?: number;
}
