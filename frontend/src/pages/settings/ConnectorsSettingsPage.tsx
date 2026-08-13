import { useQuery } from "@tanstack/react-query";
import { Card } from "@/components/ui/Card";
import { StatusBadge } from "@/components/data/StatusBadge";
import { DataTable, type DataTableColumn } from "@/components/data/DataTable";
import { Button } from "@/components/ui/Button";
import { listConnectors } from "@/api/connectors";
import type { Connector } from "@/types/connector";
import { formatRelativeTime } from "@/utils/date";
import { titleCase } from "@/utils/format";

export function ConnectorsSettingsPage() {
  const connectorsQuery = useQuery({ queryKey: ["connectors"], queryFn: listConnectors });

  const columns: DataTableColumn<Connector>[] = [
    {
      key: "source",
      header: "Connector",
      render: (row) => <span className="font-medium text-ink">{titleCase(row.source)}</span>,
    },
    { key: "status", header: "Status", render: (row) => <StatusBadge status={row.status} /> },
    {
      key: "lastSyncedAt",
      header: "Last sync",
      render: (row) => (row.lastSyncedAt ? formatRelativeTime(row.lastSyncedAt) : "Never"),
    },
    {
      key: "actions",
      header: "",
      render: () => (
        <Button size="sm" variant="secondary">
          Configure
        </Button>
      ),
    },
  ];

  return (
    <Card>
      <DataTable
        columns={columns}
        rows={connectorsQuery.data ?? []}
        rowKey={(row) => row.id}
        isLoading={connectorsQuery.isLoading}
        isError={connectorsQuery.isError}
        onRetry={() => connectorsQuery.refetch()}
      />
    </Card>
  );
}
