import type { ISODateString, UUID } from "./common";

export interface Citation {
  documentId: UUID;
  chunkId: UUID;
  sourceUrl: string | null;
  excerpt: string;
}

export type EvidenceSource =
  | "github"
  | "pull_request"
  | "commit"
  | "issue"
  | "slack"
  | "jira"
  | "deployment"
  | "postmortem"
  | "monitoring";

export interface EvidenceItem {
  source: EvidenceSource;
  reference: string;
  summary: string;
  retrievedAt: ISODateString;
  sourceTimestamp: ISODateString | null;
  metadata: Record<string, string>;
}

export interface RootCauseHypothesis {
  description: string;
  confidence: number;
  supportingEvidenceIds: string[];
}

export interface InvestigationResult {
  evidence: EvidenceItem[];
  hypotheses: RootCauseHypothesis[];
  suggestedOwnerTeam: string | null;
  suggestedNextSteps: string[];
}

export interface AskResponse {
  confidence: number;
  routeTaken: "answer" | "investigation";
  answer: string | null;
  citations: Citation[];
  investigation: InvestigationResult | null;
}

export type AgentExecutionStatus = "running" | "succeeded" | "failed";

export interface QuestionHistoryEntry {
  id: UUID;
  organizationId: UUID;
  agentName: string;
  triggerSource: string;
  inputSummary: Record<string, string | null> | null;
  confidenceScore: number | null;
  status: AgentExecutionStatus;
  errorDetail: string | null;
  startedAt: ISODateString;
  completedAt: ISODateString | null;
}

export interface ScoredChunk {
  chunkId: UUID;
  documentId: UUID;
  collection: "documentation" | "code" | "conversations";
  content: string;
  score: number;
  sourceOffsetStart: number;
  sourceOffsetEnd: number;
  title: string | null;
  sourceUrl: string | null;
  metadata: Record<string, string>;
}

/** One turn in the Ask EKIP chat UI -- a question plus, once resolved, its answer. */
export interface ChatTurn {
  id: string;
  question: string;
  isPending: boolean;
  response?: AskResponse;
  error?: string;
}
