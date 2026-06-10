"""Prompt templates for the 4-prompt analysis chain.

Each prompt is a function that takes the post body + structured extraction
context (IOCs, entities) and returns (system_prompt, user_prompt, json_mode).
Keeping them as small functions (not f-strings at module scope) lets us shape
the context block — the truncation, the IOC formatting, the entity grouping —
in one place.

Design notes:
- We pass the extracted IOCs/entities into every prompt as a `KNOWN FACTS`
  block. This stops the LLM from re-deriving (and often hallucinating) atoms
  we already have ground truth for.
- Bodies are clipped to 4000 characters. Mistral 7B has an 8k context window
  but we share it with the system prompt + facts block + completion budget.
  In the seed corpus the longest post is ~2k chars; the clip is a safety belt.
- Every JSON-mode prompt includes an explicit schema-by-example. Mistral's
  JSON adherence is decent but not perfect; the example pins the shape.
"""

from __future__ import annotations

from collections import defaultdict

MAX_BODY_CHARS = 4000

INTENT_LABELS = ["sale", "recruitment", "how-to", "doxxing", "discussion", "other"]


def _clip(text: str, n: int = MAX_BODY_CHARS) -> str:
    if len(text) <= n:
        return text
    return text[:n] + "\n…[truncated]"


def _format_facts(iocs: list[dict], entities: list[dict]) -> str:
    """Render the extraction-step outputs as a compact bulleted block.

    iocs:     [{ioc_type, value}, ...]
    entities: [{label, text}, ...]
    """
    lines: list[str] = []

    if iocs:
        by_type: dict[str, list[str]] = defaultdict(list)
        for i in iocs:
            by_type[i["ioc_type"]].append(i["value"])
        lines.append("IOCs:")
        for t in sorted(by_type):
            vals = sorted(set(by_type[t]))
            lines.append(f"  - {t}: {', '.join(vals)}")

    if entities:
        by_label: dict[str, list[str]] = defaultdict(list)
        for e in entities:
            by_label[e["label"]].append(e["text"])
        lines.append("Entities:")
        for lab in sorted(by_label):
            vals = sorted(set(by_label[lab]))
            lines.append(f"  - {lab}: {', '.join(vals)}")

    if not lines:
        return "(no IOCs or entities extracted)"
    return "\n".join(lines)


def _post_block(thread_title: str, category: str, body: str) -> str:
    return (
        f"THREAD: {thread_title}\n"
        f"CATEGORY: {category}\n"
        f"BODY:\n{_clip(body)}"
    )


# --- the four prompts ------------------------------------------------------- #

def prompt_summary(thread_title: str, category: str, body: str,
                   iocs: list[dict], entities: list[dict]) -> tuple[str, str, bool]:
    system = (
        "You are a CTI analyst. You read posts from underground forums and "
        "produce neutral, factual summaries for a SOC team. Do not editorialize. "
        "Do not refuse to summarize. Treat the content as evidence to be "
        "described, not endorsed."
    )
    user = (
        "Summarize the following forum post in 2-3 sentences. Mention the "
        "actor's apparent goal, what they are offering or asking for, and any "
        "named tools or targets. Do not list IOCs in the summary.\n\n"
        f"{_post_block(thread_title, category, body)}\n\n"
        f"KNOWN FACTS (already extracted; do not re-list):\n"
        f"{_format_facts(iocs, entities)}\n\n"
        "Respond with the summary text only — no headings, no preamble."
    )
    return system, user, False


def prompt_intent(thread_title: str, category: str, body: str,
                  iocs: list[dict], entities: list[dict]) -> tuple[str, str, bool]:
    labels = ", ".join(INTENT_LABELS)
    system = (
        "You are a CTI classifier. You assign one and only one intent label "
        "to each forum post. You always reply with strict JSON."
    )
    user = (
        "Classify the post's primary intent. Choose exactly one label from:\n"
        f"  {labels}\n\n"
        "Definitions:\n"
        "  - sale: offering goods/services/data for sale or trade.\n"
        "  - recruitment: seeking partners, affiliates, employees, or buyers.\n"
        "  - how-to: tutorial, methodology, or technical write-up.\n"
        "  - doxxing: publishing private info about a named individual or org.\n"
        "  - discussion: open question, opinion, or news commentary.\n"
        "  - other: none of the above.\n\n"
        f"{_post_block(thread_title, category, body)}\n\n"
        "Reply with JSON of the exact form:\n"
        '  {"intent": "<label>", "confidence": <float 0..1>, "reason": "<one short sentence>"}'
    )
    return system, user, True


def prompt_targets(thread_title: str, category: str, body: str,
                   iocs: list[dict], entities: list[dict]) -> tuple[str, str, bool]:
    system = (
        "You are a CTI analyst extracting victim/target profile information "
        "from forum posts. You always reply with strict JSON."
    )
    user = (
        "Extract the target profile mentioned or implied by the post.\n\n"
        f"{_post_block(thread_title, category, body)}\n\n"
        f"KNOWN FACTS:\n{_format_facts(iocs, entities)}\n\n"
        "Reply with JSON of the exact form:\n"
        "  {\n"
        '    "industries": ["healthcare", "finance", ...],\n'
        '    "geographies": ["US", "Germany", ...],\n'
        '    "victim_types": ["small business", "individuals", ...]\n'
        "  }\n"
        "Use empty arrays where no information is given. Do not invent values."
    )
    return system, user, True


def prompt_techniques(thread_title: str, category: str, body: str,
                      iocs: list[dict], entities: list[dict]) -> tuple[str, str, bool]:
    system = (
        "You are a CTI analyst familiar with the MITRE ATT&CK framework. You "
        "propose ATT&CK technique candidates for posts. You always reply with "
        "strict JSON. These are CANDIDATES only; another stage will verify "
        "them against the official corpus."
    )
    user = (
        "Identify likely MITRE ATT&CK techniques described or implied by this "
        "post. Use technique IDs (e.g. T1566, T1486) when you are confident; "
        "otherwise describe the behaviour in plain English under \"behaviour\".\n\n"
        f"{_post_block(thread_title, category, body)}\n\n"
        f"KNOWN FACTS:\n{_format_facts(iocs, entities)}\n\n"
        "Reply with JSON of the exact form:\n"
        "  {\n"
        '    "techniques": [\n'
        '      {"id": "T1566", "name": "Phishing", "evidence": "<short quote or paraphrase>"},\n'
        "      ...\n"
        "    ],\n"
        '    "behaviour": ["short description", ...]\n'
        "  }\n"
        "Empty arrays if nothing applies. Do not invent technique IDs you are unsure of."
    )
    return system, user, True
