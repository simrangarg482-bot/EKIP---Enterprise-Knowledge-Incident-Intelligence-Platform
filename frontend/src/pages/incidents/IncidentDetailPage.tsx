import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { Sparkles, FileText } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { SeverityBadge } from "@/components/data/SeverityBadge";
import { StatusBadge } from "@/components/data/StatusBadge";
import { Tabs } from "@/components/ui/Tabs";
import { Card, CardContent } from "@/components/ui/Card";
import { LoadingState } from "@/components/ui/LoadingState";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { Button } from "@/components/ui/Button";
import { Timeline } from "@/components/domain/Timeline";
import { AIAnalysisPanel } from "@/components/domain/AIAnalysisPanel";
import { CitationList } from "@/components/domain/CitationList";
import {
  getAiInvestigation,
  getIncident,
  getIncidentComments,
  getIncidentTimeline,
  addIncidentNote,
} from "@/api/incidents";
import { formatDateTime, formatRelativeTime } from "@/utils/date";
import { formatPercent } from "@/utils/format";

const TABS = [
  { key: "timeline", label: "Timeline" },
  { key: "investigation", label: "AI Investigation" },
  { key: "knowledge", label: "Related Knowledge" },
  { key: "similar", label: "Similar Incidents" },
  { key: "activity", label: "Activity" },
];

export function IncidentDetailPage() {
  const { id = "" } = useParams();
  const [activeTab, setActiveTab] = useState("timeline");
  const [note, setNote] = useState("");
  const queryClient = useQueryClient();

  const incidentQuery = useQuery({ queryKey: ["incident", id], queryFn: () => getIncident(id) });
  const timelineQuery = useQuery({ queryKey: ["incident", id, "timeline"], queryFn: () => getIncidentTimeline(id) });
  const commentsQuery = useQuery({ queryKey: ["incident", id, "comments"], queryFn: () => getIncidentComments(id) });
  const investigationQuery = useQuery({
    queryKey: ["incident", id, "investigation"],
    queryFn: () => getAiInvestigation(id),
    enabled: activeTab === "investigation" || activeTab === "knowledge" || activeTab === "similar",
  });

  const addNoteMutation = useMutation({
    mutationFn: (body: string) => addIncidentNote(id, body),
    onSuccess: () => {
      setNote("");
      queryClient.invalidateQueries({ queryKey: ["incident", id, "comments"] });
    },
  });

  if (incidentQuery.isLoading) return <LoadingState label="Loading incident…" />;
  if (incidentQuery.isError || !incidentQuery.data) {
    return <ErrorState onRetry={() => incidentQuery.refetch()} />;
  }

  const incident = incidentQuery.data;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        breadcrumbs={[{ label: "Incidents", path: "/incidents" }, { label: incident.displayId }]}
        title={incident.title}
        description={incident.description}
        actions={
          <>
            <SeverityBadge severity={incident.severity} />
            <StatusBadge status={incident.status} />
          </>
        }
      />

      <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-ink-muted">
        <span>
          Service: <span className="font-medium text-ink">{incident.service}</span>
        </span>
        <span title={formatDateTime(incident.createdAt)}>
          Created: <span className="font-medium text-ink">{formatRelativeTime(incident.createdAt)}</span>
        </span>
        <span>
          Assigned to: <span className="font-medium text-ink">{incident.assignee?.name ?? "Unassigned"}</span>
        </span>
      </div>

      <Tabs items={TABS} activeKey={activeTab} onChange={setActiveTab} />

      {activeTab === "timeline" && (
        <Card>
          <CardContent>
            {timelineQuery.isLoading && <LoadingState label="Loading timeline…" />}
            {timelineQuery.data && <Timeline entries={timelineQuery.data} />}
          </CardContent>
        </Card>
      )}

      {activeTab === "investigation" && (
        <>
          {investigationQuery.isLoading && <LoadingState label="Running investigation agents…" />}
          {investigationQuery.data && <AIAnalysisPanel investigation={investigationQuery.data} />}
          {!investigationQuery.isLoading && !investigationQuery.data && (
            <Card>
              <CardContent>
                <EmptyState
                  icon={Sparkles}
                  title="No AI investigation yet"
                  description="Run the investigation pipeline to generate root cause hypotheses for this incident."
                />
              </CardContent>
            </Card>
          )}
        </>
      )}

      {activeTab === "knowledge" && (
        <Card>
          <CardContent>
            {investigationQuery.isLoading && <LoadingState label="Retrieving related knowledge…" />}
            {investigationQuery.data && investigationQuery.data.relevantKnowledge.length > 0 ? (
              <CitationList sources={investigationQuery.data.relevantKnowledge} />
            ) : (
              !investigationQuery.isLoading && (
                <EmptyState icon={FileText} title="No related knowledge found" />
              )
            )}
          </CardContent>
        </Card>
      )}

      {activeTab === "similar" && (
        <Card>
          <CardContent>
            {investigationQuery.isLoading && <LoadingState label="Searching similar incidents…" />}
            {investigationQuery.data && investigationQuery.data.similarIncidents.length > 0 ? (
              <ul className="flex flex-col gap-2">
                {investigationQuery.data.similarIncidents.map(({ incident: similar, similarityScore, matchedOn }) => (
                  <li key={similar.id}>
                    <Link
                      to={`/incidents/${similar.id}`}
                      className="flex items-center justify-between gap-3 rounded-md border border-border bg-white px-3 py-2.5 hover:border-accent-border hover:bg-accent-subtle"
                    >
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-medium text-ink-muted">{similar.displayId}</span>
                          <SeverityBadge severity={similar.severity} />
                        </div>
                        <p className="mt-0.5 text-sm text-ink">{similar.title}</p>
                        <p className="text-xs text-ink-subtle">Matched on {matchedOn}</p>
                      </div>
                      <span className="text-xs font-medium text-ink-muted">{formatPercent(similarityScore)}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              !investigationQuery.isLoading && <EmptyState title="No similar incidents found" />
            )}
          </CardContent>
        </Card>
      )}

      {activeTab === "activity" && (
        <Card>
          <CardContent className="flex flex-col gap-4">
            {commentsQuery.data?.map((comment) => (
              <div key={comment.id} className="flex flex-col gap-0.5">
                <div className="flex items-baseline gap-2">
                  <span className="text-sm font-medium text-ink">{comment.author}</span>
                  <span className="text-xs text-ink-subtle">{formatRelativeTime(comment.createdAt)}</span>
                </div>
                <p className="text-sm text-ink-muted">{comment.body}</p>
              </div>
            ))}
            {commentsQuery.data?.length === 0 && <p className="text-sm text-ink-muted">No comments yet.</p>}

            <form
              className="flex gap-2 border-t border-border pt-4"
              onSubmit={(e) => {
                e.preventDefault();
                if (note.trim()) addNoteMutation.mutate(note.trim());
              }}
            >
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Add a note…"
                className="h-9 flex-1 rounded-md border border-border bg-white px-3 text-sm text-ink placeholder:text-ink-subtle focus-visible:border-accent"
              />
              <Button type="submit" size="sm" isLoading={addNoteMutation.isPending}>
                Post
              </Button>
            </form>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
