import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";

/**
 * Surveillance-camera awakening boot sequence.
 *
 * Five phases, total ~5.5s (skippable any time):
 *   0  static  — black with TV static noise + faint hum text
 *   1  iris    — circular clip-path opens like a camera lens iris
 *   2  map     — world map fades in, pinging dots flash at simulated .onion
 *                  hub locations, status lines type out underneath
 *   3  drip    — violet "system bleed" drips down from the top edge into
 *                  the dashboard (CSS-only blob shapes, varied speeds)
 *   4  cut     — quick glitch wipe + fade to the dashboard underneath
 *
 * Played once per session via sessionStorage. The hasStartedRef guard
 * prevents StrictMode double-mount from running the sequence twice.
 */

const KEY = "sentinelx.boot.played";
const PHASE_DURATIONS = [900, 800, 2400, 900, 500] as const;

interface PhaseConfig {
  static: number;
  iris: number;
  map: number;
  drip: number;
  cut: number;
}
const PHASES: (keyof PhaseConfig)[] = ["static", "iris", "map", "drip", "cut"];

interface PingNode {
  cx: number;
  cy: number;
  delay: number;
  label: string;
}

const PING_NODES: PingNode[] = [
  { cx: 240, cy: 180, delay: 0.0, label: "AMS / 51.219N" },
  { cx: 480, cy: 200, delay: 0.25, label: "FRA / 50.110N" },
  { cx: 700, cy: 240, delay: 0.55, label: "MOW / 55.755N" },
  { cx: 200, cy: 280, delay: 0.9, label: "NYC / 40.712N" },
  { cx: 820, cy: 320, delay: 1.2, label: "SHA / 31.230N" },
  { cx: 360, cy: 380, delay: 1.45, label: "DEL / 28.613N" },
  { cx: 600, cy: 420, delay: 1.7, label: "JNB / -26.20S" },
  { cx: 130, cy: 360, delay: 2.0, label: "GRU / -23.55S" },
];

interface Props {
  onDone: () => void;
}

// Module-scope refs survive React StrictMode's unmount/remount of <App/>
// in dev. We use them to enforce "the timeline only runs once per page
// load" without the cleanup function tearing the timeline down on the
// dev-only first unmount.
let bootStarted = false;
let bootDone = false;

export function BootSequence({ onDone }: Props) {
  const [phase, setPhase] = useState(0);
  const [visible, setVisible] = useState(!bootDone);
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  useEffect(() => {
    // Already finished in this page load? Notify the parent and stay hidden.
    if (bootDone) {
      onDoneRef.current();
      return;
    }
    // Already running from a previous (StrictMode) mount? Just attach the
    // current parent's onDone via the ref above; the in-flight timeline
    // will fire it when it completes. Do NOT restart the chain.
    if (bootStarted) return;
    bootStarted = true;

    if (sessionStorage.getItem(KEY) === "1") {
      bootDone = true;
      setVisible(false);
      onDoneRef.current();
      return;
    }

    const advance = (i: number) => {
      if (i >= PHASES.length) {
        sessionStorage.setItem(KEY, "1");
        bootDone = true;
        setVisible(false);
        onDoneRef.current();
        return;
      }
      setPhase(i);
      setTimeout(() => advance(i + 1), PHASE_DURATIONS[i]);
    };
    advance(0);
    // No cleanup — we deliberately let the timeline run to completion even
    // if the component briefly unmounts (StrictMode dev-only). It writes
    // sessionStorage + bootDone at the end so a real page reload behaves
    // correctly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const skip = () => {
    sessionStorage.setItem(KEY, "1");
    setVisible(false);
    onDone();
  };

  if (!visible) return null;

  return (
    <motion.div
      initial={{ opacity: 1 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-50 overflow-hidden bg-black"
    >
      <AnimatePresence mode="wait">
        {phase === 0 && <StaticPhase key="static" />}
        {(phase === 1 || phase === 2 || phase === 3) && (
          <IrisPhase key="iris" phase={phase} />
        )}
        {phase === 4 && <CutPhase key="cut" />}
      </AnimatePresence>

      {phase === 3 && <DripOverlay />}

      <button
        onClick={skip}
        className="absolute top-6 right-6 z-50 font-mono text-xs tracking-[0.2em] text-text-muted hover:text-accent border border-border-soft px-3 py-1.5 transition-colors backdrop-blur-sm bg-black/40"
      >
        [ SKIP ]
      </button>
    </motion.div>
  );
}

/* --- Phase 0: static --------------------------------------------------- */

function StaticPhase() {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.3 }}
      className="absolute inset-0 flex items-center justify-center"
    >
      <div className="absolute inset-0 noise-static" />
      <div className="relative font-mono text-xs tracking-[0.4em] text-text-muted z-10">
        <motion.span
          animate={{ opacity: [0.3, 1, 0.4, 1, 0.6] }}
          transition={{ duration: 0.9, times: [0, 0.2, 0.4, 0.7, 1] }}
        >
          [ NO SIGNAL ]
        </motion.span>
      </div>
    </motion.div>
  );
}

/* --- Phase 1-3: iris-open + world map ---------------------------------- */

function IrisPhase({ phase }: { phase: number }) {
  // phase 1: iris opens 0→100. phase 2/3: full open + map content.
  const isOpen = phase >= 2;
  return (
    <motion.div
      initial={{ opacity: 1 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="absolute inset-0"
    >
      <motion.div
        initial={{ clipPath: "circle(0% at 50% 50%)" }}
        animate={{
          clipPath: isOpen ? "circle(75% at 50% 50%)" : "circle(0% at 50% 50%)",
        }}
        transition={{ duration: 0.8, ease: [0.65, 0, 0.35, 1] }}
        className="absolute inset-0 hero-gradient"
      >
        <div className="absolute inset-0 grid-overlay" />
        {phase >= 2 && <WorldMap />}
      </motion.div>

      {/* Iris ring — a thin violet circle that briefly traces the iris edge. */}
      <motion.div
        initial={{ opacity: 0, scale: 0 }}
        animate={{
          opacity: isOpen ? [0, 0.9, 0] : [0, 0.9, 0.6],
          scale: isOpen ? 1.5 : 0.001,
        }}
        transition={{ duration: 0.8, ease: "easeOut" }}
        className="absolute top-1/2 left-1/2 w-[1500px] h-[1500px] -translate-x-1/2 -translate-y-1/2 rounded-full border border-accent/60 pointer-events-none"
        style={{ boxShadow: "0 0 80px rgba(167, 139, 250, 0.4)" }}
      />
    </motion.div>
  );
}

function WorldMap() {
  const [lines, setLines] = useState<string[]>([]);
  useEffect(() => {
    const seq = [
      "> camera_init   :: lens online",
      "> tor_relay     :: 6 hops established",
      "> ingest        :: 235 historical posts loaded",
      "> mitre_corpus  :: 697 techniques indexed",
      "> ollama        :: mistral:latest hot",
      "> dashboard     :: ready",
    ];
    let i = 0;
    const t = setInterval(() => {
      setLines((p) => [...p, seq[i]]);
      i++;
      if (i >= seq.length) clearInterval(t);
    }, 280);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-6 px-12">
      <div className="relative">
        <svg
          viewBox="0 0 1000 500"
          className="w-[min(900px,80vw)] h-auto opacity-70"
          xmlns="http://www.w3.org/2000/svg"
        >
          {/* Stylised world — a sparse dot grid, NOT a real map (avoids any */}
          {/* country misrepresentation, plus reads better on dark bg).      */}
          <DotMap />
          {/* Ping nodes */}
          {PING_NODES.map((n, i) => (
            <PingNode key={i} {...n} />
          ))}
        </svg>
        {/* Crosshair */}
        <div className="absolute inset-0 pointer-events-none flex items-center justify-center">
          <div className="w-[1px] h-full bg-accent/20 absolute" />
          <div className="h-[1px] w-full bg-accent/20 absolute" />
        </div>
      </div>

      <div className="font-mono text-[11px] text-text-muted space-y-1 min-h-[8rem]">
        {lines.map((l, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.2 }}
          >
            {l}
          </motion.div>
        ))}
      </div>
    </div>
  );
}

function DotMap() {
  // Generate a sparse dot grid that *suggests* continents without claiming
  // to be one. Deterministic, no extra package.
  const dots: { x: number; y: number; r: number }[] = [];
  for (let y = 60; y < 460; y += 14) {
    for (let x = 60; x < 940; x += 14) {
      // pseudo-random density mask to suggest land masses
      const seed = Math.sin(x * 12.9898 + y * 78.233) * 43758.5453;
      const v = seed - Math.floor(seed);
      // weighted toward middle latitudes
      const latW = 1 - Math.abs((y - 250) / 220);
      if (v < 0.25 * latW) {
        dots.push({ x, y, r: 1 + v * 1.8 });
      }
    }
  }
  return (
    <g>
      {dots.map((d, i) => (
        <circle
          key={i}
          cx={d.x}
          cy={d.y}
          r={d.r}
          fill="rgba(167, 139, 250, 0.45)"
        />
      ))}
    </g>
  );
}

function PingNode({ cx, cy, delay, label }: PingNode) {
  return (
    <g>
      <motion.circle
        cx={cx}
        cy={cy}
        r={3}
        fill="rgb(167, 139, 250)"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay, duration: 0.2 }}
      />
      {/* Expanding ring */}
      <motion.circle
        cx={cx}
        cy={cy}
        fill="none"
        stroke="rgba(167, 139, 250, 0.7)"
        strokeWidth={1}
        initial={{ r: 0, opacity: 0 }}
        animate={{ r: [0, 28], opacity: [0.9, 0] }}
        transition={{
          delay,
          duration: 1.4,
          repeat: 1,
          ease: "easeOut",
        }}
      />
      <motion.text
        x={cx + 8}
        y={cy + 4}
        fill="rgba(230, 227, 240, 0.7)"
        fontSize={9}
        fontFamily="JetBrains Mono, monospace"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: delay + 0.15, duration: 0.3 }}
      >
        {label}
      </motion.text>
    </g>
  );
}

/* --- Phase 3: drip overlay -------------------------------------------- */

function DripOverlay() {
  // 7 violet drips with varied widths, x-positions and speeds. Pure CSS.
  const drips = [
    { left: "8%", w: 24, dur: 1.0, len: "55vh", delay: 0.0 },
    { left: "21%", w: 14, dur: 0.85, len: "40vh", delay: 0.15 },
    { left: "33%", w: 32, dur: 1.05, len: "70vh", delay: 0.05 },
    { left: "47%", w: 18, dur: 0.95, len: "62vh", delay: 0.2 },
    { left: "62%", w: 26, dur: 1.1, len: "52vh", delay: 0.0 },
    { left: "78%", w: 20, dur: 0.9, len: "45vh", delay: 0.25 },
    { left: "91%", w: 30, dur: 1.0, len: "60vh", delay: 0.1 },
  ];
  return (
    <div className="absolute inset-x-0 top-0 z-30 pointer-events-none">
      {drips.map((d, i) => (
        <motion.div
          key={i}
          initial={{ height: 0 }}
          animate={{ height: d.len }}
          transition={{ duration: d.dur, delay: d.delay, ease: [0.4, 0, 0.6, 1] }}
          style={{
            position: "absolute",
            top: 0,
            left: d.left,
            width: d.w,
            background:
              "linear-gradient(to bottom, rgba(124, 58, 237, 0.95), rgba(167, 139, 250, 0.7) 60%, rgba(167, 139, 250, 0))",
            filter: "blur(0.4px)",
            borderBottomLeftRadius: "50%",
            borderBottomRightRadius: "50%",
            boxShadow: "0 0 18px rgba(167, 139, 250, 0.6)",
          }}
        />
      ))}
    </div>
  );
}

/* --- Phase 4: glitch cut ---------------------------------------------- */

function CutPhase() {
  return (
    <motion.div
      initial={{ opacity: 1 }}
      animate={{ opacity: [1, 1, 0.4, 1, 0] }}
      transition={{ duration: 0.5, times: [0, 0.2, 0.4, 0.6, 1] }}
      className="absolute inset-0"
    >
      <motion.div
        animate={{ x: [0, -8, 6, -3, 0] }}
        transition={{ duration: 0.4 }}
        className="absolute inset-0 hero-gradient"
      />
      <motion.div
        animate={{ opacity: [0, 0.8, 0] }}
        transition={{ duration: 0.4 }}
        className="absolute inset-0"
        style={{
          background:
            "repeating-linear-gradient(0deg, rgba(167,139,250,0.15) 0px, rgba(167,139,250,0.15) 2px, transparent 2px, transparent 6px)",
        }}
      />
    </motion.div>
  );
}
