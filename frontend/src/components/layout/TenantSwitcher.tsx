import { ChevronsUpDown } from "lucide-react";
import { useTenant } from "@/context/TenantContext";
import { DropdownMenu } from "@/components/ui/DropdownMenu";

export function TenantSwitcher() {
  const { organization, project, projects, setProject } = useTenant();

  if (!organization) return null;

  return (
    <DropdownMenu
      align="left"
      trigger={
        <button
          type="button"
          className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-slate-100"
        >
          <div className="flex flex-col leading-tight">
            <span className="text-xs font-semibold text-ink">{organization.name}</span>
            <span className="flex items-center gap-1 text-xs text-ink-muted">
              {project?.name ?? "Select project"}
            </span>
          </div>
          <ChevronsUpDown className="h-3.5 w-3.5 text-ink-subtle" />
        </button>
      }
      items={projects.map((p) => ({
        label: p.name,
        onSelect: () => setProject(p),
      }))}
    />
  );
}
