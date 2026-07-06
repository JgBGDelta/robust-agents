"""Tests de `TrajectoryReader`: agregacion de `trajectory.traj.json`.

Fixtures modeladas sobre el formato real observado en
`runs/full-lite-120inst-6agents/instances/**/robust_*/**/trajectory.traj.json`:
- `signals_history` alterna entradas `uncertainty` y `structural_risk` (nunca
  ambas en la misma entrada), etiquetadas por `step_index`.
- `decisions_history` envuelve la decision en `{"decision": {...}, "step_index": N}`.
- `robust_agent.steps` trae `uncertainty`/`structural_risk`/`decision`/
  `validation_outcome` como hermanos directos.
"""

from __future__ import annotations

from analysis.consolidation_module.trajectory_reader import TrajectoryReader


def _uncertainty_entry(step_index: int, score: float, level: str = "low", **components) -> dict:
    """Auxiliar interno: uncertainty entry."""
    return {
        "step_index": step_index,
        "uncertainty": {
            "score": score,
            "level": level,
            "components": {
                "cycle": components.get("cycle", 0.0),
                "failure_rate": components.get("failure_rate", 0.0),
                "hedging": components.get("hedging", 0.0),
                "volatility": components.get("volatility", 0.0),
            },
            "evidence": {"omitted_components": components.get("omitted", [])},
        },
    }


def _structural_risk_entry(step_index: int, score: float, level: str = "low", **kw) -> dict:
    """Auxiliar interno: structural risk entry."""
    return {
        "step_index": step_index,
        "structural_risk": {
            "score": score,
            "level": level,
            "metrics": {
                "files_changed_ratio": kw.get("files_changed_ratio", 0.1),
                "hunks_per_file": kw.get("hunks_per_file", 1.0),
                "net_lines": kw.get("net_lines", 5),
            },
            "diff_summary": {
                "cumulative_lines_changed": kw.get("cumulative_lines_changed", 10),
                "files_touched": kw.get("files_touched", ["a.py"]),
            },
        },
    }


def _decision_entry(step_index: int, action: str, subtype: str | None = None, **snapshot) -> dict:
    """Auxiliar interno: decision entry."""
    return {
        "step_index": step_index,
        "decision": {
            "action": action,
            "subtype": subtype,
            "policy_snapshot": {
                "uncertainty_level": snapshot.get("uncertainty_level", "low"),
                "risk_level": snapshot.get("risk_level", "low"),
            },
        },
    }


def _message(role: str, prompt_tokens=None, completion_tokens=None, total_tokens=None) -> dict:
    """Auxiliar interno: message."""
    msg = {"role": role, "content": "..."}
    if prompt_tokens is not None:
        msg["extra"] = {
            "response": {
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                }
            }
        }
    return msg


# ---------------------------------------------------------------------------
# Tokens / api_calls (disponible para cualquier agente con traza)
# ---------------------------------------------------------------------------


def test_read_sums_tokens_from_assistant_messages_only():
    """Comprueba que read sums tokens from assistant messages only."""
    data = {
        "messages": [
            _message("system"),
            _message("user"),
            _message("assistant", prompt_tokens=100, completion_tokens=20, total_tokens=120),
            _message("user"),
            _message("assistant", prompt_tokens=150, completion_tokens=30, total_tokens=180),
        ]
    }
    metrics = TrajectoryReader().read(data)
    assert metrics.prompt_tokens == 250
    assert metrics.completion_tokens == 50
    assert metrics.total_tokens == 300


def test_read_tokens_none_when_no_usage_present():
    """Comprueba que read tokens none when no usage present."""
    data = {"messages": [_message("system"), _message("assistant")]}
    metrics = TrajectoryReader().read(data)
    assert metrics.prompt_tokens is None
    assert metrics.completion_tokens is None
    assert metrics.total_tokens is None


def test_read_api_calls_from_model_stats():
    """Comprueba que read api calls from model stats."""
    data = {"info": {"model_stats": {"api_calls": 7}}}
    metrics = TrajectoryReader().read(data)
    assert metrics.api_calls == 7


def test_read_without_robust_agent_block_leaves_robust_fields_none():
    """Comprueba que read without robust agent block leaves robust fields none."""
    data = {"messages": [], "info": {"model_stats": {"api_calls": 3}}}
    metrics = TrajectoryReader().read(data)
    assert metrics.has_robust_agent is False
    assert metrics.uncertainty_mean is None
    assert metrics.structural_risk_mean is None
    assert metrics.controller_intervention_count is None


# ---------------------------------------------------------------------------
# Incertidumbre
# ---------------------------------------------------------------------------


def test_read_uncertainty_aggregates_mean_max_final_std():
    """Comprueba que read uncertainty aggregates mean max final std."""
    signals = [
        _uncertainty_entry(1, 0.1),
        _structural_risk_entry(1, 0.0),
        _uncertainty_entry(2, 0.3, level="medium"),
        _structural_risk_entry(2, 0.0),
        _uncertainty_entry(3, 0.5, level="high"),
    ]
    data = {"robust_agent": {"episode_state_final": {"signals_history": signals}}}
    metrics = TrajectoryReader().read(data)

    assert metrics.has_robust_agent is True
    assert metrics.uncertainty_mean == (0.1 + 0.3 + 0.5) / 3
    assert metrics.uncertainty_max == 0.5
    assert metrics.uncertainty_final == 0.5
    assert metrics.uncertainty_high_ratio == 1 / 3


def test_read_uncertainty_component_means():
    """Comprueba que read uncertainty component means."""
    signals = [
        _uncertainty_entry(1, 0.1, cycle=0.2, failure_rate=0.1, hedging=0.0, volatility=0.3),
        _uncertainty_entry(2, 0.2, cycle=0.4, failure_rate=0.3, hedging=0.2, volatility=0.1),
    ]
    data = {"robust_agent": {"episode_state_final": {"signals_history": signals}}}
    metrics = TrajectoryReader().read(data)

    assert metrics.cycle_mean == (0.2 + 0.4) / 2
    assert metrics.failure_rate_mean == (0.1 + 0.3) / 2
    assert metrics.hedging_mean == (0.0 + 0.2) / 2
    assert metrics.volatility_mean == (0.3 + 0.1) / 2


def test_read_uncertainty_missing_components_uses_last_entry():
    """Comprueba que read uncertainty missing components uses last entry."""
    signals = [
        _uncertainty_entry(1, 0.1, omitted=["a", "b"]),
        _uncertainty_entry(2, 0.2, omitted=["c"]),
    ]
    data = {"robust_agent": {"episode_state_final": {"signals_history": signals}}}
    metrics = TrajectoryReader().read(data)
    assert metrics.uncertainty_missing_components == 1


# ---------------------------------------------------------------------------
# Riesgo estructural
# ---------------------------------------------------------------------------


def test_read_structural_risk_ignores_empty_entries():
    """Comprueba que read structural risk ignores empty entries."""
    signals = [
        _uncertainty_entry(1, 0.0),
        _structural_risk_entry(1, 0.1, files_changed_ratio=0.2, net_lines=3),
        _uncertainty_entry(2, 0.0),
        # Ultimo paso: structural_risk vacio (sin cambios incrementales), no debe contar.
        {"step_index": 2, "structural_risk": {}},
    ]
    data = {"robust_agent": {"episode_state_final": {"signals_history": signals}}}
    metrics = TrajectoryReader().read(data)

    assert metrics.structural_risk_mean == 0.1
    assert metrics.structural_risk_final == 0.1
    assert metrics.files_changed_ratio == 0.2
    assert metrics.net_lines == 3


def test_read_structural_risk_final_fields_from_last_non_empty_entry():
    """Comprueba que read structural risk final fields from last non empty entry."""
    signals = [
        _structural_risk_entry(
            1, 0.1, files_changed_ratio=0.1, hunks_per_file=1.0, net_lines=2,
            cumulative_lines_changed=4, files_touched=["a.py"],
        ),
        _structural_risk_entry(
            2, 0.3, files_changed_ratio=0.4, hunks_per_file=2.0, net_lines=8,
            cumulative_lines_changed=12, files_touched=["a.py", "b.py"],
        ),
    ]
    data = {"robust_agent": {"episode_state_final": {"signals_history": signals}}}
    metrics = TrajectoryReader().read(data)

    assert metrics.structural_risk_final == 0.3
    assert metrics.files_changed_ratio == 0.4
    assert metrics.hunks_per_file == 2.0
    assert metrics.net_lines == 8
    assert metrics.cumulative_lines_changed == 12
    assert metrics.surface_final_size == 2


def test_read_structural_risk_high_ratio():
    """Comprueba que read structural risk high ratio."""
    signals = [
        _structural_risk_entry(1, 0.1, level="low"),
        _structural_risk_entry(2, 0.9, level="high"),
    ]
    data = {"robust_agent": {"episode_state_final": {"signals_history": signals}}}
    metrics = TrajectoryReader().read(data)
    assert metrics.structural_risk_high_ratio == 0.5


# ---------------------------------------------------------------------------
# Controlador
# ---------------------------------------------------------------------------


def test_read_controller_counts_actions():
    """Comprueba que read controller counts actions."""
    decisions = [
        _decision_entry(1, "PROCEED"),
        _decision_entry(2, "INJECT_FEEDBACK"),
        _decision_entry(3, "RUN_VALIDATION"),
        _decision_entry(4, "FINALIZE", subtype="abort"),
        _decision_entry(5, "FINALIZE", subtype="submit"),
    ]
    data = {"robust_agent": {"episode_state_final": {"decisions_history": decisions}}}
    metrics = TrajectoryReader().read(data)

    assert metrics.proceed_count == 1
    assert metrics.feedback_count == 1
    assert metrics.validation_count == 1
    assert metrics.abort_count == 1
    assert metrics.controller_intervention_count == 4
    assert metrics.final_controller_action == "FINALIZE"


def test_read_controller_first_intervention_step():
    """Comprueba que read controller first intervention step."""
    decisions = [
        _decision_entry(1, "PROCEED"),
        _decision_entry(2, "PROCEED"),
        _decision_entry(3, "INJECT_FEEDBACK"),
        _decision_entry(4, "RUN_VALIDATION"),
    ]
    data = {"robust_agent": {"episode_state_final": {"decisions_history": decisions}}}
    metrics = TrajectoryReader().read(data)
    assert metrics.first_intervention_step == 3


def test_read_controller_no_intervention_case():
    """Caso observado en la practica: solo PROCEED y FINALIZE(submit)."""
    decisions = [
        _decision_entry(1, "PROCEED"),
        _decision_entry(2, "PROCEED"),
    ]
    data = {"robust_agent": {"episode_state_final": {"decisions_history": decisions}}}
    metrics = TrajectoryReader().read(data)
    assert metrics.controller_intervention_count == 0
    assert metrics.first_intervention_step is None


def test_read_controller_high_high_encounter_count():
    """Comprueba que read controller high high encounter count."""
    decisions = [
        _decision_entry(1, "PROCEED", uncertainty_level="high", risk_level="high"),
        _decision_entry(2, "PROCEED", uncertainty_level="low", risk_level="high"),
        _decision_entry(3, "PROCEED", uncertainty_level="high", risk_level="high"),
    ]
    data = {"robust_agent": {"episode_state_final": {"decisions_history": decisions}}}
    metrics = TrajectoryReader().read(data)
    assert metrics.high_high_encounter_count == 2


def test_read_controller_empty_decisions_history_gives_zero_counts():
    """Comprueba que read controller empty decisions history gives zero counts."""
    data = {"robust_agent": {"episode_state_final": {"decisions_history": []}}}
    metrics = TrajectoryReader().read(data)
    assert metrics.controller_intervention_count == 0
    assert metrics.proceed_count == 0
    assert metrics.final_controller_action is None


# ---------------------------------------------------------------------------
# validation_outcome (robust_agent.steps)
# ---------------------------------------------------------------------------


def test_read_validation_outcomes_counts_success_and_failure():
    """Comprueba que read validation outcomes counts success and failure."""
    steps = [
        {"step_id": 1, "validation_outcome": {"status": "passed"}},
        {"step_id": 2, "validation_outcome": {"status": "directive_issued"}},
        {"step_id": 3, "validation_outcome": {"status": "error"}},
        {"step_id": 4, "validation_outcome": {"status": "skipped"}},
        {"step_id": 5, "validation_outcome": None},
    ]
    data = {"robust_agent": {"steps": steps, "episode_state_final": {}}}
    metrics = TrajectoryReader().read(data)
    assert metrics.validation_success_count == 2
    assert metrics.validation_failure_count == 1


# ---------------------------------------------------------------------------
# build_step_rows
# ---------------------------------------------------------------------------


def test_build_step_rows_returns_empty_without_robust_agent():
    """Comprueba que build step rows returns empty without robust agent."""
    rows = TrajectoryReader().build_step_rows(
        {"messages": []}, run_id="r-1", instance_id="repo__repo-1", configuration_id="default"
    )
    assert rows == []


def test_build_step_rows_maps_fields_per_step():
    """Comprueba que build step rows maps fields per step."""
    steps = [
        {
            "step_id": 1,
            "uncertainty": {"score": 0.1, "level": "low"},
            "structural_risk": {"score": 0.2, "level": "low"},
            "decision": {"action": "PROCEED"},
            "validation_outcome": None,
        },
        {
            "step_id": 2,
            "uncertainty": {"score": 0.0, "level": "low"},
            "structural_risk": {},
            "decision": None,
            "validation_outcome": None,
        },
    ]
    data = {"robust_agent": {"steps": steps}}
    rows = TrajectoryReader().build_step_rows(
        data, run_id="r-1", instance_id="repo__repo-1", configuration_id="robust_balanced__gemini-2.5-flash"
    )

    assert len(rows) == 2
    assert rows[0].step_index == 1
    assert rows[0].uncertainty_score == 0.1
    assert rows[0].structural_risk_score == 0.2
    assert rows[0].controller_action == "PROCEED"
    assert rows[0].run_id == "r-1"
    assert rows[0].instance_id == "repo__repo-1"
    assert rows[0].configuration_id == "robust_balanced__gemini-2.5-flash"

    # Ultimo paso: sin decision (submit) y structural_risk vacio.
    assert rows[1].controller_action is None
    assert rows[1].structural_risk_score is None
