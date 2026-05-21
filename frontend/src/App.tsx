import { useState } from "react";
import { Route, Routes } from "react-router-dom";
import { BootSequence } from "./components/BootSequence";
import { ShaderBackground } from "./components/ShaderBackground";
import { Header } from "./components/Shell";
import { Home } from "./pages/Home";
import { Timeline } from "./pages/Timeline";
import { Heatmap } from "./pages/Heatmap";
import { Investigations } from "./pages/Investigations";
import { IocPivot } from "./pages/IocPivot";
import { CaseGraph } from "./pages/CaseGraph";
import { Scout } from "./pages/Scout";

export default function App() {
  const [booted, setBooted] = useState(false);

  return (
    <div className="grain min-h-screen">
      <ShaderBackground />
      <BootSequence onDone={() => setBooted(true)} />
      <div
        style={{ visibility: booted ? "visible" : "hidden" }}
        className="relative z-10"
      >
        <Header />
        <main>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/posts" element={<Timeline />} />
            <Route path="/techniques" element={<Heatmap />} />
            <Route path="/investigations" element={<Investigations />} />
            <Route path="/scout" element={<Scout />} />
            <Route path="/iocs/:value" element={<IocPivot />} />
            <Route
              path="/investigations/:id/graph"
              element={<CaseGraph />}
            />
          </Routes>
        </main>
      </div>
    </div>
  );
}
