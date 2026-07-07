"""Tests unitarios del modulo controlador (fase 4b).

Cubren la interfaz publica (`ControllerModule.decide`, `BudgetGuard`)
y los contratos de la spec `docs/fase2/agente/modulo_controlador.md`:
matriz por celda y perfil, correctores (`BudgetGuard`,
`StabilityMonitor`), politica ante entradas degradadas, fallback de
validacion y registro en `EpisodeState.decisions_history`.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agent.config import ControllerConfig
from agent.episode_state import EpisodeState, Profile
from agent.controller_module import BudgetGuard, ControllerDecision, ControllerModule
from agent.structural_metrics_module import StructuralRiskResult
from agent.uncertainty_module import UncertaintyResult


# --- Fabricas auxiliares de entradas sinteticas --------------------------------------


def _new_controller(
    profile: str = "balanced",
    *,
    config: ControllerConfig | None = None,
) -> tuple[ControllerModule, EpisodeState]:
    """Instancia un controlador con su `EpisodeState` recien creado."""
    episode_state = EpisodeState(profile=Profile(name=profile))
    module = ControllerModule(config=config or ControllerConfig(), episode_state=episode_state)
    return module, episode_state


def _uncertainty(level: str, *, score: float | None = None, error: str | None = None) -> UncertaintyResult:
    """Construye un `UncertaintyResult` con score por defecto coherente con el nivel."""
    default_score = {"low": 0.1, "medium": 0.5, "high": 0.9}[level]
    return UncertaintyResult(
        score=score if score is not None else default_score,
        level=level,  # type: ignore[arg-type]
        components={"verbalized": 0.0, "token_entropy": 0.0, "hedging": 0.0,
                    "cycle": 0.0, "failure_rate": 0.0, "volatility": 0.0},
        evidence={"error": error} if error else {},
    )


def _risk(level: str, *, score: float | None = None, surface: str = "sig-default") -> StructuralRiskResult:
    """Construye un `StructuralRiskResult` con ``surface_signature`` controlable.

    Tanto ``surface_signature`` (incremental) como
    ``cumulative_surface_signature`` se inicializan al mismo valor para que
    los tests de estabilidad funcionen correctamente con la firma acumulada
    que usa el ``StabilityMonitor``.
    """
    default_score = {"low": 0.1, "medium": 0.5, "high": 0.9}[level]
    return StructuralRiskResult(
        score=score if score is not None else default_score,
        level=level,  # type: ignore[arg-type]
        metrics={},
        diff_summary={
            "surface_signature": surface,
            "cumulative_surface_signature": surface,
        },
    )


def _config_full_validation() -> ControllerConfig:
    """Config con todas las capacidades de validacion habilitadas."""
    return ControllerConfig(tests_validation_enabled=True, self_review_enabled=True)


# --- Matriz de decision (politica base) ----------------------------------------------


@pytest.mark.parametrize(
    ("u_level", "r_level", "expected_action"),
    [
        ("low", "low", "PROCEED"),
        ("low", "medium", "PROCEED"),
        ("low", "high", "RUN_VALIDATION"),
        ("medium", "low", "PROCEED"),
        ("medium", "medium", "INJECT_FEEDBACK"),
        ("medium", "high", "RUN_VALIDATION"),
        ("high", "low", "INJECT_FEEDBACK"),
        ("high", "medium", "RUN_VALIDATION"),
    ],
)
def test_policy_matrix_balanced_profile(u_level: str, r_level: str, expected_action: str):
    """Comprueba que policy matrix balanced profile."""
    module, _ = _new_controller("balanced", config=_config_full_validation())

    decision = module.decide(_uncertainty(u_level), _risk(r_level))

    assert decision.action == expected_action


def test_high_high_balanced_finalizes_abort():
    """Comprueba que high high balanced finalizes abort."""
    module, _ = _new_controller("balanced", config=_config_full_validation())
    decision = module.decide(_uncertainty("high"), _risk("high"))

    assert decision.action == "FINALIZE"
    assert decision.subtype == "abort"


def test_high_high_strict_finalizes_abort():
    """Comprueba que high high strict finalizes abort."""
    module, _ = _new_controller("strict", config=_config_full_validation())
    decision = module.decide(_uncertainty("high"), _risk("high"))

    assert decision.action == "FINALIZE"
    assert decision.subtype == "abort"


def test_high_high_permissive_runs_validation():
    """Comprueba que high high permissive runs validation."""
    module, _ = _new_controller("permissive", config=_config_full_validation())
    decision = module.decide(_uncertainty("high"), _risk("high"))

    # En permissive la celda (high, high) se reasigna a RUN_VALIDATION, que
    # con `validation_per_profile.permissive == "none"` degenera a PROCEED
    # y se registra en `overrides`.
    assert decision.action == "PROCEED"
    assert decision.overrides.get("validation_degenerated")


# --- Subtipo de validacion por perfil y cadena de fallback ----------------------------


def test_validation_subtype_strict_combined_when_supported():
    """Comprueba que validation subtype strict combined when supported."""
    config = ControllerConfig(tests_validation_enabled=True, self_review_enabled=True)
    module, _ = _new_controller("strict", config=config)

    decision = module.decide(_uncertainty("medium"), _risk("high"))

    assert decision.action == "RUN_VALIDATION"
    assert decision.subtype == "combined"
    assert "validation_fallback" not in decision.overrides
    assert "directive" in decision.payload
    assert "Valida tu cambio actual" in decision.payload["directive"]
    assert decision.payload["forbidden_test_ids"] == []
    assert decision.payload["prompt"]


def test_validation_subtype_balanced_tests_when_supported():
    """Comprueba que validation subtype balanced tests when supported."""
    config = ControllerConfig(tests_validation_enabled=True, self_review_enabled=False)
    module, _ = _new_controller("balanced", config=config)

    decision = module.decide(_uncertainty("medium"), _risk("high"))

    assert decision.action == "RUN_VALIDATION"
    assert decision.subtype == "tests"
    assert "directive" in decision.payload
    assert "Valida tu cambio actual" in decision.payload["directive"]


def test_validation_directive_includes_forbidden_block_when_configured():
    """Comprueba que validation directive includes forbidden block when configured."""
    config = ControllerConfig(
        tests_validation_enabled=True,
        forbidden_test_ids=["tests/foo::test_a", "tests/foo::test_b"],
    )
    module, _ = _new_controller("balanced", config=config)

    decision = module.decide(_uncertainty("medium"), _risk("high"))

    assert decision.subtype == "tests"
    directive = decision.payload["directive"]
    assert "Restriccion importante" in directive
    assert "tests/foo::test_a" in directive
    assert "tests/foo::test_b" in directive
    assert decision.payload["forbidden_test_ids"] == [
        "tests/foo::test_a",
        "tests/foo::test_b",
    ]


def test_validation_combined_falls_back_to_tests_when_self_review_disabled():
    """Comprueba que validation combined falls back to tests when self review disabled."""
    config = ControllerConfig(tests_validation_enabled=True, self_review_enabled=False)
    module, _ = _new_controller("strict", config=config)

    decision = module.decide(_uncertainty("medium"), _risk("high"))

    assert decision.action == "RUN_VALIDATION"
    assert decision.subtype == "tests"
    assert decision.overrides["validation_fallback"] == "combined -> tests"


def test_validation_tests_degenerates_to_proceed_without_capacity():
    """Comprueba que validation tests degenerates to proceed without capacity."""
    # Perfil balanced (validation=tests) pero sin capacidad ni self-review.
    config = ControllerConfig(tests_validation_enabled=False, self_review_enabled=False)
    module, _ = _new_controller("balanced", config=config)

    decision = module.decide(_uncertainty("medium"), _risk("high"))

    assert decision.action == "PROCEED"
    assert decision.overrides["validation_fallback"] == "tests -> none"
    assert decision.overrides["validation_degenerated"] == "RUN_VALIDATION -> PROCEED"


# --- Corrector de presupuesto --------------------------------------------------------


def test_budget_corrector_clamps_run_validation_when_insufficient_budget():
    """Comprueba que budget corrector clamps run validation when insufficient budget."""
    config = ControllerConfig(tests_validation_enabled=True, self_review_enabled=True)
    module, episode_state = _new_controller("strict", config=config)
    episode_state.budget.cost_limit = 0.04
    episode_state.budget.cost_used = 0.04  # ya agotado para el subtipo combined (0.05)

    decision = module.decide(_uncertainty("medium"), _risk("high"))

    assert decision.action == "FINALIZE"
    assert decision.subtype == "abort"
    assert "budget_clamp" in decision.overrides


def test_budget_corrector_noop_when_budget_unlimited():
    """Comprueba que budget corrector noop when budget unlimited."""
    config = ControllerConfig(tests_validation_enabled=True, self_review_enabled=True)
    module, episode_state = _new_controller("strict", config=config)
    episode_state.budget.cost_limit = 0.0  # sin limite

    decision = module.decide(_uncertainty("medium"), _risk("high"))

    assert decision.action == "RUN_VALIDATION"
    assert "budget_clamp" not in decision.overrides


# --- Corrector de estabilidad --------------------------------------------------------


def _seed_stable_history(state: EpisodeState, surface: str, count: int) -> None:
    """Inyecta ``count`` señales de riesgo bajas con la misma firma estable.

    Rellena tanto ``surface_signature`` (incremental) como
    ``cumulative_surface_signature`` (acumulada) para que el
    ``StabilityMonitor`` (que usa la acumulada) pueda detectar estabilidad.
    """
    for _ in range(count):
        state.signals_history.append(
            {
                "structural_risk": {
                    "diff_summary": {
                        "surface_signature": surface,
                        "cumulative_surface_signature": surface,
                    },
                    "score": 0.1,
                    "level": "low",
                }
            }
        )


def test_stability_corrector_promotes_low_low_to_finalize_submit():
    """Comprueba que stability corrector promotes low low to finalize submit."""
    module, state = _new_controller("balanced")
    _seed_stable_history(state, "SIG-STABLE", count=10)

    decision = module.decide(_uncertainty("low", score=0.1), _risk("low", score=0.1, surface="SIG-STABLE"))

    assert decision.action == "FINALIZE"
    assert decision.subtype == "submit"
    assert decision.overrides["stability_promotion"]


def test_stability_corrector_does_not_trigger_with_short_window():
    """Comprueba que stability corrector does not trigger with short window."""
    module, state = _new_controller("balanced")
    _seed_stable_history(state, "SIG-STABLE", count=9)  # ventana 10 por defecto

    decision = module.decide(_uncertainty("low", score=0.1), _risk("low", score=0.1, surface="SIG-STABLE"))

    assert decision.action == "PROCEED"


def test_stability_corrector_does_not_trigger_with_changing_signature():
    """Comprueba que stability corrector does not trigger with changing signature."""
    module, state = _new_controller("balanced")
    state.signals_history.extend(
        [
            {
                "structural_risk": {
                    "diff_summary": {
                        "surface_signature": f"SIG-{i}",
                        "cumulative_surface_signature": f"SIG-{i}",
                    }
                }
            }
            for i in range(3)
        ]
    )

    decision = module.decide(_uncertainty("low", score=0.1), _risk("low", score=0.1, surface="SIG-3"))

    assert decision.action == "PROCEED"


# --- Entradas degradadas -------------------------------------------------------------


@pytest.mark.parametrize(
    ("profile", "expected_action"),
    [("permissive", "PROCEED"), ("balanced", "INJECT_FEEDBACK")],
)
def test_degraded_uncertainty_uses_profile_conservative_cell(profile: str, expected_action: str):
    """Comprueba que degraded uncertainty uses profile conservative cell."""
    module, _ = _new_controller(profile, config=_config_full_validation())
    degraded = _uncertainty("medium", error="model_unavailable")

    decision = module.decide(degraded, _risk("medium"))

    assert decision.action == expected_action
    assert decision.overrides["degraded_input"] == "uncertainty_neutral"


def test_degraded_uncertainty_strict_runs_validation():
    """Comprueba que degraded uncertainty strict runs validation."""
    module, _ = _new_controller("strict", config=_config_full_validation())
    degraded = _uncertainty("medium", error="model_unavailable")

    decision = module.decide(degraded, _risk("medium"))

    assert decision.action == "RUN_VALIDATION"
    assert decision.subtype == "combined"


# --- Registro y contrato -------------------------------------------------------------


def test_decide_records_decision_in_episode_state():
    """Comprueba que decide records decision in episode state."""
    module, state = _new_controller("balanced")
    module.decide(_uncertainty("low"), _risk("low"))
    module.decide(_uncertainty("medium"), _risk("medium"))

    assert len(state.decisions_history) == 2
    assert all("decision" in entry and "step_index" in entry for entry in state.decisions_history)


def test_controller_decision_is_json_serializable():
    """Comprueba que controller decision is json serializable."""
    module, _ = _new_controller("balanced")
    decision = module.decide(_uncertainty("medium"), _risk("medium"))

    payload = decision.to_dict()
    assert set(payload) == {"action", "subtype", "rationale", "policy_snapshot", "overrides", "payload"}
    json.dumps(payload)  # no debe lanzar


def test_inject_feedback_payload_contains_rendered_text():
    """Comprueba que inject feedback payload contains rendered text."""
    module, _ = _new_controller("balanced")
    decision = module.decide(_uncertainty("medium"), _risk("medium"))

    assert decision.action == "INJECT_FEEDBACK"
    text = decision.payload["feedback_text"]
    assert "medium" in text


def test_policy_snapshot_includes_profile_and_levels():
    """Comprueba que policy snapshot includes profile and levels."""
    module, _ = _new_controller("strict", config=_config_full_validation())
    decision = module.decide(_uncertainty("high", score=0.85), _risk("medium", score=0.5))

    snapshot = decision.policy_snapshot
    assert snapshot["profile"] == "strict"
    assert snapshot["uncertainty_level"] == "high"
    assert snapshot["risk_level"] == "medium"
    assert "policy_matrix" in snapshot


# --- Manejo de excepciones internas --------------------------------------------------


def test_controller_internal_exception_finalizes_abort(monkeypatch: pytest.MonkeyPatch):
    """Comprueba que controller internal exception finalizes abort."""
    module, state = _new_controller("balanced")

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        """Auxiliar interno: boom."""
        raise RuntimeError("policy explotada")

    monkeypatch.setattr(module._policy, "decide_base", _boom)

    decision = module.decide(_uncertainty("low"), _risk("low"))

    assert decision.action == "FINALIZE"
    assert decision.subtype == "abort"
    assert "controller_error" in decision.overrides
    assert state.errors and state.errors[-1]["component"] == "controller"


# --- BudgetGuard como servicio publico -----------------------------------------------


def test_budget_guard_can_afford_with_unlimited_budget():
    """Comprueba que budget guard can afford with unlimited budget."""
    state = EpisodeState(profile=Profile(name="balanced"))
    guard = BudgetGuard(state)

    assert guard.can_afford("validation.tests", expected_cost=999.0) is True


def test_budget_guard_can_afford_respects_limit():
    """Comprueba que budget guard can afford respects limit."""
    state = EpisodeState(profile=Profile(name="balanced"))
    state.budget.cost_limit = 1.0
    state.budget.cost_used = 0.7
    guard = BudgetGuard(state)

    assert guard.can_afford("agent.query", expected_cost=0.2) is True
    assert guard.can_afford("agent.query", expected_cost=0.4) is False


def test_budget_guard_notify_cost_updates_state_and_attribution():
    """Comprueba que budget guard notify cost updates state and attribution."""
    state = EpisodeState(profile=Profile(name="balanced"))
    guard = BudgetGuard(state)

    guard.notify_cost(0.1, attribution="agent.query")
    guard.notify_cost(0.05, attribution="validation.self_review")
    guard.notify_cost(0.02, attribution="agent.query")

    assert state.budget.cost_used == pytest.approx(0.17)
    assert state.budget.cost_by_attribution["agent.query"] == pytest.approx(0.12)
    assert state.budget.cost_by_attribution["validation.self_review"] == pytest.approx(0.05)


def test_budget_guard_notify_cost_ignores_non_positive_amounts():
    """Comprueba que budget guard notify cost ignores non positive amounts."""
    state = EpisodeState(profile=Profile(name="balanced"))
    guard = BudgetGuard(state)

    guard.notify_cost(0.0, attribution="agent.query")
    guard.notify_cost(-1.0, attribution="agent.query")

    assert state.budget.cost_used == 0.0
    assert state.budget.cost_by_attribution == {}


# --- Acceso al servicio desde el ControllerModule ---------------------------------


def test_controller_exposes_budget_guard_as_public_attribute():
    """Comprueba que controller exposes budget guard as public attribute."""
    module, state = _new_controller("balanced")

    assert isinstance(module.budget_guard, BudgetGuard)
    module.budget_guard.notify_cost(0.1, attribution="agent.query")
    assert state.budget.cost_used == pytest.approx(0.1)
