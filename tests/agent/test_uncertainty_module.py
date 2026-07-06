"""Tests unitarios del modulo de incertidumbre."""

from __future__ import annotations

import json
import math

import pytest

from agent.config import UncertaintyConfig
from agent.episode_state import EpisodeState, Profile
from agent.uncertainty_module import UncertaintyModule, UncertaintyResult


def _new_module(config: UncertaintyConfig | None = None) -> tuple[UncertaintyModule, EpisodeState]:
    """Auxiliar interno: new module."""
    episode_state = EpisodeState(profile=Profile(name="balanced"))
    module = UncertaintyModule(
        config=config or UncertaintyConfig(),
        episode_state=episode_state,
    )
    return module, episode_state


def _message(content: str = "", extra: dict | None = None) -> dict:
    """Auxiliar interno: message."""
    return {"role": "assistant", "content": content, "extra": extra or {}}


def test_evaluate_returns_uncertainty_result_with_full_components():
    """Comprueba que evaluate returns uncertainty result with full components."""
    module, _ = _new_module()
    message = _message(
        content="Voy a editar el archivo.",
        extra={"confidence": 0.9, "token_logprobs": [-0.1, -0.2, -0.05]},
    )

    result = module.evaluate(message=message, recent_history=[])

    assert isinstance(result, UncertaintyResult)
    assert result.level in {"low", "medium", "high"}
    assert 0.0 <= result.score <= 1.0
    assert set(result.components) == {"verbalized", "token_entropy", "hedging", "cycle", "failure_rate", "volatility"}
    assert result.evidence["logprobs_available"] is True
    assert result.cost_overhead == 0.0


def test_evaluate_degrades_cleanly_without_logprobs_or_confidence():
    """Comprueba que evaluate degrades cleanly without logprobs or confidence."""
    module, _ = _new_module()
    message = _message(content="Editing the file now.")

    result = module.evaluate(message=message, recent_history=[])
    omitted = result.evidence["omitted_components"]

    assert "verbalized:not_reported" in omitted
    assert "token_entropy:no_logprobs" in omitted
    assert "verbalized" not in result.components
    assert "token_entropy" not in result.components
    assert result.evidence["logprobs_available"] is False
    assert "hedging" in result.components


def test_hedging_signal_detects_uncertain_language_and_low_confidence():
    """Comprueba que hedging signal detects uncertain language and low confidence."""
    module, _ = _new_module()
    confident_msg = _message(content="I will run pytest.", extra={"confidence": 1.0})
    hedging_msg = _message(
        content="I think maybe I should probably not be sure, perhaps let me try.",
        extra={"confidence": 1.0},
    )

    low = module.evaluate(message=confident_msg, recent_history=[])
    high = module.evaluate(message=hedging_msg, recent_history=[])

    assert high.components["hedging"] > low.components["hedging"]
    assert high.score >= low.score


def test_cycle_detection_uses_normalized_actions():
    """Comprueba que cycle detection uses normalized actions."""
    module, _ = _new_module()
    action = {"command": "ls -la"}
    repeated_action_message = _message(extra={"actions": [action], "confidence": 0.7})
    history = [_message(extra={"actions": [{"command": " ls   -la "}]}) for _ in range(3)]

    result = module.evaluate(message=repeated_action_message, recent_history=history)

    assert result.components["cycle"] == pytest.approx(1.0)


def test_failure_rate_counts_failed_observations():
    """Comprueba que failure rate counts failed observations."""
    module, _ = _new_module()
    history = [
        {"role": "user", "content": "ok", "extra": {"returncode": 0}},
        {"role": "user", "content": "boom", "extra": {"returncode": 1}},
        {"role": "user", "content": "explode", "extra": {"exception_info": "Traceback"}},
        {"role": "assistant", "content": "ignored"},
    ]
    message = _message(content="next step", extra={"confidence": 0.5})

    result = module.evaluate(message=message, recent_history=history)

    assert result.components["failure_rate"] == pytest.approx(2 / 3)


def test_volatility_detects_surface_signature_reverts():
    """Comprueba que volatility detects surface signature reverts."""
    module, episode_state = _new_module()
    episode_state.signals_history = [
        {"structural_risk": {"diff_summary": {"surface_signature": "AAA"}}},
        {"structural_risk": {"diff_summary": {"surface_signature": "BBB"}}},
        {"structural_risk": {"diff_summary": {"surface_signature": "AAA"}}},
        {"structural_risk": {"diff_summary": {"surface_signature": "CCC"}}},
        {"structural_risk": {"diff_summary": {"surface_signature": "AAA"}}},
    ]
    message = _message(content="next", extra={"confidence": 0.5})

    result = module.evaluate(message=message, recent_history=[])

    assert result.components["volatility"] > 0.0


def test_module_returns_neutral_result_when_internal_signal_raises():
    """Comprueba que module returns neutral result when internal signal raises."""
    config = UncertaintyConfig(weights={"verbalized": 1.0})
    module, episode_state = _new_module(config=config)
    # Forzamos fallo interno reemplazando el detector de hedging por uno roto.
    module._single_shot.verbalized = lambda _msg: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[attr-defined]
    message = _message(content="cualquier cosa")

    result = module.evaluate(message=message, recent_history=[])

    assert result.score == 0.5
    assert result.level == "medium"
    assert "error" in result.evidence
    assert len(episode_state.errors) == 1
    assert episode_state.errors[0]["component"] == "uncertainty"


def test_uncertainty_result_is_json_serializable():
    """Comprueba que uncertainty result es json serializable."""
    result = UncertaintyResult(
        score=0.42,
        level="medium",
        components={"verbalized": 0.3, "hedging": 0.5, "cycle": 0.0, "failure_rate": 0.0, "volatility": 0.0},
        evidence={"logprobs_available": False, "omitted_components": ["token_entropy:no_logprobs"]},
        cost_overhead=0.0,
    )
    payload = result.to_dict()

    assert payload["score"] == 0.42
    assert payload["level"] == "medium"
    assert payload["components"]["hedging"] == 0.5
    assert payload["evidence"]["omitted_components"] == ["token_entropy:no_logprobs"]
    json.dumps(payload)


def test_verbalized_reads_confidence_from_extra_actions():
    """Regresion: verbalized debe extraer confidence de extra.actions[0] (JsonBashModel)."""
    module, _ = _new_module()
    # Formato real: _parse_actions de JsonBashModel guarda confidence en extra.actions[0]
    message = {
        "role": "assistant",
        "content": '{"command": "ls -la", "confidence": 0.8}',
        "extra": {
            "actions": [{"command": "ls -la", "confidence": 0.8, "tool_call_id": "call_json_bash"}]
        },
    }

    result = module.evaluate(message=message, recent_history=[])

    assert "verbalized" in result.components
    assert result.components["verbalized"] == pytest.approx(0.2)  # 1.0 - 0.8
    omitted = result.evidence["omitted_components"]
    assert not any(o.startswith("verbalized") for o in omitted)


def test_verbalized_absent_when_no_confidence_in_actions():
    """Sin campo confidence en extra.actions, verbalized sigue siendo None."""
    module, _ = _new_module()
    message = {
        "role": "assistant",
        "content": '{"command": "ls -la"}',
        "extra": {
            "actions": [{"command": "ls -la", "confidence": None, "tool_call_id": "call_json_bash"}]
        },
    }

    result = module.evaluate(message=message, recent_history=[])

    assert "verbalized" not in result.components
    assert any(o.startswith("verbalized") for o in result.evidence["omitted_components"])


def test_reset_is_safe_noop():
    """Comprueba que reset is safe noop."""
    module, _ = _new_module()
    assert module.reset() is None


def test_token_entropy_uses_logprobs_and_increases_with_dispersion():
    """Comprueba que token entropy uses logprobs and increases with dispersion."""
    module, _ = _new_module()
    low_entropy = module.evaluate(
        message=_message(extra={"token_logprobs": [-0.01, -0.02]}),
        recent_history=[],
    )
    high_entropy = module.evaluate(
        message=_message(extra={"token_logprobs": [-3.0, -4.0]}),
        recent_history=[],
    )

    assert high_entropy.components["token_entropy"] > low_entropy.components["token_entropy"]
    assert not math.isnan(high_entropy.components["token_entropy"])
