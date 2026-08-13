import type { Connector } from "@/types/connector";
import { hoursAgo, minutesAgo } from "@/mocks/time";

const MOCK_ORG_ID = "org-1";

export const mockConnectors: Connector[] = [
  {
    id: "conn-github",
    organizationId: MOCK_ORG_ID,
    projectId: null,
    source: "github",
    config: { repos: [{ repo: "acme/api", ref: "main" }] },
    status: "active",
    lastSyncedAt: minutesAgo(12),
    createdAt: hoursAgo(48),
    updatedAt: minutesAgo(12),
  },
  {
    id: "conn-slack",
    organizationId: MOCK_ORG_ID,
    projectId: null,
    source: "slack",
    config: { channels: ["C0123456789"] },
    status: "active",
    lastSyncedAt: minutesAgo(4),
    createdAt: hoursAgo(48),
    updatedAt: minutesAgo(4),
  },
  {
    id: "conn-confluence",
    organizationId: MOCK_ORG_ID,
    projectId: null,
    source: "confluence",
    config: {},
    status: "connecting",
    lastSyncedAt: minutesAgo(1),
    createdAt: hoursAgo(2),
    updatedAt: minutesAgo(1),
  },
  {
    id: "conn-jira",
    organizationId: MOCK_ORG_ID,
    projectId: null,
    source: "jira",
    config: {},
    status: "active",
    lastSyncedAt: hoursAgo(1),
    createdAt: hoursAgo(72),
    updatedAt: hoursAgo(1),
  },
  {
    id: "conn-teams",
    organizationId: MOCK_ORG_ID,
    projectId: null,
    source: "teams",
    config: {},
    status: "disconnected",
    lastSyncedAt: null,
    createdAt: hoursAgo(96),
    updatedAt: hoursAgo(96),
  },
];
