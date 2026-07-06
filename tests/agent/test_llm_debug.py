"""Tests for LiteLLM JSONL debug logging."""

from __future__ import annotations

import json
from pathlib import Path

from minisweagent.models.utils import llm_debug


def test_log_litellm_exchange_writes_empty_choices(tmp_path, monkeypatch) -> None:
    """Comprueba que log litellm exchange writes empty choices."""
    monkeypatch.setenv("MSWEA_LLM_DEBUG_DIR", str(tmp_path))
    monkeypatch.setenv("MSWEA_LLM_DEBUG", "0")

    class _Usage:
        """Mock minimo de `usage` en respuestas LiteLLM."""

        prompt_tokens = 42
        completion_tokens = 0

    class _Response:
        """Mock de respuesta LiteLLM con `choices` vacias."""

        def model_dump(self) -> dict:
            """Devuelve el dict del objeto mock de respuesta."""
            return {
                "choices": [],
                "usage": {"prompt_tokens": 42, "completion_tokens": 0},
            }

    llm_debug.log_litellm_exchange(
        event="empty_choices",
        model_name="gemini/gemini-2.5-flash",
        call_index=1,
        retry_attempt=2,
        messages=[{"role": "user", "content": "hello"}],
        response=_Response(),
        model_kwargs={"temperature": 0.0},
        error="choices list is empty",
    )

    lines = (tmp_path / "llm_debug.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "empty_choices"
    assert record["prompt_tokens"] == 42
    assert record["choices_count"] == 0
    assert record["messages"][0]["content_preview"] == "hello"


def test_log_litellm_exchange_skips_completion_when_debug_off(tmp_path, monkeypatch) -> None:
    """Comprueba que log litellm exchange skips completion when debug off."""
    monkeypatch.setenv("MSWEA_LLM_DEBUG_DIR", str(tmp_path))
    monkeypatch.setenv("MSWEA_LLM_DEBUG", "0")

    class _Response:
        """Mock de respuesta LiteLLM con una choice."""

        def model_dump(self) -> dict:
            """Devuelve el dict del objeto mock de respuesta."""
            return {"choices": [{"message": {}}], "usage": {}}

    llm_debug.log_litellm_exchange(
        event="completion",
        model_name="test",
        call_index=1,
        retry_attempt=1,
        messages=[],
        response=_Response(),
    )

    assert not (tmp_path / "llm_debug.jsonl").exists()

