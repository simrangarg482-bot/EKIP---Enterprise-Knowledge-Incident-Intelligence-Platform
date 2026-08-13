import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { PlayCircle, Wrench } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { StatusBadge } from "@/components/data/StatusBadge";
import { LoadingState } from "@/components/ui/LoadingState";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { Drawer } from "@/components/ui/Drawer";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { listMcpTools, invokeMcpTool } from "@/api/mcp";
import type { McpTool } from "@/types/mcp";
import { formatDurationMs } from "@/utils/format";
import { formatRelativeTime } from "@/utils/date";

export function McpToolsPage() {
  const [testingTool, setTestingTool] = useState<McpTool | null>(null);
  const toolsQuery = useQuery({ queryKey: ["mcp", "tools"], queryFn: listMcpTools });

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="MCP Tools"
        description="Model Context Protocol tools EKIP's agents use to search, retrieve, and analyze."
      />

      {toolsQuery.isLoading && <LoadingState label="Loading MCP tools…" />}
      {toolsQuery.isError && <ErrorState onRetry={() => toolsQuery.refetch()} />}
      {toolsQuery.data && toolsQuery.data.length === 0 && (
        <EmptyState icon={Wrench} title="No MCP tools registered" />
      )}

      {toolsQuery.data && toolsQuery.data.length > 0 && (
        <div className="flex flex-col gap-3">
          {toolsQuery.data.map((tool) => (
            <Card key={tool.name}>
              <CardHeader>
                <div>
                  <CardTitle>
                    <code className="text-sm">{tool.name}</code>
                  </CardTitle>
                  <p className="mt-1 max-w-2xl text-xs text-ink-muted">{tool.description}</p>
                </div>
                <div className="flex items-center gap-3">
                  <StatusBadge status={tool.status === "available" ? "active" : tool.status === "deprecated" ? "error" : "disconnected"} />
                  <Button size="sm" variant="secondary" className="gap-1.5" onClick={() => setTestingTool(tool)}>
                    <PlayCircle className="h-3.5 w-3.5" />
                    Test Tool
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-ink-muted">
                <span>Parameters: {tool.parameters.length}</span>
                {tool.avgLatencyMs !== undefined && <span>Avg latency: {formatDurationMs(tool.avgLatencyMs)}</span>}
                {tool.callCountLast24h !== undefined && <span>Calls (24h): {tool.callCountLast24h.toLocaleString()}</span>}
                {tool.lastExecutionAt && <span>Last run: {formatRelativeTime(tool.lastExecutionAt)}</span>}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <ToolTestDrawer tool={testingTool} onClose={() => setTestingTool(null)} />
    </div>
  );
}

function ToolTestDrawer({ tool, onClose }: { tool: McpTool | null; onClose: () => void }) {
  const [inputs, setInputs] = useState<Record<string, string>>({});

  const invokeMutation = useMutation({
    mutationFn: () => invokeMcpTool(tool!.name, inputs),
  });

  function handleClose() {
    setInputs({});
    invokeMutation.reset();
    onClose();
  }

  return (
    <Drawer open={Boolean(tool)} onClose={handleClose} title={tool ? `Test ${tool.name}` : ""}>
      {tool && (
        <div className="flex flex-col gap-4">
          <p className="text-xs text-ink-muted">{tool.description}</p>

          <div className="flex flex-col gap-3">
            {tool.parameters.map((param) => (
              <div key={param.name}>
                <label className="mb-1 flex items-center gap-1.5 text-xs font-medium text-ink-muted">
                  {param.name}
                  {param.required && <span className="text-critical">*</span>}
                  <span className="rounded bg-slate-100 px-1 font-mono text-[10px] text-ink-subtle">{param.type}</span>
                </label>
                <Input
                  placeholder={param.defaultValue ?? param.description}
                  value={inputs[param.name] ?? ""}
                  onChange={(e) => setInputs((prev) => ({ ...prev, [param.name]: e.target.value }))}
                />
                <p className="mt-1 text-xs text-ink-subtle">{param.description}</p>
              </div>
            ))}
          </div>

          <Button
            variant="primary"
            className="gap-1.5"
            isLoading={invokeMutation.isPending}
            onClick={() => invokeMutation.mutate()}
          >
            <PlayCircle className="h-3.5 w-3.5" />
            Run tool
          </Button>

          {invokeMutation.data && (
            <div>
              <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-ink-subtle">Result</p>
              <pre className="overflow-x-auto rounded-md border border-border bg-slate-50 p-3 text-xs text-ink scrollbar-thin">
                {JSON.stringify(invokeMutation.data.output ?? invokeMutation.data.error, null, 2)}
              </pre>
              <p className="mt-1.5 text-xs text-ink-subtle">
                Completed in {formatDurationMs(invokeMutation.data.durationMs)}
              </p>
            </div>
          )}

          {invokeMutation.isError && (
            <p className="rounded-md border border-critical-border bg-critical-subtle px-3 py-2 text-xs text-critical">
              Tool invocation failed. Check that the backend endpoint is reachable.
            </p>
          )}
        </div>
      )}
    </Drawer>
  );
}
