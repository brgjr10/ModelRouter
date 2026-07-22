"""
Data models for the Model Router.

Defines the core dataclasses used throughout the router:
- Provider: a configured upstream inference server entry
- ModelEntry: a single model ID paired with the endpoint that serves it
- SelectionRecord: a record of a routing decision stored in the SelectionLog
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Provider:
    """
    A configured upstream inference server entry, persisted in providers.json.

    Requirements: 2.5
    """
    id: str                          # UUID v4 string
    name: str                        # display label
    base_url: str                    # e.g. "http://localhost:11434"
    api_key: str                     # empty string if not required
    is_enabled: bool
    cached_models: List[str] = field(default_factory=list)  # model IDs known for this endpoint


@dataclass
class ModelEntry:
    """
    A single model ID paired with the endpoint that serves it.
    Held in the in-memory Registry and used for routing decisions.

    Requirements: 10.1
    """
    endpoint_id: str
    endpoint_name: str
    model_id: str
    provider: str
    chat_url: str
    api_key: str
    model_type: str
    context_window: Optional[int] = None  # max input+output tokens
    rpm: Optional[int] = None             # requests per minute
    tpm: Optional[int] = None             # tokens per minute
    tpd: Optional[int] = None             # tokens per day


@dataclass
class CandidateTrace:
    """One candidate in the scoring trace."""
    model_id: str
    endpoint_name: str
    score: float


@dataclass
class SelectionRecord:
    """
    A record of a routing decision, stored in the in-memory SelectionLog
    circular buffer capped at 100 entries.

    Requirements: 9.1
    """
    model_id: str
    endpoint_name: str
    task_type: str
    reasoning_depth: str
    score: float
    timestamp: str  # ISO 8601 UTC, e.g. "2024-01-15T12:34:56.789Z"
    task: Optional[Dict[str, Any]] = None
    top_candidates: Optional[List[Dict[str, Any]]] = None
