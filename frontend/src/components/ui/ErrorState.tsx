import { AlertTriangle } from "lucide-react";
import { Button } from "./Button";

interface ErrorStateProps {
  title?: string;
  description?: string;
  onRetry?: () => void;
}

export function ErrorState({
  title = "Something went wrong",
  description = "We couldn't load this data. Please try again.",
  onRetry,
}: ErrorStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      <div className="mb-1 flex h-10 w-10 items-center justify-center rounded-full bg-critical-subtle">
        <AlertTriangle className="h-5 w-5 text-critical" />
      </div>
      <p className="text-sm font-medium text-ink">{title}</p>
      <p className="max-w-sm text-xs text-ink-muted">{description}</p>
      {onRetry && (
        <Button size="sm" variant="secondary" className="mt-2" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}
