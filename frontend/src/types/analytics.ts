export interface TimeSeriesPoint {
  date: string;
  value: number;
}

export interface IncidentVolumePoint {
  date: string;
  opened: number;
  resolved: number;
}

export interface SeverityBreakdown {
  severity: "critical" | "high" | "medium" | "low";
  count: number;
}

export interface ServiceIncidentCount {
  service: string;
  count: number;
}

export interface AnalyticsSummary {
  mttrMinutes: number;
  mttaMinutes: number;
  incidentVolume: IncidentVolumePoint[];
  severityBreakdown: SeverityBreakdown[];
  incidentsByService: ServiceIncidentCount[];
  repeatedIncidentRate: number;
  knowledgeRetrievalPrecision: number;
  agentSuccessRate: TimeSeriesPoint[];
}
