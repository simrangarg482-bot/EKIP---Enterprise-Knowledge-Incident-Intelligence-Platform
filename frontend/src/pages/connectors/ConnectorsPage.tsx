import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plug, Plus } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { ConnectorCard } from "@/components/domain/ConnectorCard";
import { ConnectConnectorModal } from "@/components/domain/ConnectConnectorModal";
import { Button } from "@/components/ui/Button";
import { LoadingState } from "@/components/ui/LoadingState";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { Drawer } from "@/components/ui/Drawer";
import { StatusBadge } from "@/components/data/StatusBadge";
import {
  createGithubConnector,
  createSlackConnector,
  listConnectors,
  triggerConnectorSync,
} from "@/api/connectors";
import type { Connector } from "@/types/connector";
import { useToast } from "@/context/ToastContext";
import { formatDateTime, formatRelativeTime } from "@/utils/date";
import { titleCase } from "@/utils/format";

export function ConnectorsPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [viewing, setViewing] = useState<Connector | null>(null);
  const [isConnectOpen, setIsConnectOpen] = useState(false);

  const connectorsQuery = useQuery({ queryKey: ["connectors"], queryFn: listConnectors });

  const syncMutation = useMutation({
    mutationFn: (connector: Connector) => triggerConnectorSync(connector.id),
    onSuccess: (_, connector) => {
      toast({ variant: "info", title: `Sync started for ${titleCase(connector.source)}` });
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
    },
    onError: (_, connector) => {
      toast({ variant: "error", title: `Failed to sync ${titleCase(connector.source)}` });
    },
  });

  const githubMutation = useMutation({
    mutationFn: ({ token, repos }: { token: string; repos: { repo: string; ref?: string }[] }) =>
      createGithubConnector({ token, repos }),
    onSuccess: () => {
      toast({ variant: "success", title: "GitHub connector added" });
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
    },
    onError: () => {
      toast({ variant: "error", title: "Failed to add GitHub connector" });
    },
  });

  const slackMutation = useMutation({
    mutationFn: ({ token, channelIds }: { token: string; channelIds: string[] }) =>
      createSlackConnector({ token, channelIds }),
    onSuccess: () => {
      toast({ variant: "success", title: "Slack connector added" });
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
    },
    onError: () => {
      toast({ variant: "error", title: "Failed to add Slack connector" });
    },
  });

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Connectors"
        description="Integrations that feed knowledge and incident context into EKIP."
        actions={
          <Button variant="primary" className="gap-1.5" onClick={() => setIsConnectOpen(true)}>
            <Plus className="h-4 w-4" />
            Connect a source
          </Button>
        }
      />

      {connectorsQuery.isLoading && <LoadingState label="Loading connectors…" />}
      {connectorsQuery.isError && <ErrorState onRetry={() => connectorsQuery.refetch()} />}
      {connectorsQuery.data && connectorsQuery.data.length === 0 && (
        <EmptyState
          icon={Plug}
          title="No connectors configured"
          description="Connect GitHub or Slack to start ingesting data EKIP can answer questions about."
          action={
            <Button variant="primary" className="gap-1.5" onClick={() => setIsConnectOpen(true)}>
              <Plus className="h-4 w-4" />
              Connect a source
            </Button>
          }
        />
      )}

      {connectorsQuery.data && connectorsQuery.data.length > 0 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {connectorsQuery.data.map((connector) => (
            <ConnectorCard
              key={connector.id}
              connector={connector}
              onView={setViewing}
              onSync={(c) => syncMutation.mutate(c)}
              isSyncing={syncMutation.isPending && syncMutation.variables?.id === connector.id}
            />
          ))}
        </div>
      )}

      <ConnectConnectorModal
        open={isConnectOpen}
        onClose={() => setIsConnectOpen(false)}
        isSubmitting={githubMutation.isPending || slackMutation.isPending}
        onSubmitGithub={async (token, repos) => {
          await githubMutation.mutateAsync({ token, repos });
        }}
        onSubmitSlack={async (token, channelIds) => {
          await slackMutation.mutateAsync({ token, channelIds });
        }}
      />

      <Drawer open={Boolean(viewing)} onClose={() => setViewing(null)} title={viewing ? titleCase(viewing.source) : ""}>
        {viewing && (
          <div className="flex flex-col gap-4">
            <div className="flex items-center justify-between">
              <span className="text-xs text-ink-muted">Status</span>
              <StatusBadge status={viewing.status} />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-xs text-ink-muted">Connected</span>
              <span className="text-sm text-ink">{formatDateTime(viewing.createdAt)}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-xs text-ink-muted">Last sync</span>
              <span className="text-sm text-ink">
                {viewing.lastSyncedAt ? formatRelativeTime(viewing.lastSyncedAt) : "Never"}
              </span>
            </div>
            <div>
              <p className="mb-1.5 text-xs text-ink-muted">Configuration</p>
              <pre className="overflow-x-auto rounded-md border border-border bg-slate-50 px-3 py-2 text-xs text-ink">
                {JSON.stringify(viewing.config, null, 2)}
              </pre>
            </div>
            <p className="text-xs text-ink-subtle">
              Ingested content from this source appears on the Knowledge page.
            </p>
          </div>
        )}
      </Drawer>
    </div>
  );
}
