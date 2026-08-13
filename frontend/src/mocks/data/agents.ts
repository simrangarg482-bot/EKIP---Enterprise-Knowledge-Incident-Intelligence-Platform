import type { AgentExecution, AgentStageDefinition, AgentStats } from "@/types/agent";
import { minutesAgo } from "@/mocks/time";

export const agentPipelineStages: AgentStageDefinition[] = [
  { key: "query_understanding", name: "Query Understanding", description: "Parses intent, entities, and constraints from the incoming question or incident." },
  { key: "hybrid_retrieval", name: "Hybrid Retrieval", description: "Runs lexical and vector search across knowledge and incident indices." },
  { key: "rrf_fusion", name: "RRF Fusion", description: "Fuses ranked result sets from multiple retrievers using reciprocal rank fusion." },
  { key: "cross_encoder", name: "Cross Encoder", description: "Reranks fused candidates with a cross-encoder relevance model." },
  { key: "context_assembly", name: "Context Assembly", description: "Assembles the highest-signal passages into a bounded context window." },
  { key: "confidence_evaluation", name: "Confidence Evaluation", description: "Scores retrieval sufficiency before generation is attempted." },
  { key: "answer_agent", name: "Answer Agent", description: "Generates the grounded answer, summary, or recommendation." },
  { key: "grounding_verification", name: "Grounding Verification", description: "Checks that every claim in the answer is supported by retrieved evidence." },
];

export const mockAgentStats: AgentStats[] = [
  { key: "query_understanding", name: "Query Understanding", description: agentPipelineStages[0].description, status: "healthy", lastExecutionAt: minutesAgo(8), avgExecutionTimeMs: 120, successRate: 0.995, avgConfidence: 0.93, executionsLast24h: 412 },
  { key: "hybrid_retrieval", name: "Hybrid Retrieval", description: agentPipelineStages[1].description, status: "healthy", lastExecutionAt: minutesAgo(8), avgExecutionTimeMs: 340, successRate: 0.991, avgConfidence: 0.88, executionsLast24h: 412 },
  { key: "rrf_fusion", name: "RRF Fusion", description: agentPipelineStages[2].description, status: "healthy", lastExecutionAt: minutesAgo(8), avgExecutionTimeMs: 45, successRate: 1, avgConfidence: 0.9, executionsLast24h: 412 },
  { key: "cross_encoder", name: "Cross Encoder", description: agentPipelineStages[3].description, status: "degraded", lastExecutionAt: minutesAgo(8), avgExecutionTimeMs: 890, successRate: 0.94, avgConfidence: 0.81, executionsLast24h: 412 },
  { key: "context_assembly", name: "Context Assembly", description: agentPipelineStages[4].description, status: "healthy", lastExecutionAt: minutesAgo(8), avgExecutionTimeMs: 60, successRate: 0.998, avgConfidence: 0.92, executionsLast24h: 412 },
  { key: "confidence_evaluation", name: "Confidence Evaluation", description: agentPipelineStages[5].description, status: "healthy", lastExecutionAt: minutesAgo(8), avgExecutionTimeMs: 30, successRate: 1, avgConfidence: 0.95, executionsLast24h: 412 },
  { key: "answer_agent", name: "Answer Agent", description: agentPipelineStages[6].description, status: "healthy", lastExecutionAt: minutesAgo(8), avgExecutionTimeMs: 1450, successRate: 0.97, avgConfidence: 0.86, executionsLast24h: 398 },
  { key: "grounding_verification", name: "Grounding Verification", description: agentPipelineStages[7].description, status: "healthy", lastExecutionAt: minutesAgo(8), avgExecutionTimeMs: 210, successRate: 0.988, avgConfidence: 0.9, executionsLast24h: 398 },
];

export const mockAgentExecutions: AgentExecution[] = [
  { id: "exec-1", agentKey: "answer_agent", status: "success", startedAt: minutesAgo(8), durationMs: 1390, confidence: 0.82, incidentId: "inc-1024", summary: "Generated root cause hypotheses for INC-1024." },
  { id: "exec-2", agentKey: "cross_encoder", status: "success", startedAt: minutesAgo(9), durationMs: 910, confidence: 0.79, incidentId: "inc-1024" },
  { id: "exec-3", agentKey: "hybrid_retrieval", status: "success", startedAt: minutesAgo(9), durationMs: 355, confidence: 0.88, incidentId: "inc-1024" },
  { id: "exec-4", agentKey: "answer_agent", status: "failure", startedAt: minutesAgo(42), durationMs: 2100, incidentId: "inc-1023", summary: "Timed out waiting on context assembly for a large log attachment." },
  { id: "exec-5", agentKey: "grounding_verification", status: "success", startedAt: minutesAgo(51), durationMs: 198, confidence: 0.94, incidentId: "inc-1022" },
];
