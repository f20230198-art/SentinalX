import { useEffect } from "react";

/**
 * Updates --mx / --my CSS vars on the `.cursor-halo` element so a radial
 * gradient can follow the cursor without re-rendering React.
 *
 * rAF-throttled — at 60Hz mousemove can fire ~120x/s and updating CSS vars
 * each time would be wasteful.
 */
export function useCursorHalo() {
  useEffect(() => {
    let rafId = 0;
    let nextX = window.innerWidth / 2;
    let nextY = window.innerHeight / 2;

    const onMove = (e: MouseEvent) => {
      nextX = e.clientX;
      nextY = e.clientY;
      if (!rafId) {
        rafId = requestAnimationFrame(() => {
          document.documentElement.style.setProperty("--mx", `${nextX}px`);
          document.documentElement.style.setProperty("--my", `${nextY}px`);
          rafId = 0;
        });
      }
    };
    window.addEventListener("mousemove", onMove);
    return () => {
      window.removeEventListener("mousemove", onMove);
      if (rafId) cancelAnimationFrame(rafId);
    };
  }, []);
}
