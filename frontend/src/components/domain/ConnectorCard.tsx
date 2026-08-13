import type { LucideIcon } from "lucide-react";
import {
  Github,
  MessageSquare,
  Ticket,
  FileText,
  Users2,
  RefreshCw,
  Cloud,
  BookOpen,
  Activity,
} from "lucide-react";
import type { Connector } from "@/types/connector";
import { Card, CardContent } from "@/components/ui/Card";
import { StatusBadge } from "@/components/data/StatusBadge";
import { Button } from "@/components/ui/Button";
import { formatRelativeTime } from "@/utils/date";
import { titleCase } from "@/utils/format";

const SOURCE_ICON: Record<Connector["source"], LucideIcon> = {
  github: Github,
  slack: MessageSquare,
  teams: Users2,
  azure_devops: Cloud,
  jira: Ticket,
  confluence: FileText,
  sharepoint: FileText,
  runbooks: BookOpen,
  monitoring: Activity,
};

function configSummary(connector: Connector): string | null {
  const config = connector.config as { repos?: { repo: string }[]; channels?: string[] };
  if (connector.source === "github" && config.repos?.length) {
    return config.repos.map((r) => r.repo).join(", ");
  }
  if (connector.source === "slack" && config.channels?.length) {
    return `${config.channels.length} channel${config.channels.length === 1 ? "" : "s"}`;
  }
  return null;
}

interface ConnectorCardProps {
  connector: Connector;
  onSync: (connector: Connector) => void;
  onView: (connector: Connector) => void;
  isSyncing?: boolean;
}

export function ConnectorCard({ connector, onSync, onView, isSyncing }: ConnectorCardProps) {
  const Icon = SOURCE_ICON[connector.source];
  const summary = configSummary(connector);

  return (
    <Card>
      <CardContent className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-md bg-slate-100 text-ink-muted">
              <Icon className="h-4 w-4" />
            </span>
            <p className="text-sm font-semibold text-ink">{titleCase(connector.source)}</p>
          </div>
          <StatusBadge status={connector.status} />
        </div>

        <div className="flex flex-col gap-1 text-xs text-ink-muted">
          <p>
            {connector.lastSyncedAt ? (
              <>Last sync: {formatRelativeTime(connector.lastSyncedAt)}</>
            ) : (
              "Never synced"
            )}
          </p>
          {summary && <p className="truncate">{summary}</p>}
        </div>

        <div className="flex gap-2 pt-1">
          <Button variant="secondary" size="sm" onClick={() => onView(connector)}>
            View
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => onSync(connector)}
            isLoading={isSyncing}
            disabled={connector.status === "disconnected"}
            className="gap-1.5"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Sync now
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
