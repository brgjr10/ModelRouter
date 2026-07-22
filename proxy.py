"""
proxy.py — ModelRouter main entry point (port 5000).

Refactored from buffered proxy to full SSE streaming router with:
  - providers.json config (atomic writes, CRUD API)
  - In-memory Registry with asyncio.Lock and background refresh
  - SSE streaming passthrough (always stream: true)
  - Self-loop prevention (DNS + literal checks)
  - Local model auto-discovery (Ollama :11434, LM Studio :1234, llama.cpp :8080)
  - Capacity metadata fetching per provider
  - Rate limit tracking + scoring penalties
  - Selection log circular buffer
  - React SPA served from static/
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import socket
import sqlite3
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# Load .env from the project root before anything reads os.environ
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file, override=False)
    except ImportError:
        # dotenv not installed — parse manually
        for _line in _env_file.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip("\"'"))

from router.capacity import fetch_capacity
from router.config import (
    PROVIDERS_PATH,
    WEIGHTS_PATH,
    _atomic_write_providers,
    _ensure_providers_file,
    _load_providers,
    _load_weights,
    _save_weights,
)
from router.models import ModelEntry, Provider, SelectionRecord
from router.selfloop import _is_self_loop, _own_addresses

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_CAPACITY_CACHE: set = set()


def _get_odysseus_db_path() -> str:
    """Read at call time so changes to ODYSSEUS_DB_PATH after startup take effect."""
    return os.environ.get("ODYSSEUS_DB_PATH", "").strip()


# Keep a module-level alias for the static path at start
ODYSSEUS_DB_PATH: str = os.environ.get("ODYSSEUS_DB_PATH", "")
STATIC_DIR = Path(__file__).parent / "static"

# ---------------------------------------------------------------------------
# RateLimitTracker — rolling 60-second window per model
# ---------------------------------------------------------------------------

class RateLimitTracker:
    """Tracks request counts per model in a rolling 60-second window."""

    def __init__(self) -> None:
        self._counts: Dict[str, deque] = {}

    def record(self, model_id: str) -> None:
        if model_id not in self._counts:
            self._counts[model_id] = deque()
        self._counts[model_id].append(time.monotonic())

    def recent_count(self, model_id: str) -> int:
        if model_id not in self._counts:
            return 0
        buf = self._counts[model_id]
        cutoff = time.monotonic() - 60.0
        while buf and buf[0] < cutoff:
            buf.popleft()
        return len(buf)


# ---------------------------------------------------------------------------
# FailureTracker — temporarily deprioritize models that error out
# ---------------------------------------------------------------------------

class FailureTracker:
    """Tracks recent failures per model to avoid retrying broken endpoints."""

    def __init__(self) -> None:
        self._fails: Dict[str, deque] = {}

    def mark_failed(self, model_id: str) -> None:
        if model_id not in self._fails:
            self._fails[model_id] = deque()
        self._fails[model_id].append(time.monotonic())

    def recent_failures(self, model_id: str, window: float = 120.0) -> int:
        if model_id not in self._fails:
            return 0
        buf = self._fails[model_id]
        cutoff = time.monotonic() - window
        while buf and buf[0] < cutoff:
            buf.popleft()
        return len(buf)


# ---------------------------------------------------------------------------
# SelectionLog — circular buffer capped at 100
# ---------------------------------------------------------------------------

class SelectionLog:
    """In-memory circular buffer of the last 100 routing decisions."""

    def __init__(self, cap: int = 100) -> None:
        self._buf: deque = deque(maxlen=cap)

    def append(self, entry: ModelEntry, task: Dict, score: float, top_candidates: Optional[List[Dict]] = None) -> None:
        self._buf.appendleft(
            SelectionRecord(
                model_id=entry.model_id,
                endpoint_name=entry.endpoint_name,
                task_type=task.get("task_type", "chat"),
                reasoning_depth=task.get("reasoning_depth", "light"),
                score=round(score, 4),
                timestamp=datetime.datetime.utcnow().isoformat() + "Z",
                task=task,
                top_candidates=[
                    {"model_id": c["model_id"], "endpoint_name": c["endpoint_name"], "score": round(c["score"], 4)}
                    for c in (top_candidates or [])[:3]
                ],
            )
        )

    def recent(self, n: int = 20) -> List[SelectionRecord]:
        return list(self._buf)[:n]

# ---------------------------------------------------------------------------
# Registry — in-memory model index
# ---------------------------------------------------------------------------

class Registry:
    """Thread-safe in-memory model registry with async refresh."""

    _CONTEXT_THRESHOLDS = {
        "tiny": 2_000,
        "small": 8_000,
        "medium": 32_000,
        "large": 128_000,
        "huge": 1_000_000,
    }

    _TASK_SKILL_MAP = {
        "chat": "reasoning",
        "coding": "coding",
        "vision": "vision",
        "summarization": "reasoning",
        "translation": "reasoning",
        "math": "reasoning",
        "research": "reasoning",
        "creative_writing": "creativity",
        "roleplay": "reasoning",
        "search_rag": "reasoning",
        "data_analysis": "coding",
        "agentic_tool_use": "tool_use",
        "planning": "reasoning",
        "classification": "reasoning",
    }

    _MODEL_CAPABILITIES: Dict[str, Dict[str, Any]] = {
        "openai/gpt-4o": {
            "reasoning": 88, "coding": 92, "vision": 90, "tool_use": True, "latency": 75, "cost": 65,
            "context_window": 128000, "rpm": 500, "tpm": 160000, "tpd": 1000000,
        },
        "openai/gpt-4o-mini": {
            "reasoning": 80, "coding": 78, "vision": 70, "tool_use": True, "latency": 85, "cost": 85,
            "context_window": 128000, "rpm": 1000, "tpm": 200000, "tpd": 2000000,
        },
        "openai/o3": {
            "reasoning": 99, "coding": 95, "vision": 85, "tool_use": True, "latency": 40, "cost": 30,
            "context_window": 200000, "rpm": 500, "tpm": 150000, "tpd": 1000000,
        },
        "openai/o4-mini": {
            "reasoning": 95, "coding": 90, "vision": 70, "tool_use": True, "latency": 70, "cost": 60,
            "context_window": 200000, "rpm": 500, "tpm": 150000, "tpd": 1000000,
        },
        "openai/gpt-5": {
            "reasoning": 97, "coding": 96, "vision": 92, "tool_use": True, "latency": 60, "cost": 40,
            "context_window": 400000, "rpm": 500, "tpm": 200000, "tpd": 1500000,
        },
        "openai/gpt-5-mini": {
            "reasoning": 88, "coding": 85, "vision": 75, "tool_use": True, "latency": 85, "cost": 70,
            "context_window": 400000, "rpm": 1000, "tpm": 300000, "tpd": 2000000,
        },
        "openai/gpt-5-nano": {
            "reasoning": 80, "coding": 75, "vision": 60, "tool_use": True, "latency": 95, "cost": 90,
            "context_window": 400000, "rpm": 2000, "tpm": 500000, "tpd": 5000000,
        },
        "anthropic/claude-opus-4": {
            "reasoning": 99, "coding": 90, "vision": 80, "tool_use": True, "latency": 50, "cost": 25,
            "context_window": 200000, "rpm": 1000, "tpm": 100000, "tpd": 1000000,
        },
        "anthropic/claude-sonnet-4": {
            "reasoning": 95, "coding": 88, "vision": 75, "tool_use": True, "latency": 65, "cost": 45,
            "context_window": 200000, "rpm": 1000, "tpm": 100000, "tpd": 1000000,
        },
        "anthropic/claude-haiku-4-5": {
            "reasoning": 82, "coding": 70, "vision": 65, "tool_use": True, "latency": 95, "cost": 85,
            "context_window": 200000, "rpm": 2000, "tpm": 200000, "tpd": 2000000,
        },
        "google/gemini-2.5-pro": {
            "reasoning": 95, "coding": 88, "vision": 90, "tool_use": True, "latency": 55, "cost": 50,
            "context_window": 1048000, "rpm": 360, "tpm": 4000000, "tpd": 10000000,
        },
        "google/gemini-2.5-flash": {
            "reasoning": 85, "coding": 80, "vision": 85, "tool_use": True, "latency": 90, "cost": 75,
            "context_window": 1048000, "rpm": 1000, "tpm": 8000000, "tpd": 50000000,
        },
        "deepseek/deepseek-r1": {
            "reasoning": 92, "coding": 90, "vision": 40, "tool_use": False, "latency": 55, "cost": 80,
            "context_window": 64000, "rpm": 1000, "tpm": 500000, "tpd": 1000000,
        },
        "deepseek/deepseek-chat": {
            "reasoning": 88, "coding": 85, "vision": 40, "tool_use": False, "latency": 65, "cost": 85,
            "context_window": 64000, "rpm": 1000, "tpm": 500000, "tpd": 1000000,
        },
        "groq/compound": {
            "reasoning": 88, "coding": 82, "vision": 50, "tool_use": True, "latency": 98, "cost": 70,
            "context_window": 128000, "rpm": 30, "tpm": 20000, "tpd": 1000000,
        },
        "groq/compound-mini": {
            "reasoning": 80, "coding": 75, "vision": 50, "tool_use": True, "latency": 99, "cost": 80,
            "context_window": 128000, "rpm": 30, "tpm": 20000, "tpd": 1000000,
        },
        "llama-3.3-70b-versatile": {
            "reasoning": 82, "coding": 75, "vision": 40, "tool_use": False, "latency": 80, "cost": 80,
            "context_window": 128000, "rpm": 30, "tpm": 12000, "tpd": 100000,
        },
        "meta-llama/llama-4-maverick": {
            "reasoning": 85, "coding": 80, "vision": 60, "tool_use": True, "latency": 60, "cost": 50,
            "context_window": 1000000, "rpm": 500, "tpm": 300000, "tpd": 5000000,
        },
        "qwen/qwen3-coder": {
            "reasoning": 88, "coding": 95, "vision": 50, "tool_use": True, "latency": 70, "cost": 60,
            "context_window": 128000, "rpm": 500, "tpm": 200000, "tpd": 2000000,
        },
        "qwen/qwen3-max": {
            "reasoning": 90, "coding": 85, "vision": 70, "tool_use": True, "latency": 65, "cost": 55,
            "context_window": 128000, "rpm": 500, "tpm": 200000, "tpd": 2000000,
        },
        "x-ai/grok-4.20": {
            "reasoning": 93, "coding": 88, "vision": 80, "tool_use": True, "latency": 55, "cost": 60,
            "context_window": 128000, "rpm": 500, "tpm": 250000, "tpd": 2000000,
        },
        "moonshotai/kimi-k2.6": {
            "reasoning": 90, "coding": 82, "vision": 65, "tool_use": True, "latency": 65, "cost": 55,
            "context_window": 128000, "rpm": 500, "tpm": 200000, "tpd": 2000000,
        },
    }

    _PROVIDER_DEFAULTS: Dict[str, Dict[str, Any]] = {
        "default": {
            "context_window": 4096, "rpm": 60, "tpm": 100000, "tpd": 500000,
            "reasoning": 50, "coding": 50, "vision": 0, "tool_use": True, "latency": 50, "cost": 50,
        },
        "openrouter": {
            "context_window": 8192, "rpm": 200, "tpm": 200000, "tpd": 1000000,
            "reasoning": 70, "coding": 65, "vision": 40, "tool_use": True, "latency": 70, "cost": 50,
        },
        "openai": {
            "context_window": 16384, "rpm": 500, "tpm": 300000, "tpd": 2000000,
            "reasoning": 85, "coding": 80, "vision": 60, "tool_use": True, "latency": 70, "cost": 55,
        },
        "anthropic": {
            "context_window": 200000, "rpm": 1000, "tpm": 100000, "tpd": 1000000,
            "reasoning": 90, "coding": 80, "vision": 60, "tool_use": True, "latency": 70, "cost": 50,
        },
        "google": {
            "context_window": 1048000, "rpm": 15, "tpm": 250000, "tpd": 1500,
            "reasoning": 85, "coding": 80, "vision": 85, "tool_use": True, "latency": 75, "cost": 50,
        },
        "deepseek": {
            "context_window": 64000, "rpm": 1000, "tpm": 500000, "tpd": 1000000,
            "reasoning": 85, "coding": 85, "vision": 0, "tool_use": False, "latency": 70, "cost": 75,
        },
        "fireworks": {
            "context_window": 32768, "rpm": 100, "tpm": 200000, "tpd": 1000000,
            "reasoning": 70, "coding": 65, "vision": 30, "tool_use": True, "latency": 80, "cost": 55,
        },
        "groq": {
            "context_window": 32768, "rpm": 30, "tpm": 20000, "tpd": 1000000,
            "reasoning": 75, "coding": 70, "vision": 30, "tool_use": True, "latency": 98, "cost": 70,
        },
        "ollama": {
            "context_window": 4096, "rpm": None, "tpm": None, "tpd": None,
            "reasoning": 50, "coding": 50, "vision": 0, "tool_use": True, "latency": 30, "cost": 100,
        },
        "lmstudio": {
            "context_window": 4096, "rpm": None, "tpm": None, "tpd": None,
            "reasoning": 50, "coding": 50, "vision": 0, "tool_use": True, "latency": 30, "cost": 100,
        },
        "llamacpp": {
            "context_window": 4096, "rpm": None, "tpm": None, "tpd": None,
            "reasoning": 50, "coding": 50, "vision": 0, "tool_use": True, "latency": 30, "cost": 100,
        },
    }

    @staticmethod
    def _normalize_mid(mid: str) -> str:
        return (mid or "").strip().lower()

    @classmethod
    def _capabilities_for(cls, model: ModelEntry) -> Dict[str, Any]:
        mid = cls._normalize_mid(model.model_id)
        model_id = mid
        for key in cls._MODEL_CAPABILITIES:
            if mid == key or mid.startswith(key + ":"):
                model_id = key
                break
        caps = cls._MODEL_CAPABILITIES.get(model_id)
        if not caps:
            caps = cls._infer_capabilities(model)
        return caps

    @classmethod
    def _infer_capabilities(cls, model: ModelEntry) -> Dict[str, Any]:
        mid = cls._normalize_mid(model.model_id)
        provider_key = (model.provider or "").lower()
        caps = dict(cls._PROVIDER_DEFAULTS.get(provider_key, cls._PROVIDER_DEFAULTS["default"]))

        # Model family overrides for common patterns
        if any(x in mid for x in ["gpt-4o", "gpt-4.1", "gpt-5.1", "gpt-5.2", "gpt-5.3", "gpt-5.4", "gpt-5.5"]):
            caps["context_window"] = 200000
            caps["rpm"] = 500
            caps["tpm"] = 300000
            caps["tpd"] = 2000000
        if any(x in mid for x in ["gpt-4-turbo", "gpt-4-1106", "gpt-4o-2024-11-20"]):
            caps["context_window"] = 128000
            caps["rpm"] = 500
            caps["tpm"] = 300000
        if any(x in mid for x in ["gpt-4o-mini", "gpt-4o-mini-2024-07-18"]):
            caps["context_window"] = 128000
            caps["rpm"] = 1000
            caps["tpm"] = 200000
        if "gpt-3.5" in mid:
            caps["context_window"] = 16385
            caps["rpm"] = 500
            caps["tpm"] = 80000
        if any(x in mid for x in ["o1", "o3", "o4"]):
            caps["context_window"] = 200000
            caps["rpm"] = 500
            caps["tpm"] = 150000
            caps["reasoning"] = 95
            caps["coding"] = 90
            caps["cost"] = 30
            caps["latency"] = 40
        if any(x in mid for x in ["claude-opus"]):
            caps["context_window"] = 200000
            caps["rpm"] = 1000
            caps["tpm"] = 100000
            caps["reasoning"] = 99
            caps["coding"] = 90
            caps["cost"] = 25
            caps["latency"] = 50
        if any(x in mid for x in ["claude-sonnet"]):
            caps["context_window"] = 200000
            caps["rpm"] = 1000
            caps["tpm"] = 100000
            caps["reasoning"] = 95
            caps["coding"] = 88
            caps["cost"] = 45
            caps["latency"] = 65
        if any(x in mid for x in ["claude-haiku"]):
            caps["context_window"] = 200000
            caps["rpm"] = 2000
            caps["tpm"] = 200000
            caps["reasoning"] = 82
            caps["coding"] = 70
            caps["cost"] = 85
            caps["latency"] = 95
        if any(x in mid for x in ["gemini-2.5-pro", "gemini-3-pro", "gemini-3.1-pro"]):
            caps["context_window"] = 1048000
            caps["rpm"] = 360
            caps["tpm"] = 4000000
            caps["tpd"] = 10000000
            caps["reasoning"] = 95
            caps["coding"] = 88
            caps["vision"] = 90
            caps["latency"] = 55
            caps["cost"] = 50
        if any(x in mid for x in ["gemini-2.5-flash", "gemini-3-flash", "gemini-3.1-flash", "gemini-2.0-flash"]):
            caps["context_window"] = 1048000
            caps["rpm"] = 1000
            caps["tpm"] = 8000000
            caps["tpd"] = 50000000
            caps["reasoning"] = 85
            caps["coding"] = 80
            caps["vision"] = 85
            caps["latency"] = 90
            caps["cost"] = 75
        if any(x in mid for x in ["gemini-2.5-flash-lite", "gemini-2.0-flash-lite"]):
            caps["context_window"] = 1048000
            caps["rpm"] = 1500
            caps["tpm"] = 10000000
            caps["reasoning"] = 75
            caps["coding"] = 65
            caps["vision"] = 70
            caps["latency"] = 95
            caps["cost"] = 80
        if "deepseek-r1" in mid:
            caps["context_window"] = 64000
            caps["rpm"] = 1000
            caps["tpm"] = 500000
            caps["reasoning"] = 92
            caps["coding"] = 90
            caps["vision"] = 40
            caps["tool_use"] = False
            caps["latency"] = 55
            caps["cost"] = 80
        if "deepseek-chat" in mid or "deepseek-v3" in mid:
            caps["context_window"] = 64000
            caps["rpm"] = 1000
            caps["tpm"] = 500000
            caps["reasoning"] = 88
            caps["coding"] = 85
            caps["vision"] = 40
            caps["tool_use"] = False
            caps["latency"] = 65
            caps["cost"] = 85
        if any(x in mid for x in ["llama-3.3-70b", "llama-3.1-70b", "llama-3.1-8b"]):
            caps["context_window"] = 128000
            caps["rpm"] = 30
            caps["tpm"] = 12000
            caps["reasoning"] = 80
            caps["coding"] = 70
            caps["latency"] = 80
            caps["cost"] = 80
        if "llama-4" in mid:
            caps["context_window"] = 1000000
            caps["rpm"] = 500
            caps["tpm"] = 300000
            caps["reasoning"] = 85
            caps["coding"] = 80
            caps["vision"] = 60
            caps["tool_use"] = True
            caps["latency"] = 60
            caps["cost"] = 50
        if "qwen3" in mid or "qwen-2.5" in mid:
            caps["context_window"] = 128000
            caps["rpm"] = 500
            caps["tpm"] = 200000
            caps["reasoning"] = 85
            caps["coding"] = 80
            caps["tool_use"] = True
        if "kimi-k2" in mid:
            caps["context_window"] = 128000
            caps["rpm"] = 500
            caps["tpm"] = 200000
            caps["reasoning"] = 90
            caps["coding"] = 82
            caps["tool_use"] = True
            caps["latency"] = 65
            caps["cost"] = 55
        if "grok" in mid:
            caps["context_window"] = 128000
            caps["rpm"] = 500
            caps["tpm"] = 250000
            caps["reasoning"] = 90
            caps["coding"] = 85
            caps["vision"] = 70
            caps["tool_use"] = True
            caps["latency"] = 60
            caps["cost"] = 55
        if "mistral-large" in mid or "mistral-medium" in mid or "ministral" in mid:
            caps["context_window"] = 128000
            caps["rpm"] = 500
            caps["tpm"] = 150000
            caps["reasoning"] = 85
            caps["coding"] = 75
            caps["tool_use"] = True
            caps["latency"] = 70
            caps["cost"] = 55
        if "gemma-4" in mid or "gemma-3" in mid:
            caps["context_window"] = 128000
            caps["rpm"] = 500
            caps["tpm"] = 200000
            caps["reasoning"] = 75
            caps["coding"] = 65
            caps["vision"] = 50
            caps["tool_use"] = True
            caps["latency"] = 80
            caps["cost"] = 60
        if any(x in mid for x in ["compound", "compound-mini"]):
            caps["context_window"] = 128000
            caps["rpm"] = 30
            caps["tpm"] = 20000
            caps["reasoning"] = 85
            caps["coding"] = 80
            caps["tool_use"] = True
            caps["latency"] = 98
            caps["cost"] = 70
        if ":free" in mid or "free" in mid.split("/")[-1]:
            caps["rpm"] = 20
            caps["tpm"] = 100000
            caps["tpd"] = 50000
            caps["cost"] = 100
            caps["latency"] = 25

        # Local providers never have rate limits — clear them regardless of overrides above
        if provider_key in ("ollama", "lmstudio", "llamacpp"):
            caps["rpm"] = None
            caps["tpm"] = None
            caps["tpd"] = None
        return caps

    def __init__(self) -> None:
        self._flat: List[ModelEntry] = []
        self._endpoints: Dict[str, Dict] = {}
        self._lock = asyncio.Lock()
        self._refresh_event = asyncio.Event()

    # ---- public hot-path API (no I/O) ------------------------------------

    def get_flat(self) -> List[ModelEntry]:
        return list(self._flat)

    def notify_changed(self) -> None:
        """Signal the background loop to refresh immediately."""
        self._refresh_event.set()

    # ---- provider / URL utilities ----------------------------------------

    @staticmethod
    def _detect_provider(url: str) -> str:
        u = (url or "").lower()
        if "openrouter" in u: return "openrouter"
        if "groq" in u: return "groq"
        if "google" in u or "generativelanguage" in u: return "google"
        if "deepseek" in u: return "deepseek"
        if "fireworks" in u: return "fireworks"
        if "anthropic" in u: return "anthropic"
        if "ollama" in u or ":11434" in u: return "ollama"
        if ":1234" in u: return "lmstudio"
        if ":5050" in u or ":8080" in u: return "llamacpp"
        if "openai" in u: return "openai"
        return "openai"

    @staticmethod
    def _resolve_chat_url(base_url: str, provider: str) -> str:
        base = (base_url or "").strip().rstrip("/")
        for suffix in ["/models", "/chat/completions", "/v1/messages", "/tags", "/api/tags"]:
            if base.endswith(suffix):
                base = base[: -len(suffix)].rstrip("/")
                break
        if provider == "ollama":
            root = base[:-3] if base.endswith("/v1") else base
            return root.rstrip("/") + "/api/chat"
        if provider == "anthropic":
            root = base[:-3] if base.endswith("/v1") else base
            return root + "/v1/messages"
        if base.endswith("/v1"):
            return base + "/chat/completions"
        return base + "/v1/chat/completions"

    # ---- classify / score / select (pure, no I/O) ------------------------

    @staticmethod
    def classify(messages: List[dict], requires_tools: bool = False) -> Dict[str, Any]:
        text = " ".join(str(m.get("content", "")) for m in messages).lower()
        has_image = any(
            isinstance(m.get("content"), list) and any(
                isinstance(b, dict) and b.get("type") == "image_url"
                for b in m["content"]
            )
            for m in messages
        )
        task_type = "chat"
        for kws, tt in [
            (["code", "python", "javascript", "function", "debug", "program", "implement", "scraper"], "coding"),
            (["image", "picture", "photo", "visual", "analyze this"], "vision"),
            (["summarize", "summary", "condense", "tldr"], "summarization"),
            (["translate", "translation"], "translation"),
            (["math", "calculate", "equation"], "math"),
            (["research", "paper", "study"], "research"),
            (["creative", "story", "poem", "fiction"], "creative_writing"),
            (["roleplay", "act as", "pretend"], "roleplay"),
            (["search", "find", "look up", "google"], "search_rag"),
            (["data", "csv", "json", "parse", "extract"], "data_analysis"),
            (["tool", "call", "mcp", "function_call"], "agentic_tool_use"),
            (["plan", "strategy", "roadmap", "steps"], "planning"),
            (["classify", "categorize", "label", "sentiment"], "classification"),
        ]:
            if any(x in text for x in kws):
                task_type = tt
                break
        if requires_tools and task_type == "chat":
            task_type = "agentic_tool_use"
        return {
            "task_type": task_type,
            "reasoning_depth": (
                "extreme" if any(x in text for x in ["design", "architecture", "distributed", "prove", "derive", "dissertation"])
                else "heavy" if any(x in text for x in ["analyze", "compare", "evaluate", "comprehensive"])
                else "medium" if any(x in text for x in ["explain", "describe", "walkthrough", "reasoning"])
                else "light"
            ),
            "context_requirement": (
                "huge" if len(text) > 128_000
                else "large" if len(text) > 32_000
                else "medium" if len(text) > 8_000
                else "small" if len(text) > 2_000
                else "tiny"
            ),
            "output_requirement": (
                "very_long" if any(x in text for x in ["generate", "write a", "create a", "complete", "detailed", "book"])
                else "long" if any(x in text for x in ["explain", "describe", "walkthrough", "essay"])
                else "short" if any(x in text for x in ["brief", "short", "quick", "yes or no"])
                else "medium"
            ),
            "coding_specialization": next((v for kws, v in [
                (["frontend", "react", "vue", "html", "css", "ui"], "frontend"),
                (["backend", "server", "api", "database", "sql"], "backend"),
                (["python"], "python"),
                (["javascript", "js", "typescript", "ts"], "javascript"),
                (["rust"], "rust"),
                (["c++", "cpp", "c#"], "cpp"),
                (["devops", "docker", "kubernetes", "ci/cd"], "devops"),
                (["security", "vulnerability", "exploit", "penetration"], "security"),
            ] if any(x in text for x in kws)), "general"),
            "tool_use": next((v for kws, v in [
                (["search", "web", "google", "look up"], "search"),
                (["browser", "navigate", "scrape", "crawl"], "browser"),
                (["run code", "execute", "interpreter"], "code_interpreter"),
                (["tool", "mcp", "function"], "function_calling"),
            ] if any(x in text for x in kws)), "none"),
            "latency_tolerance": next((v for kws, v in [
                (["real-time", "realtime", "voice", "streaming"], "realtime"),
                (["fast", "quick", "speed", "instantly"], "fast"),
                (["slow", "take your time", "no rush", "thorough"], "slow_ok"),
            ] if any(x in text for x in kws)), "normal"),
            "budget_tier": next((v for kws, v in [
                (["cheap", "free", "cost", "budget", "save money"], "cheapest"),
                (["best", "premium", "strongest", "most capable", "expensive"], "premium"),
            ] if any(x in text for x in kws)), "balanced"),
            "multimodal": "image_input" if has_image else "text_only",
            "safety": next((v for kws, v in [
                (["medical", "health", "diagnosis", "patient"], "medical"),
                (["legal", "law", "contract", "compliance"], "legal"),
                (["financial", "investment", "trading", "accounting"], "financial"),
            ] if any(x in text for x in kws)), "normal"),
            "structured_output": next((v for kws, v in [
                (["json"], "json"),
                (["xml"], "xml"),
                (["markdown", "md"], "markdown"),
                (["sql", "query", "database"], "sql"),
            ] if any(x in text for x in kws)), "free_text"),
        }

    @staticmethod
    def score(model: ModelEntry, task: Dict, rate_tracker: "RateLimitTracker") -> float:
        mid = (model.model_id or "").lower()
        chat_url = model.chat_url or ""
        caps = Registry._capabilities_for(model)
        w = _active_weights.get("global", {})

        skill = Registry._TASK_SKILL_MAP.get(task["task_type"], "reasoning")
        skill_score = caps.get(skill, 50)
        reasoning = task["reasoning_depth"]
        reasoning_score = caps.get("reasoning", 50)
        coding_spec = task.get("coding_specialization", "general")
        tool_use = task.get("tool_use", "none")
        latency = task.get("latency_tolerance", "normal")
        budget = task.get("budget_tier", "balanced")
        structured = task.get("structured_output", "free_text")

        s = 1.0

        if model.context_window is not None and getattr(model, "_est_tokens", 0) > model.context_window:
            return -999.0

        if any(x in chat_url for x in ("192.168", "localhost", "127.0.0.1", ":11434", ":1234", ":8080")):
            s += 0.1

        s += (skill_score / 100.0) * float(w.get("task_match", 0.35))

        if reasoning in ("heavy", "extreme"):
            s += (reasoning_score / 100.0) * float(w.get("reasoning", 0.25))
        elif reasoning == "medium":
            s += (reasoning_score / 100.0) * float(w.get("reasoning", 0.25)) * 0.6
        else:
            s += (reasoning_score / 100.0) * float(w.get("reasoning", 0.25)) * 0.2

        if coding_spec != "general" and task["task_type"] == "coding":
            s += (caps.get("coding", 50) / 100.0) * float(w.get("task_match", 0.35)) * 0.6

        ctx_req = task["context_requirement"]
        ctx_threshold = Registry._CONTEXT_THRESHOLDS.get(ctx_req, 0)
        if model.context_window and model.context_window >= ctx_threshold:
            s += float(w.get("context_fit", 0.10))

        if tool_use != "none" and caps.get("tool_use", False):
            s += float(w.get("tool_use", 0.15))

        if task.get("multimodal") == "image_input":
            if caps.get("vision", 0) >= 70:
                s += float(w.get("task_match", 0.35)) * 0.4
            elif caps.get("vision", 0) < 40:
                s -= 0.5

        latency_score = caps.get("latency", 50)
        if latency == "realtime":
            s += (latency_score / 100.0) * float(w.get("latency", 0.10))
        elif latency == "fast":
            s += (latency_score / 100.0) * float(w.get("latency", 0.10)) * 0.5
        elif latency == "slow_ok" and model.provider in ("ollama", "lmstudio", "llamacpp"):
            s += 0.1

        cost_score = caps.get("cost", 50)
        if budget == "cheapest":
            s += (cost_score / 100.0) * float(w.get("cost", 0.15))
        elif budget == "premium":
            s += ((100 - cost_score) / 100.0) * float(w.get("cost", 0.15)) * 0.7

        if task["task_type"] == "creative_writing":
            s += (caps.get("creativity", 50) / 100.0) * 0.15

        if structured != "free_text" and caps.get("reasoning", 50) >= 80:
            s += 0.08

        if model.rpm is not None and rate_tracker.recent_count(model.model_id) >= model.rpm:
            s -= 0.5

        fails = failure_tracker.recent_failures(model.model_id)
        s -= min(fails * 0.3, 1.5)

        recency = rate_tracker.recent_count(model.model_id)
        s -= min(recency * 0.1, 1.0)

        import random
        s += random.uniform(0, 0.5)
        return s

    def select(
        self,
        messages: List[dict],
        requires_tools: bool = False,
        rate_tracker: Optional["RateLimitTracker"] = None,
        min_context: Optional[int] = None,
    ) -> Optional[tuple]:
        """Returns (ModelEntry, score, task) or None."""
        flat = self.get_flat()
        if not flat:
            return None
        task = self.classify(messages, requires_tools=requires_tools)
        est = _estimate_tokens(messages)
        rt = rate_tracker or RateLimitTracker()

        candidates = []
        for m in flat:
            if min_context and m.context_window and m.context_window < min_context:
                continue
            m._est_tokens = est  # type: ignore[attr-defined]
            sc = self.score(m, task, rt)
            if sc > -999.0:
                candidates.append((sc, m))

        if not candidates:
            logger.warning(
                "SELECT: no viable candidates for task=%s after context filter. flat=%d",
                task["task_type"], len(self.get_flat()),
            )
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        sc, pick = candidates[0]
        logger.info(
            "SELECTED model=%s endpoint=%s task=%s score=%.3f viable=%d top1=%.3f top3=%s",
            pick.model_id, pick.endpoint_name, task["task_type"], sc,
            len(candidates),
            candidates[0][0],
            [(m.model_id, f"{s:.3f}") for s, m in candidates[:3]],
        )
        top3 = [
            {"model_id": m.model_id, "endpoint_name": m.endpoint_name, "score": round(s, 4)}
            for s, m in candidates[:3]
        ]
        return pick, sc, task, top3

    # ---- refresh (called from background task) ---------------------------

    async def refresh(self) -> None:
        """Sync providers.json + Odysseus DB → apply self-loop filter → update flat list."""
        own = _own_addresses(5000)
        providers = _load_providers(PROVIDERS_PATH)

        # Dedupe providers.json entries by port first
        seen_ports: Dict[int, int] = {}
        deduped: List[Provider] = []
        for p in providers:
            try:
                port = urlparse(p.base_url).port
            except Exception:
                port = None
            if port is not None and port in seen_ports:
                continue
            if port is not None:
                seen_ports[port] = 1
            deduped.append(p)
        providers = deduped

        # Optional Odysseus DB supplement — dedupe by port against existing providers
        db_providers = _read_odysseus_db()
        for dbp in db_providers:
            try:
                db_port = urlparse(dbp.base_url).port
            except Exception:
                db_port = None
            if db_port is not None and db_port in seen_ports:
                continue
            if db_port is not None:
                seen_ports[db_port] = 1
            providers.append(dbp)

        flat: List[ModelEntry] = []
        endpoints: Dict[str, Dict] = {}

        for p in providers:
            if not p.is_enabled or not p.base_url:
                continue
            provider_type = self._detect_provider(p.base_url)
            chat_url = self._resolve_chat_url(p.base_url, provider_type)
            if _is_self_loop(chat_url, own):
                continue

            endpoints[p.id] = {"id": p.id, "name": p.name, "base_url": p.base_url}
            for mid in p.cached_models:
                entry = ModelEntry(
                    endpoint_id=p.id,
                    endpoint_name=p.name,
                    model_id=mid,
                    provider=provider_type,
                    chat_url=chat_url,
                    api_key=p.api_key,
                    model_type="llm",
                )
                flat.append(entry)

        async with self._lock:
            self._flat = flat
            self._endpoints = endpoints

        logger.info("Registry refreshed: %d endpoints, %d models", len(endpoints), len(flat))

        # Fire one capacity-fetch task per provider (not per model) to avoid
        # hammering the same API endpoint once for every model in the list.
        seen_providers: set = set()
        for entry in flat:
            if entry.context_window is None and entry.endpoint_id not in seen_providers:
                seen_providers.add(entry.endpoint_id)
                # Pass all entries for this provider so the fetcher can
                # populate them all from a single API call.
                provider_entries = [e for e in flat if e.endpoint_id == entry.endpoint_id]
                asyncio.create_task(_fetch_capacity_for_provider(provider_entries))

        # Backfill any still-missing limits from the static capability table or inference.
        for entry in flat:
            provider_key = (entry.provider or "").lower()
            caps = Registry._capabilities_for(entry)
            if entry.context_window is None and caps.get("context_window"):
                entry.context_window = caps["context_window"]
            # Local providers never show rate limits — skip RPM/TPM/TPD entirely
            if provider_key in ("ollama", "lmstudio", "llamacpp"):
                continue
            if entry.rpm is None and caps.get("rpm"):
                entry.rpm = caps["rpm"]
            if entry.tpm is None and caps.get("tpm"):
                entry.tpm = caps["tpm"]
            if entry.tpd is None and caps.get("tpd"):
                entry.tpd = caps["tpd"]

# ---------------------------------------------------------------------------
# Odysseus DB optional reader
# ---------------------------------------------------------------------------

def _read_odysseus_db() -> List[Provider]:
    path = _get_odysseus_db_path()
    if not path or not os.path.exists(path):
        if path:
            logger.warning("ODYSSEUS_DB_PATH set to %r but file not found", path)
        return []
    try:
        conn = sqlite3.connect(path, timeout=3.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, name, base_url, api_key, is_enabled, cached_models, model_type "
            "FROM model_endpoints WHERE is_enabled = 1"
        ).fetchall()
        conn.close()
        result = []
        for row in rows:
            cached: List[str] = []
            if row["cached_models"]:
                try:
                    loaded = json.loads(row["cached_models"])
                    if isinstance(loaded, list):
                        cached = [str(x) for x in loaded]
                except Exception:
                    pass
            result.append(Provider(
                id=f"odysseus_{row['id']}",
                name=row["name"] or "",
                base_url=row["base_url"] or "",
                api_key=row["api_key"] or "",
                is_enabled=bool(row["is_enabled"]),
                cached_models=cached,
            ))
        return result
    except Exception as exc:
        logger.warning("Odysseus DB read failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Batched capacity fetch (one API call per provider, not per model)
# ---------------------------------------------------------------------------

async def _fetch_capacity_for_provider(entries: List[ModelEntry]) -> None:
    """Fetch capacity metadata: local providers get /v1/models; cloud uses specific batch fetchers."""
    if not entries:
        return
    first = entries[0]
    cache_key = (first.provider, first.endpoint_id)
    if cache_key in _CAPACITY_CACHE:
        return
    _CAPACITY_CACHE.add(cache_key)
    try:
        if first.provider == "openrouter":
            await _fetch_openrouter_batch(entries)
        elif first.provider == "groq":
            await _fetch_groq_batch(entries)
        elif first.provider in ("ollama", "lmstudio", "llamacpp"):
            await _fetch_local_batch(entries)
    except Exception as exc:
        logger.debug("Batch capacity fetch failed for provider %s: %s", first.endpoint_name, exc)


async def _fetch_local_batch(entries: List[ModelEntry]) -> None:
    """Fetch /v1/models once for local OpenAI-compatible endpoints, distribute to entries."""
    if not entries:
        return
    first = entries[0]
    base = first.chat_url
    for suffix in ["/v1/chat/completions", "/chat/completions"]:
        if base.endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
            break
    models_url = base.rstrip("/") + "/v1/models"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(models_url)
            r.raise_for_status()
            model_map = {m["id"]: m for m in r.json().get("data", [])}
        for entry in entries:
            m = model_map.get(entry.model_id)
            if m:
                entry.context_window = (
                    m.get("context_length")
                    or m.get("max_context_length")
                    or m.get("context_window")
                    or None
                )
    except Exception as exc:
        logger.debug("Local batch capacity fetch failed for %s: %s", first.endpoint_name, exc)


async def _fetch_openrouter_batch(entries: List[ModelEntry]) -> None:
    """Fetch all OpenRouter model metadata in one request, distribute to entries."""
    first = entries[0]
    headers = {"Content-Type": "application/json"}
    if first.api_key:
        headers["Authorization"] = f"Bearer {first.api_key}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get("https://openrouter.ai/api/v1/models", headers=headers)
            r.raise_for_status()
            model_map = {m["id"]: m for m in r.json().get("data", [])}
    except Exception as exc:
        logger.debug("OpenRouter batch capacity fetch failed: %s", exc)
        return

    for entry in entries:
        m = model_map.get(entry.model_id)
        if m:
            if m.get("context_length"):
                entry.context_window = m["context_length"]
            limits = m.get("per_request_limits") or {}
            pt = limits.get("prompt_tokens")
            if pt:
                entry.tpm = int(pt)


async def _fetch_groq_batch(entries: List[ModelEntry]) -> None:
    """Apply Groq static table to all entries; fetch context_window from API once."""
    from router.capacity import GROQ_STATIC_TABLE
    # Apply static table first
    for entry in entries:
        static = GROQ_STATIC_TABLE.get(entry.model_id)
        if static:
            entry.rpm = static.get("rpm")
            entry.tpm = static.get("tpm")
            entry.tpd = static.get("tpd")
            entry.context_window = static.get("context_window")

    # Fetch live context_window for any still missing
    missing = [e for e in entries if e.context_window is None and entries[0].api_key]
    if missing:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {entries[0].api_key}"},
                )
                r.raise_for_status()
                model_map = {m["id"]: m for m in r.json().get("data", [])}
            for entry in missing:
                m = model_map.get(entry.model_id)
                if m:
                    entry.context_window = m.get("context_window") or None
        except Exception as exc:
            logger.debug("Groq live capacity fetch failed: %s", exc)


# ---------------------------------------------------------------------------
# Local discovery
# ---------------------------------------------------------------------------

PROBES = [
    ("http://localhost:11434", "/api/tags",   "ollama",   "ollama"),
    ("http://localhost:1234",  "/v1/models",  "lmstudio", "lmstudio"),
    ("http://localhost:5050",  "/v1/models",  "llamacpp", "llamacpp"),
]


def _parse_model_list(data: Any, svc: str) -> List[str]:
    if svc == "ollama":
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    # LM Studio / llama.cpp: OpenAI-style {"data": [{"id": ...}]}
    return [m.get("id", "") for m in data.get("data", []) if m.get("id")]


async def discover_local() -> None:
    """Probe well-known local inference ports and update providers.json."""
    providers = _load_providers(PROVIDERS_PATH)
    changed = False

    async with httpx.AsyncClient(timeout=2.0) as client:
        for probe_url, path, svc, provider_type in PROBES:
            try:
                r = await client.get(probe_url + path)
                r.raise_for_status()
                model_ids = _parse_model_list(r.json(), svc)
                probe_port = urlparse(probe_url).port
                existing_idx = next(
                    (i for i, p in enumerate(providers) if urlparse(p.base_url).port == probe_port),
                    None,
                )
                if existing_idx is not None:
                    providers[existing_idx].cached_models = model_ids
                    changed = True
                    logger.info("LocalDiscovery: updated %s with %d models", probe_url, len(model_ids))
                else:
                    new_p = Provider(
                        id=str(uuid.uuid4()),
                        name=f"Local {svc}",
                        base_url=probe_url,
                        api_key="",
                        is_enabled=True,
                        cached_models=model_ids,
                    )
                    providers.append(new_p)
                    changed = True
                    logger.info("LocalDiscovery: added %s with %d models", probe_url, len(model_ids))
            except Exception as exc:
                logger.debug("LocalDiscovery probe %s failed: %s", probe_url, exc)

    if changed:
        _atomic_write_providers(PROVIDERS_PATH, providers)

# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def _estimate_tokens(messages: List[dict]) -> int:
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(content.encode("utf-8")) // 4
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        total += len(block.get("text", "").encode("utf-8")) // 4
                    elif block.get("type") == "image_url":
                        total += 765
        total += 4  # per-message overhead
    return total


# ---------------------------------------------------------------------------
# Auth headers helper
# ---------------------------------------------------------------------------

def _build_auth_headers(entry: ModelEntry) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if not entry.api_key:
        return headers
    parsed = httpx.URL(entry.chat_url)
    host = parsed.host or ""
    is_local = host in ("localhost", "127.0.0.1", "::1") or host.startswith(
        ("192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.",
         "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
         "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")
    )
    if is_local:
        headers["x-api-key"] = entry.api_key
    else:
        headers["Authorization"] = f"Bearer {entry.api_key}"
    return headers


# ---------------------------------------------------------------------------
# Background sync loop
# ---------------------------------------------------------------------------

async def _background_sync_loop(reg: Registry) -> None:
    while True:
        try:
            sleep_task = asyncio.create_task(asyncio.sleep(30))
            event_task = asyncio.create_task(reg._refresh_event.wait())
            done, pending = await asyncio.wait(
                [sleep_task, event_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            reg._refresh_event.clear()
            await discover_local()
            await reg.refresh()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Background sync error: %s", exc)


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

registry = Registry()
rate_tracker = RateLimitTracker()
failure_tracker = FailureTracker()
selection_log = SelectionLog()
_providers_lock = asyncio.Lock()
_weights_lock = asyncio.Lock()
_active_weights: Dict[str, Any] = _load_weights(WEIGHTS_PATH)

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Ensure providers.json exists
    _ensure_providers_file(PROVIDERS_PATH)
    # 2. Initial registry sync
    await registry.refresh()
    # 3. Local discovery (non-blocking, best-effort)
    discovery_task = asyncio.create_task(discover_local())
    # 4. Background sync loop
    sync_task = asyncio.create_task(_background_sync_loop(registry))
    yield
    # Shutdown — cancel background tasks cleanly
    sync_task.cancel()
    discovery_task.cancel()
    try:
        await asyncio.gather(sync_task, discovery_task, return_exceptions=True)
    except Exception:
        pass


app = FastAPI(lifespan=lifespan)


# ---------------------------------------------------------------------------
# Provider CRUD API
# ---------------------------------------------------------------------------

@app.get("/api/providers")
async def get_providers():
    return [asdict(p) for p in _load_providers(PROVIDERS_PATH)]


@app.post("/api/providers", status_code=201)
async def create_provider(request: Request):
    body = await request.json()
    async with _providers_lock:
        providers = _load_providers(PROVIDERS_PATH)
        new_p = Provider(
            id=str(uuid.uuid4()),
            name=body.get("name", ""),
            base_url=body.get("base_url", ""),
            api_key=body.get("api_key", ""),
            is_enabled=body.get("is_enabled", True),
            cached_models=body.get("cached_models", []),
        )
        providers.append(new_p)
        _atomic_write_providers(PROVIDERS_PATH, providers)
    registry.notify_changed()
    return asdict(new_p)


@app.put("/api/providers/{provider_id}")
async def update_provider(provider_id: str, request: Request):
    body = await request.json()
    async with _providers_lock:
        providers = _load_providers(PROVIDERS_PATH)
        for i, p in enumerate(providers):
            if p.id == provider_id:
                updated = Provider(
                    id=p.id,
                    name=body.get("name", p.name),
                    base_url=body.get("base_url", p.base_url),
                    api_key=body.get("api_key", p.api_key),
                    is_enabled=body.get("is_enabled", p.is_enabled),
                    cached_models=body.get("cached_models", p.cached_models),
                )
                providers[i] = updated
                _atomic_write_providers(PROVIDERS_PATH, providers)
                registry.notify_changed()
                return asdict(updated)
    raise HTTPException(status_code=404, detail="Provider not found")


@app.delete("/api/providers/{provider_id}", status_code=204)
async def delete_provider(provider_id: str):
    async with _providers_lock:
        providers = _load_providers(PROVIDERS_PATH)
        new_list = [p for p in providers if p.id != provider_id]
        if len(new_list) == len(providers):
            raise HTTPException(status_code=404, detail="Provider not found")
        _atomic_write_providers(PROVIDERS_PATH, new_list)
    registry.notify_changed()


# ---------------------------------------------------------------------------
# Status and models endpoints
# ---------------------------------------------------------------------------

@app.get("/api/endpoints")
async def api_endpoints():
    result = []
    flat = registry.get_flat()
    by_eid: Dict[str, int] = {}
    for e in flat:
        by_eid[e.endpoint_id] = by_eid.get(e.endpoint_id, 0) + 1
    providers = _load_providers(PROVIDERS_PATH)
    by_pid = {p.id: p for p in providers}
    for eid, meta in registry._endpoints.items():
        provider = by_pid.get(eid)
        result.append(
            {
                "id": eid,
                "name": meta.get("name", ""),
                "base_url": meta.get("base_url", ""),
                "is_managed": provider is not None,
                "is_enabled": provider.is_enabled if provider else True,
                "model_count": by_eid.get(eid, 0),
            }
        )
    return result


@app.get("/api/status")
async def api_status():
    active = len(registry._endpoints)
    return {
        "active_providers": active,
        "registered_models": len(registry.get_flat()),
        "recent_selections": [asdict(r) for r in selection_log.recent(20)],
    }


@app.get("/api/decisions/tail")
async def api_decisions_tail(n: int = 20):
    return [asdict(r) for r in selection_log.recent(n)]


@app.get("/api/decisions/live")
async def api_decisions_live():
    async def event_stream():
        seen: dict = {}
        while True:
            recent = selection_log.recent(10)
            for r in recent:
                key = r.timestamp + r.model_id
                if key not in seen:
                    seen[key] = True
                    yield f"data: {json.dumps(asdict(r))}\n\n"
            if len(seen) > 500:
                seen.clear()
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/weights")
async def api_get_weights():
    return _load_weights(WEIGHTS_PATH)


@app.put("/api/weights")
async def api_put_weights(request: Request):
    body = await request.json()
    async with _weights_lock:
        _save_weights(WEIGHTS_PATH, body)
        global _active_weights
        _active_weights = body
    return body


@app.get("/api/providers/models")
async def api_provider_models():
    return [
        {
            "model_id": e.model_id,
            "provider_id": e.endpoint_id,
            "provider": e.provider,
            "endpoint_name": e.endpoint_name,
            "context_window": e.context_window,
            "rpm": e.rpm,
            "tpm": e.tpm,
            "tpd": e.tpd,
        }
        for e in registry.get_flat()
    ]

# ---------------------------------------------------------------------------
# OpenAI-compatible endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "registered_models": len(registry.get_flat()),
        "active_providers": len(registry._endpoints),
    }


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "ModelRouter", "object": "model", "owned_by": "model-router", "type": "llm"}
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    requires_tools = bool(body.get("tools"))

    result = registry.select(messages, requires_tools=requires_tools, rate_tracker=rate_tracker)
    if not result:
        return JSONResponse(
            content={"error": {"message": "No model endpoints available"}},
            status_code=503,
        )

    selected, sc, task, top3 = result

    # Context-window guard: re-select with min_context if needed
    est = _estimate_tokens(messages)
    if selected.context_window and est > selected.context_window:
        result2 = registry.select(
            messages,
            requires_tools=requires_tools,
            rate_tracker=rate_tracker,
            min_context=est,
        )
        if not result2:
            return JSONResponse(
                content={"error": {"message": "No model with sufficient context window"}},
                status_code=503,
            )
        selected, sc, task, top3 = result2

    # Record to rate tracker and selection log
    rate_tracker.record(selected.model_id)
    selection_log.append(selected, task, sc, top3)

    # Build upstream request
    payload = {**body, "model": selected.model_id, "stream": True}
    headers = _build_auth_headers(selected)

    async def event_stream():
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=60.0, write=30.0, pool=5.0)
            ) as client:
                async with client.stream(
                    "POST", selected.chat_url, json=payload, headers=headers
                ) as resp:
                    if resp.status_code >= 400:
                        err_body = await resp.aread()
                        try:
                            err_json = json.loads(err_body)
                        except Exception:
                            err_json = {"error": {"message": err_body.decode(errors="replace")}}
                        yield f"data: {json.dumps(err_json)}\n\n".encode()
                        return
                    async for chunk in resp.aiter_bytes():
                        if chunk:
                            yield chunk
        except httpx.TimeoutException:
            logger.warning("TIMEOUT streaming from %s model=%s", selected.chat_url, selected.model_id)
            yield b"data: {\"error\": {\"message\": \"Upstream timeout\"}}\n\n"
        except Exception as exc:
            logger.error("Stream error from %s: %s", selected.chat_url, exc)
            yield f"data: {json.dumps({'error': {'message': str(exc)}})}\n\n".encode()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

# ---------------------------------------------------------------------------
# SPA static serving (must be after all API routes)
# ---------------------------------------------------------------------------

# Serve pre-built assets directory if it exists
_assets_dir = STATIC_DIR / "assets"
if _assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    # SPA not yet built — return friendly JSON
    return JSONResponse(
        {
            "message": "ModelRouter is running. The management UI has not been built yet.",
            "hint": "Run 'npm run build' inside the frontend/ directory, then copy dist/ to static/.",
            "api_docs": "/docs",
        },
        status_code=200,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "proxy:app",
        host="0.0.0.0",
        port=5000,
        log_level="info",
        reload=False,
    )
