export type SearchResultType = "incident" | "knowledge" | "slack" | "github";

export interface SearchResult {
  id: string;
  type: SearchResultType;
  title: string;
  snippet: string;
  source: string;
  url?: string;
  timestamp?: string;
}
