import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Clock, Timer, Repeat, Target } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { MetricCard } from "@/components/data/MetricCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { ErrorState } from "@/components/ui/ErrorState";
import { getAnalyticsSummary } from "@/api/analytics";
import { formatMinutes, formatPercent } from "@/utils/format";

const SEVERITY_COLORS = { critical: "#DC2626", high: "#D97706", medium: "#64748B", low: "#93C5FD" };

export function AnalyticsPage() {
  const analyticsQuery = useQuery({ queryKey: ["analytics", "summary"], queryFn: getAnalyticsSummary });

  if (analyticsQuery.isLoading) return <LoadingState label="Loading analytics…" />;
  if (analyticsQuery.isError || !analyticsQuery.data) {
    return <ErrorState onRetry={() => analyticsQuery.refetch()} />;
  }

  const data = analyticsQuery.data;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Analytics" description="Engineering and incident intelligence metrics." />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetricCard label="MTTR" value={formatMinutes(data.mttrMinutes)} icon={Timer} />
        <MetricCard label="MTTA" value={formatMinutes(data.mttaMinutes)} icon={Clock} />
        <MetricCard label="Repeated incident rate" value={formatPercent(data.repeatedIncidentRate)} icon={Repeat} />
        <MetricCard
          label="Knowledge retrieval precision"
          value={formatPercent(data.knowledgeRetrievalPrecision)}
          icon={Target}
          tone="success"
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Incidents by severity</CardTitle>
          </CardHeader>
          <CardContent className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                data={data.severityBreakdown}
                dataKey="count"
                nameKey="severity"
                innerRadius={50}
                outerRadius={80}
                paddingAngle={2}
                isAnimationActive={false}
              >
                  {data.severityBreakdown.map((entry) => (
                    <Cell key={entry.severity} fill={SEVERITY_COLORS[entry.severity]} />
                  ))}
                </Pie>
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <RechartsTooltip contentStyle={{ fontSize: 12, borderRadius: 6, borderColor: "#E2E8F0" }} />
              </PieChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Incidents by service</CardTitle>
          </CardHeader>
          <CardContent className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.incidentsByService} margin={{ left: -20, right: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" vertical={false} />
                <XAxis dataKey="service" tick={{ fontSize: 11, fill: "#64748B" }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 11, fill: "#64748B" }} axisLine={false} tickLine={false} width={28} />
                <RechartsTooltip contentStyle={{ fontSize: 12, borderRadius: 6, borderColor: "#E2E8F0" }} />
                <Bar dataKey="count" name="Incidents" fill="#2563EB" radius={[3, 3, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Agent execution success rate — last 14 days</CardTitle>
        </CardHeader>
        <CardContent className="h-56">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data.agentSuccessRate} margin={{ left: -20, right: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" vertical={false} />
              <XAxis dataKey="date" tick={{ fontSize: 11, fill: "#64748B" }} axisLine={false} tickLine={false} />
              <YAxis
                domain={[0.8, 1]}
                tickFormatter={(v) => `${Math.round(v * 100)}%`}
                tick={{ fontSize: 11, fill: "#64748B" }}
                axisLine={false}
                tickLine={false}
                width={40}
              />
              <RechartsTooltip
                formatter={(value: number) => formatPercent(value, 1)}
                contentStyle={{ fontSize: 12, borderRadius: 6, borderColor: "#E2E8F0" }}
              />
              <Line
                type="monotone"
                dataKey="value"
                name="Success rate"
                stroke="#2563EB"
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>
    </div>
  );
}
