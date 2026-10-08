# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary: a SOC / cyber-threat-intelligence analyst at a desk, triaging darknet
forum chatter for early warnings (access sales, credential dumps, exploit
trades). Works in long sessions, scans many posts quickly, and must justify
every escalation with evidence. Secondary evaluator: a technical reviewer
(e.g. a CTO in a demo) judging the tool as if they were that analyst.

## Product Purpose

Turn unstructured darknet forum posts into explainable, MITRE ATT&CK-mapped,
exportable threat intelligence. Success = an analyst can see what matters,
why the system believes it, and act (pivot, investigate, export) in seconds.

## Positioning

An evidence engine, not just a darknet monitor: every claim traces back to its
source — a regex match, a spaCy span, a specific LLM prompt, or a cosine score.
ATT&CK techniques carry provenance (LLM-verified against the official corpus,
semantic match, or unverified), lens summaries cite posts as `[#id]`, and
mitigations are pure MITRE lookups. Runs locally with no paid APIs.

## Operating Context

Pipeline: Tor scraper (.onion forums) → language detect/translate → IOC regex +
spaCy NER → local Mistral-7B (summary, intent, targets, T-codes) → MITRE
verification + MiniLM semantic matching → FastAPI (REST, SSE, PDF) → React UI.
Views: live post timeline, ATT&CK heatmap, investigations with lenses and
citations, IOC pivot, case-file graph, Scout (scrape any .onion on demand), PDF
export. The LLM (Ollama) and Tor can be offline; the UI then serves stored,
already-enriched data ("cached mode") and must say so calmly, not as failure.

## Capabilities and Constraints

- Real data: 441 posts, 697 ATT&CK techniques, 580 post→technique mappings, 44
  mitigations (demo DB). Synthetic forums; real Tor plumbing.
- Measured accuracy exists (backend/eval/EVAL_REPORT.md): IOC F1 0.94, intent
  0.78, technique mapping hybrid F1 0.32 — do not overclaim in UI copy.
- UI i18n: en / ru / es for interface chrome.
- Terminology: IOC, TTP, technique (T-code), tactic, provenance sources
  `llm_verified` / `semantic` / `llm_unverified`, lens, investigation.

## Brand Commitments

- Name: SentinelX.
- Visual direction pinned by the owner (Oct 2026): Swiss / analyst-grade —
  replaces the earlier violet "hacker console" look.

## Evidence on Hand

Demo DB, evaluation report, MITRE ATT&CK data (© MITRE, terms of use).
