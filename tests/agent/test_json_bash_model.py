"""Tests para las funciones de parseo de JsonBashModel.

Cubre _extract_command y _extract_confidence con los casos relevantes:
JSON valido con y sin confidence, confidence fuera de rango, JSON malformado
con fallback regex.
"""

from __future__ import annotations

import pytest

from agent.models.json_bash_model import _extract_command, _extract_confidence


# ---------------------------------------------------------------------------
# _extract_command


@pytest.mark.parametrize(("content", "expected"), [
    ('{"command": "ls -la"}', "ls -la"),
    ('{"command": "ls -la", "confidence": 0.8}', "ls -la"),
    ('```json\n{"command": "echo hello"}\n```', "echo hello"),
    ("", None),
    ('{"not_command": "foo"}', None),
    ("plain text without json", None),
])
def test_extract_command(content: str, expected: str | None):
    """Comprueba que extract command."""
    assert _extract_command(content) == expected


# ---------------------------------------------------------------------------
# _extract_confidence


@pytest.mark.parametrize(("content", "expected"), [
    ('{"command": "ls", "confidence": 0.8}', 0.8),
    ('{"command": "ls", "confidence": 1.0}', 1.0),
    ('{"command": "ls", "confidence": 0.0}', 0.0),
    ('{"command": "ls"}', None),
    ('{"command": "ls", "confidence": 1.5}', None),   # fuera de rango
    ('{"command": "ls", "confidence": -0.1}', None),  # fuera de rango
    ('```json\n{"command": "x", "confidence": 0.5}\n```', 0.5),
    ("", None),
    ("not json at all", None),
])
def test_extract_confidence(content: str, expected: float | None):
    """Comprueba que extract confidence."""
    result = _extract_confidence(content)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)
