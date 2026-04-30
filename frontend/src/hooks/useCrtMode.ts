import { useEffect, useState } from "react";

const KEY = "sentinelx.crt";

/**
 * CRT/scanline mode toggle. State lives on `<body data-crt="on|off">` so
 * the index.css scanline rule can target it without React re-renders.
 * Persisted in localStorage so the choice survives a refresh.
 */
export function useCrtMode(): [boolean, () => void] {
  const [on, setOn] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(KEY) === "1";
  });

  useEffect(() => {
    document.body.setAttribute("data-crt", on ? "on" : "off");
    window.localStorage.setItem(KEY, on ? "1" : "0");
  }, [on]);

  return [on, () => setOn((v) => !v)];
}
