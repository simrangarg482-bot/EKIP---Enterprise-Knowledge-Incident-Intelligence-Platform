import { useTenant } from "@/context/TenantContext";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";

export function ProjectSettingsPage() {
  const { project, projects, organization } = useTenant();

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Active project</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="max-w-md">
            <label className="mb-1.5 block text-xs font-medium text-ink-muted">Project name</label>
            <Input defaultValue={project?.name} />
          </div>
          <div className="max-w-md">
            <label className="mb-1.5 block text-xs font-medium text-ink-muted">Slug</label>
            <Input defaultValue={project?.slug} disabled />
          </div>
          <div>
            <Button variant="primary" size="sm">
              Save changes
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>All projects in {organization?.name}</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="flex flex-col divide-y divide-border">
            {projects.map((p) => (
              <li key={p.id} className="flex items-center justify-between py-2.5 text-sm">
                <span className="text-ink">{p.name}</span>
                <span className="text-xs text-ink-subtle">{p.slug}</span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
