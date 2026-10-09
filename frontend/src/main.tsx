import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "@fontsource-variable/public-sans";
import "@fontsource-variable/jetbrains-mono";
import "./index.css";
// Side-effect import: initialises i18next before any component renders.
import "./i18n";

// Shared data-fetching cache: data counts as fresh for 30s, retry a failed request once
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

// Mount the app into <div id="root"> with the data cache and the router around it
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
