import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";
import { AuthProvider } from "@/context/AuthContext";
import { TenantProvider } from "@/context/TenantContext";
import { ToastProvider } from "@/context/ToastContext";
import { router } from "@/routes";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    },
  },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <AuthProvider>
          <TenantProvider>
            <RouterProvider router={router} />
          </TenantProvider>
        </AuthProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}
