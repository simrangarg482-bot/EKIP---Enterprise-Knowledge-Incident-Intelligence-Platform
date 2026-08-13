import type { LucideIcon } from "lucide-react";
import {
  MessageCircleQuestion,
  LayoutDashboard,
  AlertCircle,
  BookOpen,
  Search,
  Plug,
  Bot,
  Wrench,
  BarChart3,
  Settings,
} from "lucide-react";

export interface NavItem {
  label: string;
  path: string;
  icon: LucideIcon;
}

export const PRIMARY_NAV: NavItem[] = [
  { label: "Ask EKIP", path: "/ask", icon: MessageCircleQuestion },
  { label: "Dashboard", path: "/dashboard", icon: LayoutDashboard },
  { label: "Incidents", path: "/incidents", icon: AlertCircle },
  { label: "Knowledge", path: "/knowledge", icon: BookOpen },
  { label: "Search", path: "/search", icon: Search },
  { label: "Connectors", path: "/connectors", icon: Plug },
  { label: "Agents", path: "/agents", icon: Bot },
  { label: "MCP Tools", path: "/mcp", icon: Wrench },
  { label: "Analytics", path: "/analytics", icon: BarChart3 },
];

export const SETTINGS_NAV: NavItem[] = [{ label: "Settings", path: "/settings", icon: Settings }];
