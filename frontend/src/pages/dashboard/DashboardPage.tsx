import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { AlertCircle, ShieldAlert, CheckCircle2, BookOpen, Plug, Bot } from "lucide-react";
import {
  AreaChart,
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { PageHeader } from "@/components/layout/PageHeader";
import { MetricCard } from "@/components/data/MetricCard";
import { DataTable, type DataTableColumn } from "@/components/data/DataTable";
import { SeverityBadge } from "@/components/data/SeverityBadge";
import { StatusBadge } from "@/components/data/StatusBadge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { listIncidents } from "@/api/incidents";
import { listConnectors } from "@/api/connectors";
import { listAgentStats } from "@/api/agents";
import { getAnalyticsSummary } from "@/api/analytics";
import { listKnowledgeDocuments } from "@/api/knowledge";
import { listRecentActivity } from "@/api/activity";
import type { Incident } from "@/types/incident";
import { formatRelativeTime } from "@/utils/date";

const CHART_COLORS = {
  opened: "#94A3B8",
  resolved: "#2563EB",
  critical: "#DC2626",
  high: "#D97706",
  medium: "#64748B",
  low: "#93C5FD",
};

export function DashboardPage() {
  const navigate = useNavigate();

  const incidentsQuery = useQuery({
    queryKey: ["incidents", "dashboard"],
    queryFn: () => listIncidents({ page: 1, pageSize: 6 }),
  });
  const connectorsQuery = useQuery({ queryKey: ["connectors"], queryFn: listConnectors });
  const agentsQuery = useQuery({ queryKey: ["agents", "stats"], queryFn: listAgentStats });
  const analyticsQuery = useQuery({ queryKey: ["analytics", "summary"], queryFn: getAnalyticsSummary });
  const knowledgeQuery = useQuery({
    queryKey: ["knowledge", "dashboard"],
    queryFn: () => listKnowledgeDocuments({ page: 1, pageSize: 1 }),
  });

  const incidents = incidentsQuery.data?.items ?? [];
  const openCount = incidents.filter((i) => i.status === "open" || i.status === "investigating").length;
  const criticalCount = incidents.filter((i) => i.severity === "critical").length;
  const resolvedCount = incidents.filter((i) => i.status === "resolved" || i.status === "closed").length;

  const columns: DataTableColumn<Incident>[] = [
    {
      key: "displayId",
      header: "Incident",
      render: (row) => <span className="font-medium text-ink">{row.displayId}</span>,
    },
    { key: "severity", header: "Severity", render: (row) => <SeverityBadge severity={row.severity} /> },
    { key: "status", header: "Status", render: (row) => <StatusBadge status={row.status} /> },
    { key: "service", header: "Service", render: (row) => row.service },
    {
      key: "createdAt",
      header: "Created",
      render: (row) => <span className="text-ink-muted">{formatRelativeTime(row.createdAt)}</span>,
    },
    {
      key: "assignee",
      header: "Assignee",
      render: (row) => <span className="text-ink-muted">{row.assignee?.name ?? "Unassigned"}</span>,
    },
  ];

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Dashboard"
        description="Current state of the engineering environment across incidents, knowledge, and agents."
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <MetricCard label="Open Incidents" value={openCount} icon={AlertCircle} tone="neutral" />
        <MetricCard label="Critical Incidents" value={criticalCount} icon={ShieldAlert} tone="critical" />
        <MetricCard label="Resolved Incidents" value={resolvedCount} icon={CheckCircle2} tone="success" />
        <MetricCard
          label="Knowledge Documents"
          value={knowledgeQuery.data?.total ?? "—"}
          icon={BookOpen}
        />
        <MetricCard
          label="Connected Sources"
          value={connectorsQuery.data?.filter((c) => c.status === "active").length ?? "—"}
          icon={Plug}
        />
        <MetricCard
          label="Active Agents"
          value={agentsQuery.data?.filter((a) => a.status === "healthy").length ?? "—"}
          icon={Bot}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Incident volume — last 14 days</CardTitle>
          </CardHeader>
          <CardContent className="h-64">
            {analyticsQuery.data && (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={analyticsQuery.data.incidentVolume} margin={{ left: -20, right: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" vertical={false} />
                  <XAxis dataKey="date" tick={{ fontSize: 11, fill: "#64748B" }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 11, fill: "#64748B" }} axisLine={false} tickLine={false} width={28} />
                  <RechartsTooltip
                    contentStyle={{ fontSize: 12, borderRadius: 6, borderColor: "#E2E8F0" }}
                  />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Area
                    type="monotone"
                    dataKey="opened"
                    name="Opened"
                    stroke={CHART_COLORS.opened}
                    fill={CHART_COLORS.opened}
                    fillOpacity={0.12}
                    strokeWidth={2}
                    isAnimationActive={false}
                  />
                  <Area
                    type="monotone"
                    dataKey="resolved"
                    name="Resolved"
                    stroke={CHART_COLORS.resolved}
                    fill={CHART_COLORS.resolved}
                    fillOpacity={0.12}
                    strokeWidth={2}
                    isAnimationActive={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Severity distribution</CardTitle>
          </CardHeader>
          <CardContent className="h-64">
            {analyticsQuery.data && (
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={analyticsQuery.data.severityBreakdown}
                    dataKey="count"
                    nameKey="severity"
                    innerRadius={50}
                    outerRadius={80}
                    paddingAngle={2}
                    isAnimationActive={false}
                  >
                    {analyticsQuery.data.severityBreakdown.map((entry) => (
                      <Cell key={entry.severity} fill={CHART_COLORS[entry.severity]} />
                    ))}
                  </Pie>
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <RechartsTooltip contentStyle={{ fontSize: 12, borderRadius: 6, borderColor: "#E2E8F0" }} />
                </PieChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Recent incidents</CardTitle>
        </CardHeader>
        <DataTable
          columns={columns}
          rows={incidents}
          rowKey={(row) => row.id}
          isLoading={incidentsQuery.isLoading}
          isError={incidentsQuery.isError}
          onRetry={() => incidentsQuery.refetch()}
          onRowClick={(row) => navigate(`/incidents/${row.id}`)}
        />
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Incidents by service</CardTitle>
          </CardHeader>
          <CardContent className="h-56">
            {analyticsQuery.data && (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={analyticsQuery.data.incidentsByService} margin={{ left: -20, right: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" vertical={false} />
                  <XAxis dataKey="service" tick={{ fontSize: 11, fill: "#64748B" }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 11, fill: "#64748B" }} axisLine={false} tickLine={false} width={28} />
                  <RechartsTooltip contentStyle={{ fontSize: 12, borderRadius: 6, borderColor: "#E2E8F0" }} />
                  <Bar dataKey="count" name="Incidents" fill="#2563EB" radius={[3, 3, 0, 0]} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>System activity</CardTitle>
          </CardHeader>
          <CardContent>
            <SystemActivityFeed />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function SystemActivityFeed() {
  const activityQuery = useQuery({ queryKey: ["activity", "recent"], queryFn: listRecentActivity });

  if (activityQuery.isLoading) {
    return <p className="text-sm text-ink-muted">Loading activity…</p>;
  }

  return (
    <ul className="flex flex-col gap-3">
      {(activityQuery.data ?? []).map((item) => (
        <li key={item.id} className="flex items-start justify-between gap-3 text-sm">
          <div>
            <p className="text-ink">{item.label}</p>
            <p className="text-xs text-ink-muted">{item.meta}</p>
          </div>
          <span className="shrink-0 text-xs text-ink-subtle">{formatRelativeTime(item.occurredAt)}</span>
        </li>
      ))}
    </ul>
  );
}
