import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { CheckCircle2, AlertTriangle, XCircle, Info, X } from "lucide-react";
import { cn } from "@/utils/cn";

export type ToastVariant = "success" | "error" | "warning" | "info";

interface Toast {
  id: number;
  title: string;
  description?: string;
  variant: ToastVariant;
}

interface ToastContextValue {
  toast: (toast: Omit<Toast, "id">) => void;
}

const ToastContext = createContext<ToastContextValue | undefined>(undefined);

const VARIANT_STYLES: Record<ToastVariant, { icon: typeof Info; classes: string }> = {
  success: { icon: CheckCircle2, classes: "border-success-border bg-success-subtle text-success" },
  error: { icon: XCircle, classes: "border-critical-border bg-critical-subtle text-critical" },
  warning: { icon: AlertTriangle, classes: "border-warning-border bg-warning-subtle text-warning" },
  info: { icon: Info, classes: "border-accent-border bg-accent-subtle text-accent" },
};

let idCounter = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback(
    (input: Omit<Toast, "id">) => {
      const id = ++idCounter;
      setToasts((prev) => [...prev, { ...input, id }]);
      setTimeout(() => dismiss(id), 5000);
    },
    [dismiss],
  );

  const value = useMemo(() => ({ toast }), [toast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2">
        {toasts.map((t) => {
          const { icon: Icon, classes } = VARIANT_STYLES[t.variant];
          return (
            <div
              key={t.id}
              role="status"
              className={cn(
                "flex items-start gap-2.5 rounded-md border px-3.5 py-3 shadow-panel",
                classes,
              )}
            >
              <Icon className="mt-0.5 h-4 w-4 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-ink">{t.title}</p>
                {t.description && <p className="mt-0.5 text-xs text-ink-muted">{t.description}</p>}
              </div>
              <button
                type="button"
                onClick={() => dismiss(t.id)}
                aria-label="Dismiss notification"
                className="text-ink-subtle hover:text-ink-muted"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within a ToastProvider");
  return ctx;
}
