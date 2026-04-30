import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";

const LINES = [
  "> initializing sentinelx_console v0.7 ...",
  "> loading enrichment store ........... ok",
  "> binding mitre corpus ............... 697 techniques",
  "> contacting ollama runtime .......... mistral:latest",
  "> opening tor circuit ................ ok",
  "> hydrating dashboard ................ ready",
];

const KEY = "sentinelx.boot.played";

interface Props {
  onDone: () => void;
}

export function BootSequence({ onDone }: Props) {
  const [visible, setVisible] = useState(true);
  const [printed, setPrinted] = useState<string[]>([]);
  const [current, setCurrent] = useState("");

  useEffect(() => {
    if (sessionStorage.getItem(KEY) === "1") {
      setVisible(false);
      onDone();
      return;
    }
    let cancelled = false;
    let lineIdx = 0;
    let charIdx = 0;
    const tick = () => {
      if (cancelled) return;
      const line = LINES[lineIdx];
      if (!line) {
        sessionStorage.setItem(KEY, "1");
        setTimeout(() => {
          if (cancelled) return;
          setVisible(false);
          onDone();
        }, 600);
        return;
      }
      if (charIdx <= line.length) {
        setCurrent(line.slice(0, charIdx));
        charIdx++;
        setTimeout(tick, 16);
      } else {
        setPrinted((p) => [...p, line]);
        setCurrent("");
        lineIdx++;
        charIdx = 0;
        setTimeout(tick, 180);
      }
    };
    tick();
    return () => {
      cancelled = true;
    };
  }, [onDone]);

  const skip = () => {
    sessionStorage.setItem(KEY, "1");
    setVisible(false);
    onDone();
  };

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          key="boot"
          initial={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.5 }}
          className="fixed inset-0 z-50 flex flex-col justify-center px-12 hero-gradient"
        >
          <div className="font-mono text-sm md:text-base text-text">
            <div className="text-accent mb-6 tracking-[0.3em] text-xs">
              [ SENTINELX // BOOT ]
            </div>
            {printed.map((l, i) => (
              <div key={i} className="opacity-80">
                {l}
              </div>
            ))}
            {current && (
              <div>
                {current}
                <span className="inline-block w-2 h-4 ml-1 bg-accent animate-pulse align-middle" />
              </div>
            )}
          </div>
          <button
            onClick={skip}
            className="absolute top-6 right-6 font-mono text-xs tracking-[0.2em] text-text-muted hover:text-accent border border-border-soft px-3 py-1.5 transition-colors"
          >
            [ SKIP ]
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
