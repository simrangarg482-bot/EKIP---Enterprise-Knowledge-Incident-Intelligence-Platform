import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/Badge";
import { Card, CardContent } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Button } from "@/components/ui/Button";
import { getKnowledgeDocument } from "@/api/knowledge";
import { formatDateTime } from "@/utils/date";
import { titleCase } from "@/utils/format";

export function KnowledgeDetailPage() {
  const { id = "" } = useParams();
  const documentQuery = useQuery({ queryKey: ["knowledge", id], queryFn: () => getKnowledgeDocument(id) });

  if (documentQuery.isLoading) return <LoadingState label="Loading document…" />;
  if (documentQuery.isError || !documentQuery.data) return <ErrorState onRetry={() => documentQuery.refetch()} />;

  const doc = documentQuery.data;
  const title = doc.title ?? "(untitled)";

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        breadcrumbs={[{ label: "Knowledge", path: "/knowledge" }, { label: title }]}
        title={title}
        actions={
          doc.sourceUrl && (
            <Button
              variant="secondary"
              size="sm"
              className="gap-1.5"
              onClick={() => window.open(doc.sourceUrl!, "_blank")}
            >
              <ExternalLink className="h-3.5 w-3.5" />
              Open source
            </Button>
          )
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="neutral">{titleCase(doc.source)}</Badge>
        <Badge tone={doc.status === "published" ? "success" : "warning"}>{titleCase(doc.status)}</Badge>
        <Badge tone="neutral">v{doc.version}</Badge>
      </div>

      <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-ink-muted">
        <span>Updated: {formatDateTime(doc.updatedAt)}</span>
        <span>Created: {formatDateTime(doc.createdAt)}</span>
      </div>

      <Card>
        <CardContent>
          {doc.content ? (
            <p className="whitespace-pre-line text-sm leading-relaxed text-ink">{doc.content}</p>
          ) : (
            <p className="text-sm text-ink-muted">
              No preview content is available for this document. Open the source to view the full content.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
