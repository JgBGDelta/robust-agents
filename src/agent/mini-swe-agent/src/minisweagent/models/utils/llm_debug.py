"""JSONL debug log for LiteLLM exchanges (opt-in via MSWEA_LLM_DEBUG_DIR)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def debug_dir() -> Path | None:
    raw = os.environ.get("MSWEA_LLM_DEBUG_DIR", "").strip()
    if not raw:
        return None
    return Path(raw)


def log_all_exchanges() -> bool:
    """When True, log every completion; otherwise only empty-choice failures."""
    return os.environ.get("MSWEA_LLM_DEBUG", "1").lower() in ("1", "true", "yes")


def include_full_messages() -> bool:
    return os.environ.get("MSWEA_LLM_DEBUG_FULL", "").lower() in ("1", "true", "yes")


def _content_len(content: Any) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(_content_len(part) for part in content)
    return len(str(content))


def _summarize_message(msg: dict[str, Any]) -> dict[str, Any]:
    content = msg.get("content")
    summary: dict[str, Any] = {
        "role": msg.get("role"),
        "content_chars": _content_len(content),
        "tool_call_id": msg.get("tool_call_id"),
        "tool_calls_count": len(msg.get("tool_calls") or []),
    }
    if include_full_messages():
        summary["content"] = content
    else:
        preview = content if isinstance(content, str) else json.dumps(content, default=str)
        summary["content_preview"] = (preview or "")[:800]
    return summary


def log_litellm_exchange(
    *,
    event: str,
    model_name: str,
    call_index: int,
    retry_attempt: int,
    messages: list[dict[str, Any]],
    response: Any | None = None,
    model_kwargs: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Append one JSON line to ``llm_debug.jsonl`` under ``MSWEA_LLM_DEBUG_DIR``."""
    directory = debug_dir()
    if directory is None:
        return
    if event == "completion" and not log_all_exchanges():
        return

    record: dict[str, Any] = {
        "ts": time.time(),
        "event": event,
        "model": model_name,
        "call_index": call_index,
        "retry_attempt": retry_attempt,
        "message_count": len(messages),
        "messages": [_summarize_message(m) for m in messages],
        "total_content_chars": sum(_content_len(m.get("content")) for m in messages),
    }
    if model_kwargs is not None:
        record["model_kwargs"] = model_kwargs
    if error is not None:
        record["error"] = error
    if response is not None:
        try:
            payload = response.model_dump()
        except AttributeError:
            payload = dict(response) if isinstance(response, dict) else {"raw": str(response)}
        record["response"] = payload
        usage = payload.get("usage") or {}
        record["prompt_tokens"] = usage.get("prompt_tokens")
        record["completion_tokens"] = usage.get("completion_tokens")
        record["choices_count"] = len(payload.get("choices") or [])
        for key in (
            "vertex_ai_safety_results",
            "vertex_ai_grounding_metadata",
            "vertex_ai_citation_metadata",
        ):
            if payload.get(key):
                record[key] = payload[key]

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "llm_debug.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
