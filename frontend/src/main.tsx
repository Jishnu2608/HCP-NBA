import { MutationCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./auth";
import "./index.css";
import { AppErrorBoundary } from "./pages/ErrorPage";
import { ThemeProvider } from "./theme";
import { ToastProvider } from "./toast";

const queryClient: QueryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 10_000, refetchOnWindowFocus: true, retry: false } },
  // Any change the user makes can move a chart (charts.tsx): refresh every insight query
  // after each successful action, so figures never lag the records they are drawn from.
  mutationCache: new MutationCache({
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["insights"] }),
  }),
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <ToastProvider>
              {/* Last line of defence: an unexpected crash outside a page shows the
                  product's error screen, never a blank page or a stack trace. */}
              <AppErrorBoundary standalone>
                <App />
              </AppErrorBoundary>
            </ToastProvider>
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
);
