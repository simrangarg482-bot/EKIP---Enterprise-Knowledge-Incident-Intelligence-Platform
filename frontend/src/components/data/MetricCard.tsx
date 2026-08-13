import type { LucideIcon } from "lucide-react";
import { cn } from "@/utils/cn";

interface MetricCardProps {
  label: string;
  value: string | number;
  icon: LucideIcon;
  trend?: { direction: "up" | "down"; label: string; positive?: boolean };
  tone?: "neutral" | "critical" | "success";
}

const TONE_ICON_CLASSES: Record<NonNullable<MetricCardProps["tone"]>, string> = {
  neutral: "bg-slate-100 text-ink-muted",
  critical: "bg-critical-subtle text-critical",
  success: "bg-success-subtle text-success",
};

export function MetricCard({ label, value, icon: Icon, trend, tone = "neutral" }: MetricCardProps) {
  return (
    <div className="rounded-lg border border-border bg-surface px-4 py-3.5 shadow-subtle">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-ink-muted">{label}</p>
        <div className={cn("flex h-6 w-6 items-center justify-center rounded-md", TONE_ICON_CLASSES[tone])}>
          <Icon className="h-3.5 w-3.5" />
        </div>
      </div>
      <div className="mt-2 flex items-baseline gap-2">
        <p className="text-2xl font-semibold tracking-tight text-ink">{value}</p>
        {trend && (
          <span
            className={cn(
              "text-xs font-medium",
              trend.positive === false ? "text-critical" : trend.positive ? "text-success" : "text-ink-muted",
            )}
          >
            {trend.direction === "up" ? "↑" : "↓"} {trend.label}
          </span>
        )}
      </div>
    </div>
  );
}
