"""Four-stage prompt chain orchestrator.

For each post:
  1. summary    (free text)
  2. intent     (JSON: {intent, confidence, reason})
  3. targets    (JSON: {industries, geographies, victim_types})
  4. techniques (JSON: {techniques, behaviour})

Returns an Analysis dataclass; persistence is the caller's job.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from backend.llm import schemas
from backend.llm.client import AsyncOllamaClient, OllamaClient
from backend.llm.prompts import (
    prompt_intent,
    prompt_summary,
    prompt_targets,
    prompt_techniques,
)

log = logging.getLogger("sentinelx.llm.chain")


# Mistral with format=json sometimes wraps JSON in ```json fences or adds a
# trailing apology. This pulls the first {...} block from the response.
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    if not text:
        return None
    # Fast path: the whole response is JSON.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Slow path: find the first balanced {...} we can parse.
    m = _JSON_BLOCK_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


@dataclass
class Analysis:
    summary: str | None = None
    intent: dict | None = None
    targets: dict | None = None
    techniques: dict | None = None
    raw_responses: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        # Summary is the cheapest signal that the model is responding at all.
        # Intent is the only one we strictly need for downstream filtering.
        return self.summary is not None and self.intent is not None


def analyse_post(
    client: OllamaClient,
    thread_title: str,
    category: str,
    body: str,
    iocs: list[dict],
    entities: list[dict],
) -> Analysis:
    out = Analysis()

    # 1. summary
    sys_p, usr_p, _ = prompt_summary(thread_title, category, body, iocs, entities)
    try:
        g = client.generate(usr_p, system=sys_p, json_mode=False, num_predict=400)
        out.summary = g.text.strip() or None
        out.raw_responses["summary"] = g.raw
    except Exception as e:
        out.errors.append(f"summary: {e!r}")

    # 2. intent
    sys_p, usr_p, _ = prompt_intent(thread_title, category, body, iocs, entities)
    try:
        g = client.generate(usr_p, system=sys_p, json_mode=True, num_predict=200)
        out.raw_responses["intent"] = g.raw
        _apply_json(out, "intent", g.text)
    except Exception as e:
        out.errors.append(f"intent: {e!r}")

    # 3. targets
    sys_p, usr_p, _ = prompt_targets(thread_title, category, body, iocs, entities)
    try:
        g = client.generate(usr_p, system=sys_p, json_mode=True, num_predict=300)
        out.raw_responses["targets"] = g.raw
        _apply_json(out, "targets", g.text)
    except Exception as e:
        out.errors.append(f"targets: {e!r}")

    # 4. techniques
    sys_p, usr_p, _ = prompt_techniques(thread_title, category, body, iocs, entities)
    try:
        g = client.generate(usr_p, system=sys_p, json_mode=True, num_predict=500)
        out.raw_responses["techniques"] = g.raw
        _apply_json(out, "techniques", g.text)
    except Exception as e:
        out.errors.append(f"techniques: {e!r}")

    return out


# --- async variant ---------------------------------------------------------- #
#
# All four prompts are independent — none of them needs the output of another.
# So we can fire them concurrently against the same loaded model. Ollama
# happily interleaves requests sharing one model in VRAM (it queues kernel
# launches on the GPU). Real wall-clock benefit comes from overlapping the
# prompt-eval phase of one call with the token-generation of another.
#
# The schema for each prompt — system text, user text, json_mode, num_predict —
# is the same as the sync path. We just await all four together.

_PROMPT_SPECS = (
    ("summary",    prompt_summary,    False, 400),
    ("intent",     prompt_intent,     True,  200),
    ("targets",    prompt_targets,    True,  300),
    ("techniques", prompt_techniques, True,  500),
)


async def _run_one(
    client: AsyncOllamaClient,
    name: str,
    builder,
    json_mode: bool,
    num_predict: int,
    thread_title: str,
    category: str,
    body: str,
    iocs: list[dict],
    entities: list[dict],
):
    sys_p, usr_p, _ = builder(thread_title, category, body, iocs, entities)
    try:
        g = await client.generate(
            usr_p, system=sys_p, json_mode=json_mode, num_predict=num_predict,
        )
        return name, g, None
    except Exception as e:
        return name, None, repr(e)


async def analyse_post_async(
    client: AsyncOllamaClient,
    thread_title: str,
    category: str,
    body: str,
    iocs: list[dict],
    entities: list[dict],
) -> Analysis:
    out = Analysis()

    tasks = [
        _run_one(client, name, builder, json_mode, num_predict,
                 thread_title, category, body, iocs, entities)
        for (name, builder, json_mode, num_predict) in _PROMPT_SPECS
    ]
    results = await asyncio.gather(*tasks)

    for name, gen, err in results:
        if err is not None:
            out.errors.append(f"{name}: {err}")
            continue
        out.raw_responses[name] = gen.raw
        if name == "summary":
            out.summary = gen.text.strip() or None
        else:
            _apply_json(out, name, gen.text)

    return out


def _apply_json(out: Analysis, name: str, text: str) -> None:
    """Parse + schema-validate one JSON-mode response onto `out`.

    Parsing alone isn't enough: valid JSON can still have the wrong keys,
    an intent outside our label set, or a shape a prompt-injected post asked
    for. Anything that fails validation is stored as None with an error, so
    downstream stages never see unvalidated model output.
    """
    clean, err = schemas.validate(name, _extract_json(text))
    setattr(out, name, clean)
    if err:
        out.errors.append(f"{name}: {err}: {text[:200]!r}")
