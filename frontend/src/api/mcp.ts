import { apiRequest, mockDelay } from "./client";
import { USE_MOCK_DATA } from "./config";
import type { McpTool, McpToolInvocationResult } from "@/types/mcp";
import { mockMcpTools } from "@/mocks/data/mcp";

export async function listMcpTools(): Promise<McpTool[]> {
  if (USE_MOCK_DATA) {
    return mockDelay(mockMcpTools);
  }
  return apiRequest<McpTool[]>(`/observability/mcp`);
}

/**
 * Invokes an MCP tool with the given input. Wired to a placeholder endpoint —
 * the backend does not yet expose a generic tool-invocation route, so this
 * throws in real (non-mock) mode until /mcp/tools/{name}/invoke exists.
 */
export async function invokeMcpTool(
  toolName: string,
  input: Record<string, unknown>,
): Promise<McpToolInvocationResult> {
  if (USE_MOCK_DATA) {
    const tool = mockMcpTools.find((t) => t.name === toolName);
    const start = Date.now();
    return mockDelay(
      {
        toolName,
        input,
        output: {
          note: `Mock response for ${toolName}. Connect the real MCP endpoint to see live results.`,
          tool: tool?.description,
        },
        durationMs: Date.now() - start + 180,
        executedAt: new Date().toISOString(),
      },
      500,
    );
  }
  return apiRequest<McpToolInvocationResult>(`/mcp/tools/${toolName}/invoke`, {
    method: "POST",
    body: input,
  });
}
