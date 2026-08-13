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
import { Badge } from "@/components/ui/Badge";
import { useDebounce } from "@/hooks/useDebounce";
import { listKnowledgeDocuments } from "@/api/knowledge";
import type { KnowledgeDocument, KnowledgeSource } from "@/types/knowledge";
import { formatDateTime, formatRelativeTime } from "@/utils/date";
import { titleCase } from "@/utils/format";

const SOURCES: KnowledgeSource[] = ["github", "slack", "confluence", "jira", "manual"];

export function KnowledgeListPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [source, setSource] = useState<KnowledgeSource | "">("");
  const [page, setPage] = useState(1);
  const pageSize = 10;

  const debouncedSearch = useDebounce(search, 300);

  const filters = useMemo(
    () => ({
      search: debouncedSearch || undefined,
      source: source ? [source] : undefined,
      page,
      pageSize,
    }),
    [debouncedSearch, source, page],
  );

  const documentsQuery = useQuery({
    queryKey: ["knowledge", filters],
    queryFn: () => listKnowledgeDocuments(filters),
  });

  const activeFilterCount = source ? 1 : 0;

  const columns: DataTableColumn<KnowledgeDocument>[] = [
    {
      key: "title",
      header: "Document",
      render: (row) => <span className="font-medium text-ink">{row.title ?? "(untitled)"}</span>,
    },
    {
      key: "source",
      header: "Source",
      render: (row) => <Badge tone="neutral">{titleCase(row.source)}</Badge>,
    },
    {
      key: "status",
      header: "Status",
      render: (row) => <Badge tone={row.status === "published" ? "success" : "warning"}>{titleCase(row.status)}</Badge>,
    },
    {
      key: "updatedAt",
      header: "Updated",
      render: (row) => (
        <span title={formatDateTime(row.updatedAt)} className="text-ink-muted">
          {formatRelativeTime(row.updatedAt)}
        </span>
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Knowledge Base"
        description="Browse knowledge ingested from connected sources -- GitHub, Slack, and manually reviewed runbooks."
      />

      <div className="flex flex-col gap-3">
        <SearchBar
          value={search}
          onChange={(value) => {
            setSearch(value);
            setPage(1);
          }}
          placeholder="Search documents…"
          className="max-w-sm"
        />

        <FilterBar
          activeCount={activeFilterCount}
          onClear={() => {
            setSource("");
            setPage(1);
          }}
        >
          <Select
            value={source}
            onChange={(e) => {
              setSource(e.target.value as KnowledgeSource | "");
              setPage(1);
            }}
            className="w-40"
          >
            <option value="">All sources</option>
            {SOURCES.map((s) => (
              <option key={s} value={s}>
                {titleCase(s)}
              </option>
            ))}
          </Select>
        </FilterBar>
      </div>

      <Card>
        <DataTable
          columns={columns}
          rows={documentsQuery.data?.items ?? []}
          rowKey={(row) => row.id}
          isLoading={documentsQuery.isLoading}
          isError={documentsQuery.isError}
          onRetry={() => documentsQuery.refetch()}
          onRowClick={(row) => navigate(`/knowledge/${row.id}`)}
          emptyTitle="No documents found"
          emptyDescription="Connect a source and sync it, or try a different search term."
        />
        <Pagination page={page} pageSize={pageSize} total={documentsQuery.data?.total ?? 0} onPageChange={setPage} />
      </Card>
    </div>
  );
}
