import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Select } from "@/components/ui/Select";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { LoadingState } from "@/components/ui/LoadingState";
import { getSsoConfig, updateSsoConfig } from "@/api/tenancy";
import type { SsoConfig, SsoProviderKind, SsoProtocol } from "@/types/tenancy";
import { useToast } from "@/context/ToastContext";
import { useTenant } from "@/context/TenantContext";

// Matches app/core/tenancy/schemas.py's SSOProvider literal exactly -- the
// real backend rejects anything else with a 422.
const PROVIDERS: { value: SsoProviderKind; label: string }[] = [
  { value: "entra_id", label: "Microsoft Entra ID" },
  { value: "okta", label: "Okta" },
  { value: "auth0", label: "Auth0" },
  { value: "google_workspace", label: "Google Workspace" },
];

const PROTOCOLS: SsoProtocol[] = ["oidc", "saml"];

export function SsoSettingsPage() {
  const { toast } = useToast();
  const { organization } = useTenant();
  const organizationId = organization?.id ?? "";
  const ssoQuery = useQuery({
    queryKey: ["sso", organizationId],
    queryFn: () => getSsoConfig(organizationId),
    enabled: Boolean(organizationId),
  });
  const [draft, setDraft] = useState<SsoConfig | null>(null);

  const config = draft ?? ssoQuery.data ?? null;

  const saveMutation = useMutation({
    mutationFn: (next: SsoConfig) => updateSsoConfig(organizationId, next),
    onSuccess: () => toast({ variant: "success", title: "SSO configuration saved" }),
    onError: () => toast({ variant: "error", title: "Failed to save SSO configuration" }),
  });

  if (ssoQuery.isLoading) return <LoadingState label="Loading SSO configuration…" />;
  if (!config) return null;

  function update<K extends keyof SsoConfig>(key: K, value: SsoConfig[K]) {
    setDraft({ ...config, [key]: value } as SsoConfig);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Single sign-on</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="max-w-md">
          <label className="mb-1.5 block text-xs font-medium text-ink-muted">Provider</label>
          <Select value={config.provider} onChange={(e) => update("provider", e.target.value as SsoProviderKind)}>
            {PROVIDERS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </Select>
        </div>

        <div className="max-w-md">
          <label className="mb-1.5 block text-xs font-medium text-ink-muted">Protocol</label>
          <Select value={config.protocol} onChange={(e) => update("protocol", e.target.value as SsoProtocol)}>
            {PROTOCOLS.map((p) => (
              <option key={p} value={p}>
                {p.toUpperCase()}
              </option>
            ))}
          </Select>
        </div>

        <div className="max-w-md">
          <label className="mb-1.5 block text-xs font-medium text-ink-muted">Issuer URL</label>
          <Input value={config.issuerUrl} onChange={(e) => update("issuerUrl", e.target.value)} />
        </div>

        <div className="max-w-md">
          <label className="mb-1.5 block text-xs font-medium text-ink-muted">Client ID</label>
          <Input value={config.clientId} onChange={(e) => update("clientId", e.target.value)} />
        </div>

        <div className="max-w-md">
          <label className="mb-1.5 block text-xs font-medium text-ink-muted">Client secret reference</label>
          <Input
            value={config.clientSecretReference}
            onChange={(e) => update("clientSecretReference", e.target.value)}
            className="font-mono text-xs"
          />
          <p className="mt-1 text-xs text-ink-subtle">
            Reference to a secret manager entry. Actual secret values are never displayed or stored client-side.
          </p>
        </div>

        <div>
          <Button variant="primary" size="sm" isLoading={saveMutation.isPending} onClick={() => saveMutation.mutate(config)}>
            Save configuration
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
