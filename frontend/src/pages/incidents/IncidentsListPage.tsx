import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/layout/PageHeader";
import { SearchBar } from "@/components/data/SearchBar";
import { FilterBar } from "@/components/data/FilterBar";
import { Select } from "@/components/ui/Select";
import { Card } from "@/components/ui/Card";
import { DataTable, type DataTableColumn } from "@/components/data/DataTable";
import { Pagination } from "@/components/data/Pagination";
import { SeverityBadge } from "@/components/data/SeverityBadge";
import { StatusBadge } from "@/components/data/StatusBadge";
import { useDebounce } from "@/hooks/useDebounce";
import { listIncidents } from "@/api/incidents";
import type { Incident, IncidentSeverity, IncidentStatus } from "@/types/incident";
import { formatDateTime, formatRelativeTime } from "@/utils/date";

const SEVERITIES: IncidentSeverity[] = ["critical", "high", "medium", "low"];
const STATUSES: IncidentStatus[] = ["open", "investigating", "monitoring", "resolved", "closed"];
const SERVICES = ["Payment API", "Payments", "Auth", "Ingestion", "Connectors", "Database", "Retrieval"];

export function IncidentsListPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [severity, setSeverity] = useState<IncidentSeverity | "">("");
  const [status, setStatus] = useState<IncidentStatus | "">("");
  const [service, setService] = useState<string>("");
  const [page, setPage] = useState(1);
  const [sortKey, setSortKey] = useState<string | undefined>("createdAt");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const debouncedSearch = useDebounce(search, 300);
  const pageSize = 10;

  const filters = useMemo(
    () => ({
      search: debouncedSearch || undefined,
      severity: severity ? [severity] : undefined,
      status: status ? [status] : undefined,
      service: service ? [service] : undefined,
      page,
      pageSize,
      sortBy: sortKey as keyof Incident | undefined,
      sortDir,
    }),
    [debouncedSearch, severity, status, service, page, sortKey, sortDir],
  );

  const incidentsQuery = useQuery({
    queryKey: ["incidents", filters],
    queryFn: () => listIncidents(filters),
  });

  const activeFilterCount = [severity, status, service].filter(Boolean).length;

  function handleSortChange(key: string) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  function handleClearFilters() {
    setSeverity("");
    setStatus("");
    setService("");
    setPage(1);
  }

  const columns: DataTableColumn<Incident>[] = [
    {
      key: "displayId",
      header: "ID",
      sortable: true,
      render: (row) => <span className="font-medium text-ink">{row.displayId}</span>,
    },
    {
      key: "title",
      header: "Title",
      render: (row) => <span className="max-w-xs truncate text-ink">{row.title}</span>,
      className: "max-w-xs",
    },
    { key: "severity", header: "Severity", sortable: true, render: (row) => <SeverityBadge severity={row.severity} /> },
    { key: "status", header: "Status", sortable: true, render: (row) => <StatusBadge status={row.status} /> },
    { key: "service", header: "Service", sortable: true, render: (row) => row.service },
    {
      key: "assignee",
      header: "Assignee",
      render: (row) => <span className="text-ink-muted">{row.assignee?.name ?? "Unassigned"}</span>,
    },
    {
      key: "createdAt",
      header: "Created",
      sortable: true,
      render: (row) => (
        <span title={formatDateTime(row.createdAt)} className="text-ink-muted">
          {formatRelativeTime(row.createdAt)}
        </span>
      ),
    },
    {
      key: "updatedAt",
      header: "Updated",
      sortable: true,
      render: (row) => (
        <span title={formatDateTime(row.updatedAt)} className="text-ink-muted">
          {formatRelativeTime(row.updatedAt)}
        </span>
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-4">
      <PageHeader title="Incidents" description="Track and investigate active and historical incidents." />

      <div className="flex flex-col gap-3">
        <SearchBar
          value={search}
          onChange={(value) => {
            setSearch(value);
            setPage(1);
          }}
          placeholder="Search by title or ID…"
          className="max-w-sm"
        />

        <FilterBar activeCount={activeFilterCount} onClear={handleClearFilters}>
          <Select
            value={severity}
            onChange={(e) => {
              setSeverity(e.target.value as IncidentSeverity | "");
              setPage(1);
            }}
            className="w-40"
          >
            <option value="">All severities</option>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {s[0].toUpperCase() + s.slice(1)}
              </option>
            ))}
          </Select>

          <Select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as IncidentStatus | "");
              setPage(1);
            }}
            className="w-40"
          >
            <option value="">All statuses</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s[0].toUpperCase() + s.slice(1)}
              </option>
            ))}
          </Select>

          <Select
            value={service}
            onChange={(e) => {
              setService(e.target.value);
              setPage(1);
            }}
            className="w-44"
          >
            <option value="">All services</option>
            {SERVICES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        </FilterBar>
      </div>

      <Card>
        <DataTable
          columns={columns}
          rows={incidentsQuery.data?.items ?? []}
          rowKey={(row) => row.id}
          isLoading={incidentsQuery.isLoading}
          isError={incidentsQuery.isError}
          onRetry={() => incidentsQuery.refetch()}
          onRowClick={(row) => navigate(`/incidents/${row.id}`)}
          sortKey={sortKey}
          sortDir={sortDir}
          onSortChange={handleSortChange}
          emptyTitle="No incidents found"
          emptyDescription="Try adjusting your search or filters."
        />
        <Pagination
          page={page}
          pageSize={pageSize}
          total={incidentsQuery.data?.total ?? 0}
          onPageChange={setPage}
        />
      </Card>
    </div>
  );
}
