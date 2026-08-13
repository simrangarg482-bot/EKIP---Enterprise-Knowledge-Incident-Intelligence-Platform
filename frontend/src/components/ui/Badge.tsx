import type { HTMLAttributes } from "react";
import { cn } from "@/utils/cn";

export type BadgeTone = "neutral" | "accent" | "success" | "warning" | "critical" | "info";

const TONE_CLASSES: Record<BadgeTone, string> = {
  neutral: "bg-slate-100 text-ink-muted border-border",
  accent: "bg-accent-subtle text-accent border-accent-border",
  success: "bg-success-subtle text-success border-success-border",
  warning: "bg-warning-subtle text-warning border-warning-border",
  critical: "bg-critical-subtle text-critical border-critical-border",
  info: "bg-info-subtle text-info border-info-border",
};

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
}

export function Badge({ className, tone = "neutral", ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium leading-4",
        TONE_CLASSES[tone],
        className,
      )}
      {...props}
    />
  );
}
