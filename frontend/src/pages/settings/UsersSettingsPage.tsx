import { useQuery } from "@tanstack/react-query";
import { UserPlus } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { DataTable, type DataTableColumn } from "@/components/data/DataTable";
import { listOrgUsers } from "@/api/tenancy";
import type { OrgUser } from "@/types/tenancy";
import { formatRelativeTime } from "@/utils/date";
import { titleCase } from "@/utils/format";

const ROLE_TONE = { owner: "accent", admin: "info", member: "neutral", viewer: "neutral" } as const;

export function UsersSettingsPage() {
  const usersQuery = useQuery({ queryKey: ["users"], queryFn: listOrgUsers });

  const columns: DataTableColumn<OrgUser>[] = [
    {
      key: "name",
      header: "User",
      render: (row) => (
        <div>
          <p className="font-medium text-ink">{row.name}</p>
          <p className="text-xs text-ink-muted">{row.email}</p>
        </div>
      ),
    },
    { key: "role", header: "Role", render: (row) => <Badge tone={ROLE_TONE[row.role]}>{titleCase(row.role)}</Badge> },
    {
      key: "status",
      header: "Status",
      render: (row) => <Badge tone={row.status === "active" ? "success" : row.status === "invited" ? "warning" : "critical"}>{titleCase(row.status)}</Badge>,
    },
    {
      key: "lastActiveAt",
      header: "Last active",
      render: (row) => (row.lastActiveAt ? formatRelativeTime(row.lastActiveAt) : "—"),
    },
  ];

  return (
    <Card>
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h3 className="text-sm font-semibold text-ink">Users</h3>
        <Button size="sm" variant="primary" className="gap-1.5">
          <UserPlus className="h-3.5 w-3.5" />
          Invite user
        </Button>
      </div>
      <DataTable
        columns={columns}
        rows={usersQuery.data ?? []}
        rowKey={(row) => row.id}
        isLoading={usersQuery.isLoading}
        isError={usersQuery.isError}
        onRetry={() => usersQuery.refetch()}
      />
    </Card>
  );
}
