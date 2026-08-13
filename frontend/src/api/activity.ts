import { apiRequest, mockDelay } from "./client";
import { USE_MOCK_DATA } from "./config";
import type { ActivityEntry } from "@/types/activity";
import { mockActivity } from "@/mocks/data/activity";

export async function listRecentActivity(): Promise<ActivityEntry[]> {
  if (USE_MOCK_DATA) {
    return mockDelay(mockActivity, 250);
  }
  return apiRequest<ActivityEntry[]>(`/observability/activity`);
}
