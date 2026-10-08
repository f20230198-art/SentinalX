import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

/* ----------------------------------------------------------------------- *
 * Global keyboard shortcuts.
 *   g then o/p/t/i/s/d   go to a page
 *   ?                    show this sheet
 * Page-level keys (j/k/Enter, "/", Esc) are handled where they apply.
 * Ignored while typing in a field.
 * ----------------------------------------------------------------------- */

const GO: Record<string, [string, string]> = {
  o: ["/", "Overview"],
  p: ["/posts", "Posts"],
  t: ["/techniques", "Techniques"],
  i: ["/investigations", "Investigations"],
  d: ["/discover", "Discover"],
  a: ["/alerts", "Alerts"],
  s: ["/scout", "Scout"],
};

const PAGE_KEYS: [string, string][] = [
  ["/", "Search posts (Posts page)"],
  ["j / k", "Next / previous post"],
  ["Enter", "Open the highlighted post"],
  ["Esc", "Close a panel or release a pinned graph cluster"],
];

export function Shortcuts() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const pendingG = useRef<number | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "?") {
        setOpen((v) => !v);
        return;
      }
      if (e.key === "Escape") setOpen(false);
      if (pendingG.current !== null) {
        window.clearTimeout(pendingG.current);
        pendingG.current = null;
        const dest = GO[e.key];
        if (dest) {
          e.preventDefault();
          navigate(dest[0]);
          setOpen(false);
        }
        return;
      }
      if (e.key === "g") pendingG.current = window.setTimeout(() => (pendingG.current = null), 900);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="hidden border border-border-soft px-2 py-1 font-mono text-xs text-text-muted hover:border-text hover:text-text lg:block"
        title="Keyboard shortcuts"
        aria-label="Keyboard shortcuts"
      >
        ?
      </button>
      {open && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-text/30 p-4" onClick={() => setOpen(false)}>
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Keyboard shortcuts"
            className="w-full max-w-md border-2 border-rule bg-surface-1 p-6 shadow-[0_16px_48px_rgba(0,0,0,0.5)]"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="m-0 mb-4 text-lg font-bold">Keyboard shortcuts</h2>
            <dl className="m-0 text-sm">
              {Object.entries(GO).map(([k, [, label]]) => (
                <div key={k} className="flex justify-between border-t border-border-soft py-1.5">
                  <dt>Go to {label}</dt>
                  <dd className="m-0 font-mono">g {k}</dd>
                </div>
              ))}
              {PAGE_KEYS.map(([k, label]) => (
                <div key={k} className="flex justify-between border-t border-border-soft py-1.5">
                  <dt>{label}</dt>
                  <dd className="m-0 font-mono">{k}</dd>
                </div>
              ))}
            </dl>
            <button onClick={() => setOpen(false)} className="mt-4 border border-text px-3 py-1 text-sm font-semibold hover:bg-text hover:text-surface-1">
              Close
            </button>
          </div>
        </div>
      )}
    </>
  );
}
