import { useEffect, useRef, useState } from "react";

const CHARS = "!<>-_\\/[]{}—=+*^?#________";

/**
 * Renders `target` by sweeping a "decryption" animation across its chars.
 * Every char gets a few random glyphs before settling. Total duration scales
 * with target length, capped at ~900ms.
 */
export function useScramble(target: string, deps: unknown[] = []): string {
  const [out, setOut] = useState(target);
  const frameRef = useRef(0);

  useEffect(() => {
    const length = target.length;
    if (length === 0) {
      setOut("");
      return;
    }
    const queue: { from: string; to: string; start: number; end: number; ch?: string }[] = [];
    const oldText = out;
    for (let i = 0; i < length; i++) {
      const from = oldText[i] || "";
      const to = target[i];
      const start = Math.floor(Math.random() * 20);
      const end = start + Math.floor(Math.random() * 30) + 8;
      queue.push({ from, to, start, end });
    }

    let frame = 0;
    let raf = 0;
    const tick = () => {
      let output = "";
      let complete = 0;
      for (const item of queue) {
        if (frame >= item.end) {
          complete++;
          output += item.to;
        } else if (frame >= item.start) {
          if (!item.ch || Math.random() < 0.28) {
            item.ch = CHARS[Math.floor(Math.random() * CHARS.length)];
          }
          output += item.ch;
        } else {
          output += item.from;
        }
      }
      setOut(output);
      if (complete < queue.length) {
        frame++;
        raf = requestAnimationFrame(tick);
      }
    };
    raf = requestAnimationFrame(tick);
    frameRef.current = raf;
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, ...deps]);

  return out;
}
