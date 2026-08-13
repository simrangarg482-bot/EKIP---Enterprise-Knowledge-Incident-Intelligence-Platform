import { Navigate, createBrowserRouter } from "react-router-dom";
import { AppLayout } from "@/layouts/AppLayout";
import { AuthLayout } from "@/layouts/AuthLayout";
import { ProtectedRoute } from "./ProtectedRoute";

import { LoginPage } from "@/pages/auth/LoginPage";
import { SignupPage } from "@/pages/auth/SignupPage";
import { AskPage } from "@/pages/ask/AskPage";
import { DashboardPage } from "@/pages/dashboard/DashboardPage";
import { IncidentsListPage } from "@/pages/incidents/IncidentsListPage";
import { IncidentDetailPage } from "@/pages/incidents/IncidentDetailPage";
import { KnowledgeListPage } from "@/pages/knowledge/KnowledgeListPage";
import { KnowledgeDetailPage } from "@/pages/knowledge/KnowledgeDetailPage";
import { SearchPage } from "@/pages/search/SearchPage";
import { ConnectorsPage } from "@/pages/connectors/ConnectorsPage";
import { AgentsPage } from "@/pages/agents/AgentsPage";
import { McpToolsPage } from "@/pages/mcp/McpToolsPage";
import { AnalyticsPage } from "@/pages/analytics/AnalyticsPage";
import { SettingsLayout } from "@/pages/settings/SettingsLayout";
import { OrganizationSettingsPage } from "@/pages/settings/OrganizationSettingsPage";
import { ProjectSettingsPage } from "@/pages/settings/ProjectSettingsPage";
import { UsersSettingsPage } from "@/pages/settings/UsersSettingsPage";
import { SsoSettingsPage } from "@/pages/settings/SsoSettingsPage";
import { ConnectorsSettingsPage } from "@/pages/settings/ConnectorsSettingsPage";
import { NotFoundPage } from "@/pages/misc/NotFoundPage";

export const router = createBrowserRouter([
  {
    element: <AuthLayout />,
    children: [
      { path: "/login", element: <LoginPage /> },
      { path: "/signup", element: <SignupPage /> },
    ],
  },
  {
    element: <ProtectedRoute />,
    children: [
      {
        element: <AppLayout />,
        children: [
          { path: "/", element: <Navigate to="/ask" replace /> },
          { path: "/ask", element: <AskPage /> },
          { path: "/dashboard", element: <DashboardPage /> },
          { path: "/incidents", element: <IncidentsListPage /> },
          { path: "/incidents/:id", element: <IncidentDetailPage /> },
          { path: "/knowledge", element: <KnowledgeListPage /> },
          { path: "/knowledge/:id", element: <KnowledgeDetailPage /> },
          { path: "/search", element: <SearchPage /> },
          { path: "/connectors", element: <ConnectorsPage /> },
          { path: "/agents", element: <AgentsPage /> },
          { path: "/mcp", element: <McpToolsPage /> },
          { path: "/analytics", element: <AnalyticsPage /> },
          {
            path: "/settings",
            element: <SettingsLayout />,
            children: [
              { index: true, element: <Navigate to="/settings/organization" replace /> },
              { path: "organization", element: <OrganizationSettingsPage /> },
              { path: "project", element: <ProjectSettingsPage /> },
              { path: "users", element: <UsersSettingsPage /> },
              { path: "sso", element: <SsoSettingsPage /> },
              { path: "connectors", element: <ConnectorsSettingsPage /> },
            ],
          },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);
