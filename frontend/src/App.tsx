import { useState } from "react";
import { Route, Routes } from "react-router-dom";
import { BootSequence } from "./components/BootSequence";
import { Header } from "./components/Shell";
import { Home } from "./pages/Home";
import { Placeholder } from "./pages/Placeholder";
import { useCursorHalo } from "./hooks/useCursorHalo";

export default function App() {
  const [booted, setBooted] = useState(false);
  useCursorHalo();

  return (
    <div className="grain cursor-halo min-h-screen">
      <BootSequence onDone={() => setBooted(true)} />
      <div
        style={{ visibility: booted ? "visible" : "hidden" }}
        className="relative z-10"
      >
        <Header />
        <main>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route
              path="/posts"
              element={<Placeholder index="02" title="Intelligence feed" />}
            />
            <Route
              path="/techniques"
              element={<Placeholder index="03" title="Mitre browser" />}
            />
            <Route
              path="/investigations"
              element={<Placeholder index="04" title="Investigations" />}
            />
          </Routes>
        </main>
      </div>
    </div>
  );
}
