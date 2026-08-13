import type { McpTool } from "@/types/mcp";
import { minutesAgo } from "@/mocks/time";

export const mockMcpTools: McpTool[] = [
  {
    name: "search_knowledge",
    description: "Performs hybrid lexical + semantic search over the knowledge base and returns ranked document chunks.",
    status: "available",
    parameters: [
      { name: "query", type: "string", required: true, description: "Natural-language search query." },
      { name: "project", type: "string", required: false, description: "Restrict results to a project slug." },
      { name: "top_k", type: "number", required: false, description: "Maximum number of chunks to return.", defaultValue: "10" },
    ],
    lastExecutionAt: minutesAgo(3),
    avgLatencyMs: 240,
    callCountLast24h: 1892,
  },
  {
    name: "search_incidents",
    description: "Searches historical and active incidents by title, description, service, and tags.",
    status: "available",
    parameters: [
      { name: "query", type: "string", required: true, description: "Search text." },
      { name: "status", type: "array", required: false, description: "Filter by one or more incident statuses." },
      { name: "severity", type: "array", required: false, description: "Filter by one or more severities." },
    ],
    lastExecutionAt: minutesAgo(6),
    avgLatencyMs: 180,
    callCountLast24h: 734,
  },
  {
    name: "get_incident",
    description: "Fetches full incident details, including timeline and current investigation state, by ID.",
    status: "available",
    parameters: [{ name: "incident_id", type: "string", required: true, description: "Incident identifier, e.g. INC-1024." }],
    lastExecutionAt: minutesAgo(8),
    avgLatencyMs: 65,
    callCountLast24h: 512,
  },
  {
    name: "retrieve_context",
    description: "Runs the full hybrid retrieval + fusion + reranking pipeline and returns assembled context for a query.",
    status: "available",
    parameters: [
      { name: "query", type: "string", required: true, description: "Query or incident summary to retrieve context for." },
      { name: "max_tokens", type: "number", required: false, description: "Maximum context window size.", defaultValue: "4000" },
    ],
    lastExecutionAt: minutesAgo(8),
    avgLatencyMs: 610,
    callCountLast24h: 398,
  },
  {
    name: "analyze_incident",
    description: "Runs the AI investigation pipeline for an incident and returns root cause hypotheses and recommended actions.",
    status: "available",
    parameters: [
      { name: "incident_id", type: "string", required: true, description: "Incident identifier to analyze." },
      { name: "include_similar", type: "boolean", required: false, description: "Include similar historical incidents.", defaultValue: "true" },
    ],
    lastExecutionAt: minutesAgo(8),
    avgLatencyMs: 1450,
    callCountLast24h: 211,
  },
];
