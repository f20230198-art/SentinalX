import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import { Header } from "./components/Shell";
import { Home } from "./pages/Home";
import { Backdrop } from "./components/Backdrop";

// Home renders immediately; every other route is code-split and fetched on
// first visit. The graph pages pull in d3-force, so they stay out of the
// initial bundle. While a chunk loads, a thin bar shows under the header —
// nothing is shown when the chunk is already cached.
const Timeline = lazy(() => import("./pages/Timeline").then((m) => ({ default: m.Timeline })));
const Heatmap = lazy(() => import("./pages/Heatmap").then((m) => ({ default: m.Heatmap })));
const Investigations = lazy(() =>
  import("./pages/Investigations").then((m) => ({ default: m.Investigations })),
);
const Discover = lazy(() => import("./pages/Discover").then((m) => ({ default: m.Discover })));
const Alerts = lazy(() => import("./pages/Alerts").then((m) => ({ default: m.Alerts })));
const Scout = lazy(() => import("./pages/Scout").then((m) => ({ default: m.Scout })));
const IocPivot = lazy(() => import("./pages/IocPivot").then((m) => ({ default: m.IocPivot })));
const CaseGraph = lazy(() => import("./pages/CaseGraph").then((m) => ({ default: m.CaseGraph })));

export default function App() {
  return (
    <div className="min-h-screen">
      <Backdrop />
      <Header />
      <main>
        <Suspense fallback={<div className="route-loading" role="progressbar" aria-label="Loading page" />}>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/posts" element={<Timeline />} />
            <Route path="/techniques" element={<Heatmap />} />
            <Route path="/investigations" element={<Investigations />} />
            <Route path="/discover" element={<Discover />} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/scout" element={<Scout />} />
            <Route path="/iocs/:value" element={<IocPivot />} />
            <Route path="/investigations/:id/graph" element={<CaseGraph />} />
          </Routes>
        </Suspense>
      </main>
    </div>
  );
}
