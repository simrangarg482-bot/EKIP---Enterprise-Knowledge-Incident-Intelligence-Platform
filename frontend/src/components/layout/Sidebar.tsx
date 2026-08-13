import { NavLink } from "react-router-dom";
import { Boxes } from "lucide-react";
import { cn } from "@/utils/cn";
import { PRIMARY_NAV, SETTINGS_NAV } from "@/routes/nav";

interface SidebarProps {
  collapsed?: boolean;
}

export function Sidebar({ collapsed }: SidebarProps) {
  return (
    <aside
      className={cn(
        "flex h-full flex-col bg-sidebar text-sidebar-text transition-[width] duration-150",
        collapsed ? "w-[60px]" : "w-[220px]",
      )}
    >
      <div className="flex h-14 items-center gap-2 border-b border-sidebar-border px-4">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-accent text-white">
          <Boxes className="h-4 w-4" />
        </div>
        {!collapsed && <span className="text-sm font-semibold text-white">EKIP</span>}
      </div>

      <nav className="flex-1 overflow-y-auto px-2 py-3 scrollbar-thin">
        <ul className="flex flex-col gap-0.5">
          {PRIMARY_NAV.map((item) => (
            <SidebarLink key={item.path} item={item} collapsed={collapsed} />
          ))}
        </ul>

        <div className="my-3 border-t border-sidebar-border" />

        <ul className="flex flex-col gap-0.5">
          {SETTINGS_NAV.map((item) => (
            <SidebarLink key={item.path} item={item} collapsed={collapsed} />
          ))}
        </ul>
      </nav>
    </aside>
  );
}

function SidebarLink({
  item,
  collapsed,
}: {
  item: (typeof PRIMARY_NAV)[number];
  collapsed?: boolean;
}) {
  const Icon = item.icon;
  return (
    <li>
      <NavLink
        to={item.path}
        title={collapsed ? item.label : undefined}
        className={({ isActive }) =>
          cn(
            "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium transition-colors",
            isActive ? "bg-sidebar-hover text-white" : "text-sidebar-text hover:bg-sidebar-hover hover:text-white",
          )
        }
      >
        <Icon className="h-4 w-4 shrink-0" />
        {!collapsed && <span className="truncate">{item.label}</span>}
      </NavLink>
    </li>
  );
}
