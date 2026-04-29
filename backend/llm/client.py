"""Thin Ollama HTTP client.

Ollama exposes a local REST API on http://127.0.0.1:11434. We hit two endpoints:

  POST /api/generate  - completion-style: prompt in, text out.
                        We request format=json + stream=False for the prompts
                        whose output we parse, and plain text (format omitted)
                        for the free-form summary.
  GET  /api/tags      - list installed models. Used as a startup health-check.

We deliberately do NOT use the `ollama` Python package. It adds a dependency
to do the same two POSTs we'd write by hand, and locks us to a particular
release cadence. httpx is already in the project (Stage 2).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
# Generation is the slowest step in the pipeline. Mistral on CPU regularly takes
# 20-60s per prompt for our post sizes; we budget 5 minutes per call to be
# safe, with a short connect timeout because the server is local.
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=10.0)


@dataclass(frozen=True)
class Generation:
    text: str
    raw: dict


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(
        self,
        model: str = "mistral",
        base_url: str = DEFAULT_BASE_URL,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OllamaClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def health(self) -> list[str]:
        """Return the list of installed model names. Raises if Ollama is down."""
        try:
            r = self._client.get("/api/tags")
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise OllamaError(f"ollama unreachable at {self.base_url}: {e}") from e
        return [m["name"] for m in r.json().get("models", [])]

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_mode: bool = False,
        temperature: float = 0.2,
        num_predict: int = 512,
    ) -> Generation:
        """Single non-streaming generation call.

        json_mode=True asks Ollama to constrain output to valid JSON. Mistral
        will still occasionally emit prose around the JSON; callers that need
        a parsed object should use chain._extract_json() rather than json.loads
        directly on .text.
        """
        body = _build_body(self.model, prompt, system, json_mode, temperature, num_predict)
        try:
            r = self._client.post("/api/generate", json=body)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise OllamaError(f"generate failed: {e}") from e

        try:
            payload = r.json()
        except json.JSONDecodeError as e:
            raise OllamaError(f"non-JSON response from ollama: {e}") from e

        text = payload.get("response", "")
        return Generation(text=text, raw=payload)


def _build_body(
    model: str,
    prompt: str,
    system: str | None,
    json_mode: bool,
    temperature: float,
    num_predict: int,
) -> dict:
    body: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    if system is not None:
        body["system"] = system
    if json_mode:
        body["format"] = "json"
    return body


class AsyncOllamaClient:
    """Async sibling of OllamaClient.

    Same surface, same defaults, backed by httpx.AsyncClient so multiple
    in-flight requests can share one TCP connection pool. Used by
    chain.analyse_post_async to fire the 4 prompts for a post in parallel.
    """

    def __init__(
        self,
        model: str = "mistral",
        base_url: str = DEFAULT_BASE_URL,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncOllamaClient":
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.aclose()

    async def health(self) -> list[str]:
        try:
            r = await self._client.get("/api/tags")
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise OllamaError(f"ollama unreachable at {self.base_url}: {e}") from e
        return [m["name"] for m in r.json().get("models", [])]

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_mode: bool = False,
        temperature: float = 0.2,
        num_predict: int = 512,
    ) -> Generation:
        body = _build_body(self.model, prompt, system, json_mode, temperature, num_predict)
        try:
            r = await self._client.post("/api/generate", json=body)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise OllamaError(f"generate failed: {e}") from e
        try:
            payload = r.json()
        except json.JSONDecodeError as e:
            raise OllamaError(f"non-JSON response from ollama: {e}") from e
        return Generation(text=payload.get("response", ""), raw=payload)
