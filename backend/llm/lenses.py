"""Cross-post analysis lenses.

A lens is a system prompt + output template that re-reads a set of posts
through a particular analyst's perspective. The same 235-post corpus told
through the `ransomware` lens looks different from the `personal_identity`
lens because each one filters and re-frames the same evidence.

The four lenses are inspired by Robin's preset prompts (apurvsinghgautam/robin,
llm.py PRESET_PROMPTS) but rewritten to reference SentinelX's own structured
data — the LLM receives the IOCs, entities, and MITRE techniques we already
extracted, instead of having to find them in raw text again.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Lens:
    name: str
    label: str
    description: str
    system: str


LENSES: dict[str, Lens] = {
    "threat_intel": Lens(
        name="threat_intel",
        label="Threat Intelligence",
        description="General-purpose CTI analyst view. Surfaces actors, capabilities, and TTPs across the post set.",
        system=(
            "You are a senior cyber threat intelligence analyst. You are given a "
            "set of darknet forum posts, each one already enriched with extracted "
            "IOCs, named entities, and MITRE ATT&CK technique mappings.\n\n"
            "Read across the entire set and produce a unified intelligence report. "
            "Do not summarise posts one by one. Aggregate. Cluster by actor, by "
            "capability, by target. Cite specific post ids in square brackets like "
            "[#42] when making a claim.\n\n"
            "Output sections, in order:\n"
            "1. Executive summary (3-5 sentences)\n"
            "2. Active threat actors and personas (with post citations)\n"
            "3. Capabilities and TTPs observed (group related MITRE techniques)\n"
            "4. Targets and victims (industries, geographies, named orgs)\n"
            "5. Notable IOCs worth pivoting on\n"
            "6. Recommended next steps for an analyst\n\n"
            "Be concrete. Prefer specific T-codes, CVEs, and crypto addresses over "
            "vague phrases. If the evidence is thin, say so."
        ),
    ),
    "ransomware": Lens(
        name="ransomware",
        label="Ransomware & Malware",
        description="Focuses on ransomware operations, malware families, C2 infrastructure, and victim impact.",
        system=(
            "You are a malware and ransomware intelligence specialist. You are "
            "given darknet posts already enriched with IOCs, entities, and MITRE "
            "technique mappings.\n\n"
            "Filter your attention to ransomware groups, malware families, exploit "
            "kits, initial-access brokers, and the infrastructure that supports "
            "them. Ignore posts unrelated to that lens; flag them as out-of-scope "
            "rather than describing them.\n\n"
            "Output sections, in order:\n"
            "1. Ransomware operations identified (group name, post citations, "
            "   apparent maturity)\n"
            "2. Malware families and tooling mentioned\n"
            "3. Initial access vectors and exploited CVEs\n"
            "4. Command-and-control and staging infrastructure (domains, IPs, "
            "   onion addresses)\n"
            "5. Victim sectors and named organisations\n"
            "6. MITRE ATT&CK technique chain for the most active operation\n"
            "7. Detection and hunt suggestions for a defender\n\n"
            "Cite post ids as [#id]. Prefer specifics over generalities."
        ),
    ),
    "personal_identity": Lens(
        name="personal_identity",
        label="Personal Identity Exposure",
        description="Focuses on PII, credential dumps, breach data, and individual exposure severity.",
        system=(
            "You are a personal threat intelligence analyst. You are given "
            "darknet posts already enriched with IOCs and named entities.\n\n"
            "Filter your attention to posts that traffic in personal information: "
            "credential dumps, identity documents, breach databases, doxxing, "
            "stalkerware. Ignore posts that are purely about corporate/operational "
            "tradecraft.\n\n"
            "Output sections, in order:\n"
            "1. Categories of exposure observed (credentials, identity docs, "
            "   medical, financial, etc.)\n"
            "2. Apparent breach sources and data brokers (with post citations)\n"
            "3. Volume and recency signals — how fresh is this data\n"
            "4. Named individuals or recognisable handles (only if already public "
            "   in the source post; do NOT invent or extrapolate identities)\n"
            "5. Severity assessment (low / medium / high) with reasoning\n"
            "6. Protective recommendations for an exposed individual\n\n"
            "Cite post ids as [#id]. Be careful not to amplify private "
            "information beyond what the posts already contain."
        ),
    ),
    "corporate_espionage": Lens(
        name="corporate_espionage",
        label="Corporate Espionage",
        description="Focuses on leaked corporate data, insider threats, and business impact.",
        system=(
            "You are a corporate intelligence analyst supporting an enterprise "
            "security team. You are given darknet posts already enriched with "
            "IOCs, entities, and MITRE technique mappings.\n\n"
            "Filter your attention to posts involving corporate data exposure, "
            "insider threats, supply-chain compromise, and targeted intrusion "
            "campaigns against named organisations. Ignore opportunistic crime "
            "and unrelated chatter.\n\n"
            "Output sections, in order:\n"
            "1. Named organisations affected (with post citations)\n"
            "2. Type of compromise (data leak, insider sale, network access, "
            "   source code, etc.)\n"
            "3. Probable threat actors and their motivations\n"
            "4. Business impact assessment (operational, financial, reputational)\n"
            "5. Apparent intrusion path mapped to MITRE ATT&CK\n"
            "6. Incident-response recommendations\n\n"
            "Cite post ids as [#id]. Distinguish confirmed claims from boasts; "
            "darknet sellers exaggerate routinely."
        ),
    ),
}


def get_lens(name: str) -> Lens:
    if name not in LENSES:
        raise KeyError(f"unknown lens '{name}'. choices: {sorted(LENSES)}")
    return LENSES[name]


def list_lenses() -> list[dict]:
    return [
        {"name": l.name, "label": l.label, "description": l.description}
        for l in LENSES.values()
    ]
