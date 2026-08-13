import { useState } from "react";
import { Outlet } from "react-router-dom";
import { Sidebar } from "@/components/layout/Sidebar";
import { Topbar } from "@/components/layout/Topbar";
import { useMediaQuery } from "@/hooks/useMediaQuery";

export function AppLayout() {
  const isTablet = useMediaQuery("(max-width: 1024px)");
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background">
      <Sidebar collapsed={collapsed || isTablet} />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Topbar onToggleSidebar={() => setCollapsed((c) => !c)} />
        <main className="flex-1 overflow-y-auto px-6 py-6 scrollbar-thin">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
