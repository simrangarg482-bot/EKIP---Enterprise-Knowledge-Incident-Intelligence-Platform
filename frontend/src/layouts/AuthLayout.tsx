import { Outlet } from "react-router-dom";
import { Boxes } from "lucide-react";

export function AuthLayout() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <div className="flex h-10 w-10 items-center justify-center rounded-md bg-sidebar text-white">
            <Boxes className="h-5 w-5" />
          </div>
          <div>
            <p className="text-base font-semibold text-ink">EKIP</p>
            <p className="text-sm text-ink-muted">Enterprise Knowledge Incident Intelligence</p>
          </div>
        </div>
        <Outlet />
      </div>
    </div>
  );
}
