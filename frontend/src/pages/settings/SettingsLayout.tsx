import { NavLink, Outlet } from "react-router-dom";
import { PageHeader } from "@/components/layout/PageHeader";
import { cn } from "@/utils/cn";

const SETTINGS_NAV = [
  { label: "Organization", path: "/settings/organization" },
  { label: "Project", path: "/settings/project" },
  { label: "Users", path: "/settings/users" },
  { label: "SSO", path: "/settings/sso" },
  { label: "Connectors", path: "/settings/connectors" },
];

export function SettingsLayout() {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Settings" description="Manage your organization, project, users, and integrations." />

      <div className="flex flex-col gap-6 lg:flex-row">
        <nav className="flex shrink-0 flex-row gap-1 overflow-x-auto lg:w-48 lg:flex-col lg:overflow-visible">
          {SETTINGS_NAV.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) =>
                cn(
                  "whitespace-nowrap rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  isActive ? "bg-slate-100 text-ink" : "text-ink-muted hover:bg-slate-50 hover:text-ink",
                )
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="min-w-0 flex-1">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
