import { apiRequest, mockDelay } from "./client";
import { USE_MOCK_DATA } from "./config";
import type { Organization, OrgUser, Project, SsoConfig } from "@/types/tenancy";
import { mockOrganizations, mockProjects, mockSsoConfig, mockUsers } from "@/mocks/data/tenancy";

export async function listOrganizations(): Promise<Organization[]> {
  if (USE_MOCK_DATA) {
    return mockDelay(mockOrganizations);
  }
  // Real backend: GET /organizations (no /tenancy prefix on this router --
  // and it always returns just the caller's own organization as a single-
  // element list, never every organization in the system; see
  // app/api/routers/tenancy.py's admin_router docstring).
  return apiRequest<Organization[]>(`/organizations`);
}

export async function listProjects(organizationId: string): Promise<Project[]> {
  if (USE_MOCK_DATA) {
    return mockDelay(mockProjects.filter((p) => p.organizationId === organizationId));
  }
  return apiRequest<Project[]>(`/organizations/${organizationId}/projects`);
}

export async function listOrgUsers(): Promise<OrgUser[]> {
  if (USE_MOCK_DATA) {
    return mockDelay(mockUsers);
  }
  // No real "list users in my organization" endpoint exists yet on the
  // backend (only /users/{id}/logout-all). Left pointed at the closest
  // plausible path so this fails loudly (404) instead of silently, until
  // that endpoint exists.
  return apiRequest<OrgUser[]>(`/users`);
}

export async function getSsoConfig(_organizationId: string): Promise<SsoConfig> {
  if (USE_MOCK_DATA) {
    return mockDelay(mockSsoConfig);
  }
  // No read-back endpoint exists yet on the backend -- only
  // POST /organizations/{id}/sso/configure (write-only). Left pointed at
  // the closest plausible path so this fails loudly (404) instead of
  // silently, until a GET equivalent exists.
  return apiRequest<SsoConfig>(`/organizations/${_organizationId}/sso`);
}

export async function updateSsoConfig(organizationId: string, config: SsoConfig): Promise<SsoConfig> {
  if (USE_MOCK_DATA) {
    return mockDelay(config, 300);
  }
  return apiRequest<SsoConfig>(`/organizations/${organizationId}/sso/configure`, {
    method: "POST",
    body: config,
  });
}
