import { Link } from "react-router-dom";
import { Button } from "@/components/ui/Button";

export function NotFoundPage() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
      <p className="text-3xl font-semibold text-ink">404</p>
      <p className="text-sm text-ink-muted">This page doesn't exist or has been moved.</p>
      <Link to="/dashboard">
        <Button variant="secondary" size="sm">
          Back to dashboard
        </Button>
      </Link>
    </div>
  );
}
