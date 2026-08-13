import { apiRequest, mockDelay } from "./client";
import { USE_MOCK_DATA } from "./config";
import type { AskResponse, QuestionHistoryEntry, ScoredChunk } from "@/types/ask";

const MOCK_RESPONSE: AskResponse = {
  confidence: 0.82,
  routeTaken: "answer",
  answer:
    "This is a mock answer -- set VITE_USE_MOCK_DATA=false to ask the real EKIP retrieval pipeline.",
  citations: [
    {
      documentId: "00000000-0000-0000-0000-000000000001",
      chunkId: "00000000-0000-0000-0000-000000000002",
      sourceUrl: "https://github.com/example/repo/blob/main/README.md",
      excerpt: "Example grounding excerpt from a mock source document.",
    },
  ],
  investigation: null,
};

export async function askQuestion(query: string, incidentId?: string): Promise<AskResponse> {
  if (USE_MOCK_DATA) {
    return mockDelay(MOCK_RESPONSE, 800);
  }
  return apiRequest<AskResponse>("/ask", {
    method: "POST",
    body: { query, incidentId: incidentId ?? null },
  });
}

export async function investigateIncident(incidentId: string): Promise<AskResponse> {
  if (USE_MOCK_DATA) {
    return mockDelay({ ...MOCK_RESPONSE, routeTaken: "investigation", answer: null }, 800);
  }
  return apiRequest<AskResponse>(`/incidents/${incidentId}/investigate`, { method: "POST" });
}

export async function getQuestionHistory(limit = 20, offset = 0): Promise<QuestionHistoryEntry[]> {
  if (USE_MOCK_DATA) {
    return mockDelay([], 300);
  }
  return apiRequest<QuestionHistoryEntry[]>(`/ask/history?limit=${limit}&offset=${offset}`);
}

export async function searchSimilarIncidents(description: string, topK = 10): Promise<ScoredChunk[]> {
  if (USE_MOCK_DATA) {
    return mockDelay([], 500);
  }
  return apiRequest<ScoredChunk[]>("/search/similar-incidents", {
    method: "POST",
    body: { description, topK },
  });
}

export async function searchRecentChanges(
  query: string,
  options: { since?: string; topK?: number; collection?: "documentation" | "code" | "conversations" } = {},
): Promise<ScoredChunk[]> {
  if (USE_MOCK_DATA) {
    return mockDelay([], 500);
  }
  return apiRequest<ScoredChunk[]>("/search/recent-changes", {
    method: "POST",
    body: {
      query,
      since: options.since ?? null,
      topK: options.topK ?? 10,
      collection: options.collection ?? "code",
    },
  });
}
