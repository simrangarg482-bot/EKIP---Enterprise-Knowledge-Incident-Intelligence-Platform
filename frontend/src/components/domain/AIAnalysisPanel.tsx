import { Sparkles, ChevronRight } from "lucide-react";
import type { AiInvestigation } from "@/types/incident";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { SeverityBadge } from "@/components/data/SeverityBadge";
import { CitationList } from "./CitationList";
import { formatDateTime, formatRelativeTime } from "@/utils/date";
import { formatPercent } from "@/utils/format";
import { Link } from "react-router-dom";
import { cn } from "@/utils/cn";

function ConfidenceBar({ value }: { value: number }) {
  const tone = value >= 0.75 ? "bg-success" : value >= 0.5 ? "bg-warning" : "bg-critical";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-slate-100">
        <div className={cn("h-full rounded-full", tone)} style={{ width: `${value * 100}%` }} />
      </div>
      <span className="text-xs font-medium text-ink-muted">{formatPercent(value)}</span>
    </div>
  );
}

export function AIAnalysisPanel({ investigation }: { investigation: AiInvestigation }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-accent-subtle text-accent">
            <Sparkles className="h-3.5 w-3.5" />
          </span>
          <CardTitle>AI Analysis</CardTitle>
        </div>
        <div className="flex items-center gap-3 text-xs text-ink-muted">
          <span title={formatDateTime(investigation.generatedAt)}>
            Generated {formatRelativeTime(investigation.generatedAt)}
          </span>
          <span className="text-ink-subtle">·</span>
          <span>{investigation.model}</span>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <section>
          <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-ink-subtle">Summary</p>
          <p className="text-sm leading-relaxed text-ink">{investigation.summary}</p>
        </section>

        <section>
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs font-medium uppercase tracking-wide text-ink-subtle">Root cause hypotheses</p>
            <ConfidenceBar value={investigation.confidence} />
          </div>
          <ol className="flex flex-col gap-3">
            {investigation.rootCauseHypotheses.map((hypothesis, index) => (
              <li key={index} className="rounded-md border border-border bg-slate-50 px-3 py-2.5">
                <div className="flex items-start justify-between gap-3">
                  <p className="text-sm font-medium text-ink">{hypothesis.summary}</p>
                  <span className="shrink-0 text-xs font-medium text-ink-muted">
                    {formatPercent(hypothesis.confidence)}
                  </span>
                </div>
                <ul className="mt-1.5 flex flex-col gap-0.5">
                  {hypothesis.evidence.map((item, i) => (
                    <li key={i} className="flex items-start gap-1.5 text-xs text-ink-muted">
                      <ChevronRight className="mt-0.5 h-3 w-3 shrink-0 text-ink-subtle" />
                      {item}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        </section>

        <section>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-subtle">Recommended actions</p>
          <ul className="flex flex-col gap-1.5">
            {investigation.recommendedActions.map((action, index) => (
              <li key={index} className="flex items-start gap-2 text-sm text-ink">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                {action}
              </li>
            ))}
          </ul>
        </section>

        {investigation.similarIncidents.length > 0 && (
          <section>
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-subtle">Similar incidents</p>
            <ul className="flex flex-col gap-1.5">
              {investigation.similarIncidents.map(({ incident, similarityScore, matchedOn }) => (
                <li key={incident.id}>
                  <Link
                    to={`/incidents/${incident.id}`}
                    className="flex items-center justify-between gap-3 rounded-md border border-border bg-white px-3 py-2 hover:border-accent-border hover:bg-accent-subtle"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-medium text-ink-muted">{incident.displayId}</span>
                        <SeverityBadge severity={incident.severity} />
                      </div>
                      <p className="mt-0.5 truncate text-sm text-ink">{incident.title}</p>
                      <p className="text-xs text-ink-subtle">Matched on {matchedOn}</p>
                    </div>
                    <span className="shrink-0 text-xs font-medium text-ink-muted">
                      {formatPercent(similarityScore)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section>
          <CitationList sources={investigation.relevantKnowledge} />
        </section>
      </CardContent>
    </Card>
  );
}
