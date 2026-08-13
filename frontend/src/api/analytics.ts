import { apiRequest, mockDelay } from "./client";
import { USE_MOCK_DATA } from "./config";
import type { AnalyticsSummary } from "@/types/analytics";
import { mockAnalyticsSummary } from "@/mocks/data/analytics";

export async function getAnalyticsSummary(): Promise<AnalyticsSummary> {
  if (USE_MOCK_DATA) {
    return mockDelay(mockAnalyticsSummary, 400);
  }
  return apiRequest<AnalyticsSummary>(`/analytics/summary`);
}
