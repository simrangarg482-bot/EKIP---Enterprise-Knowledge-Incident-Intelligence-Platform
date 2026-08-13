import type { ISODateString, UUID } from "./common";

export type IncidentSeverity = "critical" | "high" | "medium" | "low";

export type IncidentStatus = "open" | "investigating" | "monitoring" | "resolved" | "closed";

export interface Incident {
  id: UUID;
  displayId: string;
  title: string;
  description?: string;
  severity: IncidentSeverity;
  status: IncidentStatus;
  service: string;
  assignee?: {
    id: UUID;
    name: string;
    avatarUrl?: string;
  };
  createdAt: ISODateString;
  updatedAt: ISODateString;
  resolvedAt?: ISODateString;
  tags?: string[];
}

export type TimelineEventType =
  | "created"
  | "status_change"
  | "severity_change"
  | "assignment"
  | "comment"
  | "agent_execution"
  | "connector_event"
  | "resolution";

export interface TimelineEntry {
  id: UUID;
  incidentId: UUID;
  type: TimelineEventType;
  actor: string;
  message: string;
  createdAt: ISODateString;
  metadata?: Record<string, string>;
}

export interface CitationSource {
  label: string;
  system: "github" | "slack" | "confluence" | "jira" | "incident" | "postgresql" | "other";
  reference: string;
  url?: string;
  timestamp?: ISODateString;
}

export interface RootCauseHypothesis {
  summary: string;
  confidence: number;
  evidence: string[];
}

export interface AiInvestigation {
  incidentId: UUID;
  summary: string;
  rootCauseHypotheses: RootCauseHypothesis[];
  relevantKnowledge: CitationSource[];
  similarIncidents: Array<{
    incident: Incident;
    similarityScore: number;
    matchedOn: string;
  }>;
  recommendedActions: string[];
  confidence: number;
  generatedAt: ISODateString;
  model: string;
}

export interface IncidentComment {
  id: UUID;
  incidentId: UUID;
  author: string;
  body: string;
  createdAt: ISODateString;
}

export interface IncidentFilters {
  search?: string;
  severity?: IncidentSeverity[];
  status?: IncidentStatus[];
  service?: string[];
  dateFrom?: string;
  dateTo?: string;
  page?: number;
  pageSize?: number;
  sortBy?: keyof Incident;
  sortDir?: "asc" | "desc";
}
