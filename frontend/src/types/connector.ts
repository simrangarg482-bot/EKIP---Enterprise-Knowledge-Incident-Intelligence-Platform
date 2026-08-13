import type { ISODateString, UUID } from "./common";

/** Mirrors `app.core.tenancy.schemas.ConnectorSource`. */
export type ConnectorSource =
  | "slack"
  | "teams"
  | "github"
  | "azure_devops"
  | "jira"
  | "confluence"
  | "sharepoint"
  | "runbooks"
  | "monitoring";

/** Mirrors `app.core.tenancy.schemas.ConnectorStatus`. */
export type ConnectorStatus = "connecting" | "active" | "error" | "disconnected";

/** Mirrors `app.core.tenancy.schemas.ConnectorConfig` -- the real, persisted
 * connector row. `credential_ref` is deliberately omitted here: it is the
 * server-side envelope-encrypted credential reference, never a raw secret,
 * but there is still no reason for the frontend to hold or render it.
 */
export interface Connector {
  id: UUID;
  organizationId: UUID;
  projectId: UUID | null;
  source: ConnectorSource;
  config: Record<string, unknown>;
  status: ConnectorStatus;
  lastSyncedAt: ISODateString | null;
  createdAt: ISODateString;
  updatedAt: ISODateString;
}

export interface GithubRepoConfig {
  repo: string;
  ref?: string;
}

export interface CreateGithubConnectorInput {
  token: string;
  repos: GithubRepoConfig[];
}

export interface CreateSlackConnectorInput {
  token: string;
  channelIds: string[];
}
