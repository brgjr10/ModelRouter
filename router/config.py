"""
router/config.py — providers.json and weights.json persistence helpers.

Responsibilities:
  - _ensure_providers_file(path): creates empty [] if path is missing.
  - _load_providers(path): reads providers.json → List[Provider];
      on missing file creates [] and returns [];
      on malformed JSON logs the error and returns [] without overwriting.
  - _atomic_write_providers(path, providers): serialises to a .json.tmp
      sibling then os.replace() to the target path atomically.
  - _load_weights / _save_weights: same pattern for weights.json.
"""

import json
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

from router.models import Provider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
PROVIDERS_PATH: Path = PROJECT_ROOT / "providers.json"
WEIGHTS_PATH: Path = PROJECT_ROOT / "weights.json"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_providers_file(path: Path) -> None:
    if not path.exists():
        logger.info("providers.json not found at %s — creating empty file.", path)
        _atomic_write_providers(path, [])


def _load_providers(path: Path) -> List[Provider]:
    if not path.exists():
        _ensure_providers_file(path)
        return []

    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(
            "providers.json at %s contains malformed JSON — using empty list. "
            "Error: %s",
            path,
            exc,
        )
        return []

    providers: List[Provider] = []
    for d in data:
        try:
            providers.append(Provider(**d))
        except TypeError as exc:
            logger.warning("Skipping malformed provider entry %r: %s", d, exc)

    return providers


def _atomic_write_providers(path: Path, providers: List[Provider]) -> None:
    data = [asdict(p) for p in providers]
    _atomic_write_json(path, data)


def _ensure_weights_file(path: Path) -> None:
    if not path.exists():
        logger.info("weights.json not found at %s — creating default file.", path)
        _save_weights(path, _DEFAULT_WEIGHTS)


_DEFAULT_WEIGHTS: Dict[str, Any] = {
    "global": {
        "task_match": 0.35,
        "reasoning": 0.25,
        "context_fit": 0.10,
        "tool_use": 0.15,
        "latency": 0.10,
        "cost": 0.15,
    },
    "task_overrides": {},
}


def _load_weights(path: Path) -> Dict[str, Any]:
    if not path.exists():
        _ensure_weights_file(path)
        return dict(_DEFAULT_WEIGHTS)

    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("weights.json malformed — using defaults. Error: %s", exc)
        return dict(_DEFAULT_WEIGHTS)

    return data if isinstance(data, dict) else dict(_DEFAULT_WEIGHTS)


def _save_weights(path: Path, weights: Dict[str, Any]) -> None:
    _atomic_write_json(path, weights)


# ---------------------------------------------------------------------------
# Atomic JSON writer (used by both providers and weights)
# ---------------------------------------------------------------------------

def _atomic_write_json(path: Path, data: Any) -> None:
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp_path), str(path))
