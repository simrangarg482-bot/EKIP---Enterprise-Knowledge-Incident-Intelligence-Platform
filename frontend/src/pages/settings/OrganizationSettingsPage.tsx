import type { ReactNode } from "react";
import { useTenant } from "@/context/TenantContext";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";

export function OrganizationSettingsPage() {
  const { organization } = useTenant();

  return (
    <Card>
      <CardHeader>
        <CardTitle>Organization</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <Field label="Organization name">
          <Input defaultValue={organization?.name} />
        </Field>
        <Field label="Slug">
          <Input defaultValue={organization?.slug} disabled />
        </Field>
        <Field label="Organization ID">
          <Input defaultValue={organization?.id} disabled className="font-mono text-xs" />
        </Field>
        <div>
          <Button variant="primary" size="sm">
            Save changes
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="max-w-md">
      <label className="mb-1.5 block text-xs font-medium text-ink-muted">{label}</label>
      {children}
    </div>
  );
}
