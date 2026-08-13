import type { UUID } from "./common";

export interface Organization {
  id: UUID;
  name: string;
  slug: string;
}

export interface Project {
  id: UUID;
  organizationId: UUID;
  name: string;
  slug: string;
}

export type UserRole = "owner" | "admin" | "member" | "viewer";

export interface OrgUser {
  id: UUID;
  name: string;
  email: string;
  role: UserRole;
  status: "active" | "invited" | "suspended";
  lastActiveAt?: string;
}

export type SsoProtocol = "oidc" | "saml";

export type SsoProviderKind = "entra_id" | "okta" | "auth0" | "google_workspace" | "generic_oidc";

export interface SsoConfig {
  provider: SsoProviderKind;
  protocol: SsoProtocol;
  issuerUrl: string;
  clientId: string;
  clientSecretReference: string;
  enabled: boolean;
}
