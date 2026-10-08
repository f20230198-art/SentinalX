/**
 * Data colours for SVG/canvas drawing, where Tailwind classes can't reach.
 * Values mirror the @theme tokens in index.css — keep the two in sync.
 *
 * Rule of the system: colour is never the only carrier of meaning. Provenance
 * also differs by fill pattern (solid / hatched / outline), IOC types also
 * by label, intents also by text.
 */

export const INK = "#e6ede7";
export const MUTED = "#9db2a6";
export const RULE = "#2a4136";
export const PAPER = "#11211b";
export const SIGNAL = "#ff6a5c";
export const INFO = "#79b0ff";
export const WARN = "#e9b04f";
export const OK = "#5fd39a";

/** IOC families: network (blue), crypto/payment (amber), vuln (red),
 *  hashes (green), identity/web (ink). */
export const IOC_COLOR: Record<string, string> = {
  ipv4: INFO,
  ipv6: INFO,
  domain: INK,
  url: INK,
  email: MUTED,
  btc: WARN,
  cve: SIGNAL,
  md5: OK,
  sha1: OK,
  sha256: OK,
};
export const iocColor = (t: string) => IOC_COLOR[t] ?? INK;

/** Intent is a label first; colour only separates the two that need eyes. */
export const INTENT_COLOR: Record<string, string> = {
  sale: SIGNAL,
  doxxing: SIGNAL,
  recruitment: INFO,
};
export const intentColor = (intent: string | null | undefined) =>
  (intent && INTENT_COLOR[intent]) || MUTED;

export type Provenance = "llm_verified" | "semantic" | "llm_unverified";

export const PROVENANCE: Record<
  Provenance,
  { label: string; short: string; description: string; color: string }
> = {
  llm_verified: {
    label: "LLM · verified",
    short: "Verified",
    description: "Mistral proposed this T-code and it exists in the official MITRE ATT&CK corpus.",
    color: INK,
  },
  semantic: {
    label: "Semantic match",
    short: "Semantic",
    description: "Found by embedding similarity (MiniLM cosine ≥ 0.45) against technique descriptions; no LLM.",
    color: INFO,
  },
  llm_unverified: {
    label: "LLM · unverified",
    short: "Unverified",
    description: "Mistral proposed it but the T-code is not in the corpus: treat as a hallucination.",
    color: WARN,
  },
};
