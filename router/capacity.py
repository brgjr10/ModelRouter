"""
router/capacity.py — Model capacity metadata fetcher.

Fetches context_window, rpm, tpm, tpd for each ModelEntry from
provider-specific APIs or static tables. Never raises — failures set
fields to None and log at DEBUG level.

Requirements: 10.2, 10.3, 10.4, 10.5, 10.6, 10.12
"""

import logging
from typing import Optional

import httpx

from router.models import ModelEntry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Groq static rate-limit table
# Free-tier limits as of 2024. Keys are model IDs as returned by Groq API.
# ---------------------------------------------------------------------------
GROQ_STATIC_TABLE: dict = {
    "llama3-8b-8192":              {"rpm": 30,  "tpm": 14_400,  "tpd": 500_000,  "context_window": 8_192},
    "llama3-70b-8192":             {"rpm": 30,  "tpm":  6_000,  "tpd": 500_000,  "context_window": 8_192},
    "llama-3.1-8b-instant":        {"rpm": 30,  "tpm": 20_000,  "tpd": 500_000,  "context_window": 131_072},
    "llama-3.1-70b-versatile":     {"rpm": 30,  "tpm": 12_000,  "tpd": 100_000,  "context_window": 131_072},
    "llama-3.3-70b-versatile":     {"rpm": 30,  "tpm": 12_000,  "tpd": 100_000,  "context_window": 131_072},
    "llama-3.3-70b-specdec":       {"rpm": 30,  "tpm": 12_000,  "tpd": 100_000,  "context_window": 131_072},
    "llama-3.2-1b-preview":        {"rpm": 30,  "tpm":  7_000,  "tpd": 500_000,  "context_window": 131_072},
    "llama-3.2-3b-preview":        {"rpm": 30,  "tpm": 12_000,  "tpd": 500_000,  "context_window": 131_072},
    "llama-3.2-11b-vision-preview":{"rpm": 30,  "tpm":  7_000,  "tpd": 500_000,  "context_window": 131_072},
    "llama-3.2-90b-vision-preview":{"rpm": 15,  "tpm":  7_000,  "tpd": 250_000,  "context_window": 131_072},
    "mixtral-8x7b-32768":          {"rpm": 30,  "tpm":  5_000,  "tpd": 500_000,  "context_window": 32_768},
    "gemma-7b-it":                 {"rpm": 30,  "tpm": 14_400,  "tpd": 500_000,  "context_window": 8_192},
    "gemma2-9b-it":                {"rpm": 30,  "tpm": 14_400,  "tpd": 500_000,  "context_window": 8_192},
    "whisper-large-v3":            {"rpm": 20,  "tpm": None,    "tpd": 2_000,    "context_window": None},
    "whisper-large-v3-turbo":      {"rpm": 20,  "tpm": None,    "tpd": 2_000,    "context_window": None},
}

_NULL = {"context_window": None, "rpm": None, "tpm": None, "tpd": None}


async def fetch_capacity(entry: ModelEntry) -> None:
    """Populate context_window, rpm, tpm, tpd on *entry* in-place.

    Dispatches to provider-specific logic. All errors are caught; affected
    fields are left as None on any failure.

    Requirements: 10.2–10.6, 10.12
    """
    try:
        if entry.provider == "openrouter":
            await _fetch_openrouter(entry)
        elif entry.provider == "groq":
            await _fetch_groq(entry)
        elif entry.provider == "ollama":
            await _fetch_ollama(entry)
        elif entry.provider in ("lmstudio", "llamacpp", "openai"):
            await _fetch_generic_local(entry)
        # google, deepseek, fireworks, anthropic — leave as None
    except Exception as exc:
        logger.debug("Capacity fetch failed for %s/%s: %s", entry.provider, entry.model_id, exc)


# ---------------------------------------------------------------------------
# Provider-specific fetch helpers
# ---------------------------------------------------------------------------

async def _fetch_openrouter(entry: ModelEntry) -> None:
    """GET https://openrouter.ai/api/v1/models, find the model, populate fields."""
    headers = {"Content-Type": "application/json"}
    if entry.api_key:
        headers["Authorization"] = f"Bearer {entry.api_key}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get("https://openrouter.ai/api/v1/models", headers=headers)
        r.raise_for_status()
        data = r.json()
    models = data.get("data", [])
    for m in models:
        if m.get("id") == entry.model_id:
            entry.context_window = m.get("context_length") or None
            limits = m.get("per_request_limits") or {}
            # OpenRouter doesn't expose rpm/tpm/tpd in the standard format;
            # use prompt_tokens as a rough tpm proxy if available.
            pt = limits.get("prompt_tokens")
            entry.tpm = int(pt) if pt else None
            entry.rpm = None
            entry.tpd = None
            return
    logger.debug("Model %s not found in OpenRouter model list", entry.model_id)


async def _fetch_groq(entry: ModelEntry) -> None:
    """Use static table for rpm/tpm/tpd; fetch context_window from API if key available."""
    mid = entry.model_id.lower()
    # Look up static table (try exact then partial match)
    static = GROQ_STATIC_TABLE.get(entry.model_id) or GROQ_STATIC_TABLE.get(mid)
    if static:
        entry.rpm = static.get("rpm")
        entry.tpm = static.get("tpm")
        entry.tpd = static.get("tpd")
        entry.context_window = static.get("context_window")
        return  # static table is sufficient

    # Fallback: query Groq models API for context_window
    if entry.api_key:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {entry.api_key}"},
                )
                r.raise_for_status()
                for m in r.json().get("data", []):
                    if m.get("id") == entry.model_id:
                        entry.context_window = m.get("context_window") or None
                        return
        except Exception as exc:
            logger.debug("Groq API fetch failed for %s: %s", entry.model_id, exc)


async def _fetch_ollama(entry: ModelEntry) -> None:
    """POST /api/show to get num_ctx → context_window. Rate fields are None for local."""
    entry.rpm = None
    entry.tpm = None
    entry.tpd = None
    # Derive the base URL (drop /api/chat suffix if present)
    base = entry.chat_url
    for suffix in ["/api/chat", "/v1/chat/completions", "/chat/completions"]:
        if base.endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
            break
    show_url = base.rstrip("/") + "/api/show"
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.post(show_url, json={"name": entry.model_id})
        r.raise_for_status()
        data = r.json()
    # num_ctx lives at model_info -> llama.context_length or parameters section
    params = data.get("parameters", "")
    for line in (params or "").splitlines():
        if "num_ctx" in line:
            parts = line.split()
            if len(parts) >= 2:
                try:
                    entry.context_window = int(parts[-1])
                    return
                except ValueError:
                    pass
    # Also check model_info dict
    model_info = data.get("model_info", {})
    for key in model_info:
        if "context_length" in key or "ctx_length" in key:
            try:
                entry.context_window = int(model_info[key])
                return
            except (ValueError, TypeError):
                pass
    logger.debug("num_ctx not found in Ollama /api/show response for %s", entry.model_id)


async def _fetch_generic_local(entry: ModelEntry) -> None:
    """Try to read context_length from a /v1/models or metadata endpoint."""
    entry.rpm = None
    entry.tpm = None
    entry.tpd = None
    base = entry.chat_url
    for suffix in ["/v1/chat/completions", "/chat/completions"]:
        if base.endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
            break
    models_url = base.rstrip("/") + "/v1/models"
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(models_url)
            r.raise_for_status()
            for m in r.json().get("data", []):
                if m.get("id") == entry.model_id:
                    entry.context_window = (
                        m.get("context_length")
                        or m.get("max_context_length")
                        or m.get("context_window")
                        or None
                    )
                    return
    except Exception as exc:
        logger.debug("Generic local capacity fetch failed for %s: %s", entry.model_id, exc)
