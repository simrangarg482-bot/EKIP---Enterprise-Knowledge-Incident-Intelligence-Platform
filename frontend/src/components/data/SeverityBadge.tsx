import type { IncidentSeverity } from "@/types/incident";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { titleCase } from "@/utils/format";

const SEVERITY_TONE: Record<IncidentSeverity, BadgeTone> = {
  critical: "critical",
  high: "warning",
  medium: "neutral",
  low: "accent",
};

export function SeverityBadge({ severity }: { severity: IncidentSeverity }) {
  return <Badge tone={SEVERITY_TONE[severity]}>{titleCase(severity)}</Badge>;
}
