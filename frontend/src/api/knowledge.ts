import { apiRequest, mockDelay } from "./client";
import { USE_MOCK_DATA } from "./config";
import type { GapReport, KnowledgeDocument, KnowledgeFilters } from "@/types/knowledge";
import type { Paginated } from "@/types/common";
import { mockGapReports, mockKnowledgeDocuments } from "@/mocks/data/knowledge";

export async function listKnowledgeDocuments(
  filters: KnowledgeFilters = {},
): Promise<Paginated<KnowledgeDocument>> {
  if (USE_MOCK_DATA) {
    let result = [...mockKnowledgeDocuments];
    if (filters.search) {
      const q = filters.search.toLowerCase();
      result = result.filter((d) => (d.title ?? "").toLowerCase().includes(q));
    }
    if (filters.source?.length) {
      result = result.filter((d) => filters.source!.includes(d.source));
    }
    result.sort((a, b) => (a.updatedAt > b.updatedAt ? -1 : 1));

    const page = filters.page ?? 1;
    const pageSize = filters.pageSize ?? 20;
    const start = (page - 1) * pageSize;
    return mockDelay({
      items: result.slice(start, start + pageSize),
      total: result.length,
      page,
      pageSize,
    });
  }

  // The real GET /knowledge returns every published document (no pagination
  // or search-text filter server-side; the backend does support `source`)
  // -- filtering and paging happen client-side over that full list, same as
  // the mock branch above.
  const params = new URLSearchParams();
  if (filters.source?.length === 1) params.set("source", filters.source[0]);

  let documents = await apiRequest<KnowledgeDocument[]>(`/knowledge?${params.toString()}`);
  if (filters.search) {
    const q = filters.search.toLowerCase();
    documents = documents.filter((d) => (d.title ?? "").toLowerCase().includes(q));
  }
  if (filters.source && filters.source.length > 1) {
    documents = documents.filter((d) => filters.source!.includes(d.source));
  }
  documents = [...documents].sort((a, b) => (a.updatedAt > b.updatedAt ? -1 : 1));

  const page = filters.page ?? 1;
  const pageSize = filters.pageSize ?? 20;
  const start = (page - 1) * pageSize;
  return {
    items: documents.slice(start, start + pageSize),
    total: documents.length,
    page,
    pageSize,
  };
}

export async function getKnowledgeDocument(id: string): Promise<KnowledgeDocument> {
  if (USE_MOCK_DATA) {
    const doc = mockKnowledgeDocuments.find((d) => d.id === id);
    if (!doc) throw { status: 404, message: "Document not found" };
    return mockDelay(doc);
  }
  return apiRequest<KnowledgeDocument>(`/knowledge/${id}`);
}

export async function listProposedDocuments(): Promise<KnowledgeDocument[]> {
  if (USE_MOCK_DATA) {
    return mockDelay(mockKnowledgeDocuments.filter((d) => d.status === "proposed"));
  }
  return apiRequest<KnowledgeDocument[]>(`/knowledge/proposed`);
}

export async function listGapReports(): Promise<GapReport[]> {
  if (USE_MOCK_DATA) {
    return mockDelay(mockGapReports);
  }
  return apiRequest<GapReport[]>(`/knowledge/gaps`);
}
