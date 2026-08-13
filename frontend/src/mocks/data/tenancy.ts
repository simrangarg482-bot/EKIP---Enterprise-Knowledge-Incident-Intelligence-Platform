import type { Organization, OrgUser, Project, SsoConfig } from "@/types/tenancy";

export const mockOrganizations: Organization[] = [
  { id: "org-1", name: "Acme Corp", slug: "acme-corp" },
  { id: "org-2", name: "Northwind Traders", slug: "northwind" },
];

export const mockProjects: Project[] = [
  { id: "proj-1", organizationId: "org-1", name: "Engineering Platform", slug: "engineering-platform" },
  { id: "proj-2", organizationId: "org-1", name: "Payments", slug: "payments" },
  { id: "proj-3", organizationId: "org-2", name: "Core Services", slug: "core-services" },
];

export const mockUsers: OrgUser[] = [
  { id: "user-1", name: "Simran Kaur", email: "simran.kaur@acme.corp", role: "admin", status: "active", lastActiveAt: "2026-08-11T08:12:00Z" },
  { id: "user-2", name: "Rahul Mehta", email: "rahul.mehta@acme.corp", role: "member", status: "active", lastActiveAt: "2026-08-11T07:40:00Z" },
  { id: "user-3", name: "Priya Nair", email: "priya.nair@acme.corp", role: "member", status: "active", lastActiveAt: "2026-08-10T21:05:00Z" },
  { id: "user-4", name: "Daniel Osei", email: "daniel.osei@acme.corp", role: "viewer", status: "invited" },
  { id: "user-5", name: "Bhawna Relhan", email: "bhawna.relhan@navikenz.com", role: "owner", status: "active", lastActiveAt: "2026-08-11T09:02:00Z" },
];

export const mockSsoConfig: SsoConfig = {
  provider: "entra_id",
  protocol: "oidc",
  issuerUrl: "https://login.microsoftonline.com/acme-corp/v2.0",
  clientId: "8f14e45f-ceea-4b3f-8d7c-000000000000",
  clientSecretReference: "vault://ekip/sso/entra-id/client-secret",
  enabled: true,
};
