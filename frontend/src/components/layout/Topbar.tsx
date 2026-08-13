import { useNavigate } from "react-router-dom";
import { Menu, User as UserIcon } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { SearchBar } from "@/components/data/SearchBar";
import { DropdownMenu } from "@/components/ui/DropdownMenu";
import { TenantSwitcher } from "./TenantSwitcher";
import { useState } from "react";

interface TopbarProps {
  onToggleSidebar: () => void;
}

export function Topbar({ onToggleSidebar }: TopbarProps) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");

  function handleSubmitSearch(event: React.FormEvent) {
    event.preventDefault();
    if (query.trim()) {
      navigate(`/search?q=${encodeURIComponent(query.trim())}`);
    }
  }

  return (
    <header className="flex h-14 items-center gap-3 border-b border-border bg-surface px-4">
      <button
        type="button"
        onClick={onToggleSidebar}
        aria-label="Toggle sidebar"
        className="rounded-md p-1.5 text-ink-muted hover:bg-slate-100 hover:text-ink lg:hidden"
      >
        <Menu className="h-4 w-4" />
      </button>

      <div className="hidden lg:block">
        <TenantSwitcher />
      </div>

      <form onSubmit={handleSubmitSearch} className="mx-auto max-w-md flex-1">
        <SearchBar value={query} onChange={setQuery} placeholder="Search EKIP…" />
      </form>

      <DropdownMenu
        trigger={
          <button
            type="button"
            className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-slate-100"
          >
            <div className="flex h-7 w-7 items-center justify-center rounded-full bg-accent-subtle text-accent">
              <UserIcon className="h-3.5 w-3.5" />
            </div>
            <span className="hidden text-sm font-medium text-ink sm:inline">{user?.name}</span>
          </button>
        }
        items={[
          { label: "Settings", onSelect: () => navigate("/settings") },
          { label: "Sign out", onSelect: () => void logout(), destructive: true },
        ]}
      />
    </header>
  );
}
