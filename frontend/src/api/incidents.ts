import { apiRequest, mockDelay } from "./client";
import { USE_MOCK_DATA } from "./config";
import type {
  AiInvestigation,
  Incident,
  IncidentComment,
  IncidentFilters,
  TimelineEntry,
} from "@/types/incident";
import type { Paginated } from "@/types/common";
import {
  mockAiInvestigations,
  mockComments,
  mockIncidents,
  mockTimeline,
} from "@/mocks/data/incidents";

function applyFilters(items: Incident[], filters: IncidentFilters): Incident[] {
  let result = [...items];

  if (filters.search) {
    const q = filters.search.toLowerCase();
    result = result.filter(
      (i) => i.title.toLowerCase().includes(q) || i.displayId.toLowerCase().includes(q),
    );
  }
  if (filters.severity?.length) {
    result = result.filter((i) => filters.severity!.includes(i.severity));
  }
  if (filters.status?.length) {
    result = result.filter((i) => filters.status!.includes(i.status));
  }
  if (filters.service?.length) {
    result = result.filter((i) => filters.service!.includes(i.service));
  }
  if (filters.dateFrom) {
    result = result.filter((i) => i.createdAt >= filters.dateFrom!);
  }
  if (filters.dateTo) {
    result = result.filter((i) => i.createdAt <= filters.dateTo!);
  }
  if (filters.sortBy) {
    const dir = filters.sortDir === "asc" ? 1 : -1;
    result.sort((a, b) => {
      const av = String(a[filters.sortBy!] ?? "");
      const bv = String(b[filters.sortBy!] ?? "");
      return av > bv ? dir : av < bv ? -dir : 0;
    });
  } else {
    result.sort((a, b) => (a.createdAt > b.createdAt ? -1 : 1));
  }
  return result;
}

export async function listIncidents(filters: IncidentFilters = {}): Promise<Paginated<Incident>> {
  if (USE_MOCK_DATA) {
    const filtered = applyFilters(mockIncidents, filters);
    const page = filters.page ?? 1;
    const pageSize = filters.pageSize ?? 20;
    const start = (page - 1) * pageSize;
    return mockDelay({
      items: filtered.slice(start, start + pageSize),
      total: filtered.length,
      page,
      pageSize,
    });
  }

  const params = new URLSearchParams();
  if (filters.search) params.set("search", filters.search);
  filters.severity?.forEach((s) => params.append("severity", s));
  filters.status?.forEach((s) => params.append("status", s));
  filters.service?.forEach((s) => params.append("service", s));
  if (filters.page) params.set("page", String(filters.page));
  if (filters.pageSize) params.set("page_size", String(filters.pageSize));

  return apiRequest<Paginated<Incident>>(`/incidents?${params.toString()}`);
}

export async function getIncident(id: string): Promise<Incident> {
  if (USE_MOCK_DATA) {
    const incident = mockIncidents.find((i) => i.id === id || i.displayId === id);
    if (!incident) throw { status: 404, message: "Incident not found" };
    return mockDelay(incident);
  }
  return apiRequest<Incident>(`/incidents/${id}`);
}

export async function getIncidentTimeline(id: string): Promise<TimelineEntry[]> {
  if (USE_MOCK_DATA) {
    return mockDelay(mockTimeline[id] ?? []);
  }
  return apiRequest<TimelineEntry[]>(`/incidents/${id}/timeline`);
}

export async function getIncidentComments(id: string): Promise<IncidentComment[]> {
  if (USE_MOCK_DATA) {
    return mockDelay(mockComments[id] ?? []);
  }
  return apiRequest<IncidentComment[]>(`/incidents/${id}/comments`);
}

export async function addIncidentNote(id: string, body: string): Promise<IncidentComment> {
  if (USE_MOCK_DATA) {
    const comment: IncidentComment = {
      id: `c-${Date.now()}`,
      incidentId: id,
      author: "You",
      body,
      createdAt: new Date().toISOString(),
    };
    return mockDelay(comment, 200);
  }
  return apiRequest<IncidentComment>(`/incidents/${id}/timeline`, { method: "POST", body: { body } });
}

export async function getAiInvestigation(id: string): Promise<AiInvestigation | null> {
  if (USE_MOCK_DATA) {
    return mockDelay(mockAiInvestigations[id] ?? null, 500);
  }
  return apiRequest<AiInvestigation>(`/incidents/${id}/investigate`);
}

export async function updateIncident(id: string, patch: Partial<Incident>): Promise<Incident> {
  if (USE_MOCK_DATA) {
    const incident = mockIncidents.find((i) => i.id === id);
    if (!incident) throw { status: 404, message: "Incident not found" };
    return mockDelay({ ...incident, ...patch, updatedAt: new Date().toISOString() }, 200);
  }
  return apiRequest<Incident>(`/incidents/${id}`, { method: "PATCH", body: patch });
}
