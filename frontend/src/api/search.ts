import { apiRequest, mockDelay } from "./client";
import { USE_MOCK_DATA } from "./config";
import type { SearchResult } from "@/types/search";
import { mockSearchResults } from "@/mocks/data/search";

export async function globalSearch(query: string): Promise<SearchResult[]> {
  if (!query.trim()) return [];

  if (USE_MOCK_DATA) {
    const q = query.toLowerCase();
    const results = mockSearchResults.filter(
      (r) => r.title.toLowerCase().includes(q) || r.snippet.toLowerCase().includes(q),
    );
    return mockDelay(results.length ? results : mockSearchResults, 300);
  }

  return apiRequest<SearchResult[]>(`/search?q=${encodeURIComponent(query)}`);
}
