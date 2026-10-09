"""Small HTTP client for the local Ollama LLM server (/api/generate, /api/tags)."""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

# Where Ollama runs on this machine
DEFAULT_BASE_URL = "http://127.0.0.1:11434"
# LLM calls are slow on CPU, so allow up to 5 min per call
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=10.0)


# One LLM reply: the generated text + the full raw JSON from Ollama
@dataclass(frozen=True)
class Generation:
    text: str
    raw: dict


# Raised when Ollama is down or replies with something broken
class OllamaError(RuntimeError):
    pass


# Talks to Ollama one request at a time (use with `with OllamaClient() as c:`)
class OllamaClient:
    def __init__(
        self,
        model: str = "mistral",
        base_url: str = DEFAULT_BASE_URL,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        # Reusable HTTP connection to Ollama
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    # Lets you use `with OllamaClient() as c:` so the connection closes automatically
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
        """One LLM call; json_mode asks for JSON (parse with chain._extract_json)."""
        # Build the request, send it, and fail clearly if Ollama errors out
        body = _build_body(self.model, prompt, system, json_mode, temperature, num_predict)
        try:
            r = self._client.post("/api/generate", json=body)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise OllamaError(f"generate failed: {e}") from e

        # Ollama replies with JSON; the generated text is in "response"
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
    # stream=False = wait for the full answer instead of word by word
    body: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    # Optional system prompt (the "role" instructions)
    if system is not None:
        body["system"] = system
    # Ask Ollama to only output valid JSON
    if json_mode:
        body["format"] = "json"
    return body


class AsyncOllamaClient:
    """Async version of OllamaClient, used to run a post's 4 prompts in parallel."""

    def __init__(
        self,
        model: str = "mistral",
        base_url: str = DEFAULT_BASE_URL,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        # Async HTTP connection (many requests can be in flight at once)
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
        # Same steps as the normal client, just awaited
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
