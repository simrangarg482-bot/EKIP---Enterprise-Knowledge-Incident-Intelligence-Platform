import type { AnalyticsSummary } from "@/types/analytics";

function lastNDays(n: number): string[] {
  const days: string[] = [];
  const base = new Date();
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(base.getTime() - i * 86_400_000);
    days.push(d.toISOString().slice(0, 10));
  }
  return days;
}

const days = lastNDays(14);

export const mockAnalyticsSummary: AnalyticsSummary = {
  mttrMinutes: 46,
  mttaMinutes: 4.2,
  incidentVolume: days.map((date, i) => ({
    date,
    opened: 3 + ((i * 2) % 6),
    resolved: 2 + ((i * 3) % 6),
  })),
  severityBreakdown: [
    { severity: "critical", count: 6 },
    { severity: "high", count: 14 },
    { severity: "medium", count: 27 },
    { severity: "low", count: 19 },
  ],
  incidentsByService: [
    { service: "Payment API", count: 18 },
    { service: "Auth", count: 11 },
    { service: "Database", count: 9 },
    { service: "Connectors", count: 8 },
    { service: "Retrieval", count: 6 },
    { service: "Ingestion", count: 5 },
  ],
  repeatedIncidentRate: 0.23,
  knowledgeRetrievalPrecision: 0.87,
  agentSuccessRate: days.map((date, i) => ({ date, value: 0.9 + ((i % 5) * 0.015) })),
};
