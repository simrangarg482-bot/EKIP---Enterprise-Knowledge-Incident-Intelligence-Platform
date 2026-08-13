import { Loader2 } from "lucide-react";

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      <Loader2 className="h-5 w-5 animate-spin text-ink-subtle" />
      <p className="text-xs text-ink-muted">{label}</p>
    </div>
  );
}
