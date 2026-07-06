"""Tests de `common.mini_config`."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from common.mini_config import build_litellm_model


def test_build_litellm_model_uses_experiment_model_id_over_yaml_default(
    monkeypatch: pytest.MonkeyPatch,
):
    """Comprueba que build litellm model uses experiment model id over yaml default."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    mini_config = {
        "model": {
            "model_name": "anthropic/claude-sonnet-4-5-20250929",
            "model_kwargs": {"drop_params": True},
        }
    }

    with patch("minisweagent.models.litellm_model.LitellmModel") as mock_model:
        build_litellm_model("gemini/gemini-2.5-flash", mini_config)

    mock_model.assert_called_once_with(
        model_name="gemini/gemini-2.5-flash",
        model_kwargs={"drop_params": True},
    )
