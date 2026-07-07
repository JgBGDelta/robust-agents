"""Tests de integracion end-to-end del `RobustAgent` (Fase 3, paso 5).

Estos tests ejercitan el bucle completo del agente robusto sobre:

- un repositorio git local efimero (PR1 satisfecha con un commit limpio),
- un `DeterministicModel` de `mini-swe-agent` (sin LLM real, sin red),
- un `LocalEnvironment` con `cwd` en el repo.

El objetivo no es verificar fórmulas internas (eso lo cubren los tests
unitarios de `tests/agent/test_*_module.py`) sino el *wiring* del bucle
y el contrato de la traza `robust-agent-1.0`. El diseño completo y los
gaps explícitos están documentados en `docs/fase2/agente/tests.md`.

Los artefactos (git de prueba, trazas JSON) se crean bajo `tests/agent/tmp/`,
no en el TMP del sistema.

La organización del archivo sigue los bloques del diseño:

- A: smoke de forma de la traza
- B: cuatro vias de terminacion (`model_submitted`, `limits_exceeded`,
  `precondition_failed`, `controller_finalize`)
- C: aplicacion de la decision al bucle (`INJECT_FEEDBACK`,
  `RUN_VALIDATION`)
- D: contrato `validation_outcome`
- E: contabilidad de presupuesto via `BudgetGuard`
- F: corrector de finalizacion por estabilidad (`StabilityMonitor`)
- G: entorno bash local (resolucion de ejecutable y ejecucion real de comandos)
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from agent.config import (
    ControllerConfig,
    StructuralMetricsConfig,
    UncertaintyConfig,
)
from agent.robust_agent import ROBUST_TRACE_FORMAT_VERSION, RobustAgent
from agent.environments import build_local_environment, resolve_bash
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.test_models import DeterministicModel

# Silenciar el banner de mini-swe-agent al importarlo.
os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")

# Raiz autocontenida en el repo: ningun artefacto E2E va al TMP del sistema.
_E2E_TMP_ROOT = Path(__file__).resolve().parent / "tmp"


def _force_remove_readonly(func, path, _exc_info) -> None:
    """Handler para `shutil.rmtree` que retira el bit read-only y reintenta.

    Necesario en Windows con repos git: los ficheros bajo `.git/objects`
    se crean read-only y bloquean `os.unlink`. Sin este handler, los
    `PermissionError` se descartarian silenciosamente y dejarian basura
    en `tests/agent/tmp/`.
    """
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except FileNotFoundError:
        pass


@pytest.fixture
def e2e_workdir(request: pytest.FixtureRequest) -> Path:
    """Subdirectorio unico bajo `tests/agent/tmp` por test; se borra al final."""
    _E2E_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in request.node.name)
    work = _E2E_TMP_ROOT / f"{safe}_{uuid.uuid4().hex[:10]}"
    work.mkdir(parents=True, exist_ok=True)
    yield work
    shutil.rmtree(work, onerror=_force_remove_readonly)


# --- Helpers compartidos -------------------------------------------------------------


def _run_git(repo: Path, *args: str) -> None:
    """Ejecuta un comando git contra el repo de prueba."""
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _init_clean_git_repo(workdir: Path) -> Path:
    """Crea un repositorio git con un commit inicial limpio (PR1 satisfecha)."""
    repo = workdir / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "test")
    (repo / "main.py").write_text("print('hello')\n", encoding="utf-8")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "init")
    return repo


def _make_output(
    content: str,
    actions: list[dict],
    *,
    cost: float = 0.01,
    confidence: float | None = None,
    token_logprobs: list[float] | None = None,
) -> dict:
    """Construye un output del DeterministicModel con metadatos de incertidumbre."""
    extra: dict[str, Any] = {"actions": actions, "cost": cost, "timestamp": time.time()}
    if confidence is not None:
        extra["confidence"] = confidence
    if token_logprobs is not None:
        extra["token_logprobs"] = token_logprobs
    return {"role": "assistant", "content": content, "extra": extra}


def _build_agent(
    repo: Path,
    outputs: list[dict],
    output_path: Path,
    *,
    profile: str = "balanced",
    step_limit: int = 5,
    cost_limit: float = 10.0,
    structural_metrics: StructuralMetricsConfig | None = None,
    uncertainty: UncertaintyConfig | None = None,
    controller: ControllerConfig | None = None,
) -> RobustAgent:
    """Construye un `RobustAgent` con `DeterministicModel` y `LocalEnvironment`."""
    model = DeterministicModel(outputs=outputs)
    env = LocalEnvironment(cwd=str(repo))
    config_kwargs: dict[str, Any] = {
        "profile": profile,
        "system_template": "sys",
        "instance_template": "task",
        "output_path": output_path,
        "step_limit": step_limit,
        "cost_limit": cost_limit,
    }
    if structural_metrics is not None:
        config_kwargs["structural_metrics"] = structural_metrics
    if uncertainty is not None:
        config_kwargs["uncertainty"] = uncertainty
    if controller is not None:
        config_kwargs["controller"] = controller
    return RobustAgent(model=model, env=env, **config_kwargs)


def _build_agent_with_bash_env(
    repo: Path,
    outputs: list[dict],
    output_path: Path,
    *,
    step_limit: int = 5,
    cost_limit: float = 10.0,
) -> RobustAgent:
    """Construye un `RobustAgent` con `BashLocalEnvironment` (como el CLI)."""
    if resolve_bash() is None:
        pytest.skip("bash funcional no disponible en este entorno")
    model = DeterministicModel(outputs=outputs)
    env = build_local_environment(cwd=str(repo), shell="auto")
    return RobustAgent(
        model=model,
        env=env,
        profile="balanced",
        system_template="sys",
        instance_template="task",
        output_path=output_path,
        step_limit=step_limit,
        cost_limit=cost_limit,
    )


def _load_trace(path: Path) -> dict[str, Any]:
    """Carga el JSON de traza producido por `agent.save()`."""
    return json.loads(path.read_text(encoding="utf-8"))


def _submit_command() -> dict:
    """Comando que dispara `Submitted` desde `LocalEnvironment`."""
    return {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}


def _modify_tracked_file_command(content: str = "modified") -> dict:
    """Comando shell que sobrescribe `main.py` (tracked) para producir diff git.

    Se opera sobre un fichero ya versionado porque `git stash create`
    (usado por el modulo de metricas para materializar la referencia del
    paso) no captura ficheros sin trackear.
    """
    return {"command": f"echo {content}>main.py"}


# Configuraciones reutilizables para forzar niveles concretos sin depender
# de la calibracion fina de pesos por defecto.
_LOW_RISK_HIGH = StructuralMetricsConfig(low_threshold=0.0, high_threshold=0.01)
_LOW_UNCERTAINTY_BOUNDS_MEDIUM = UncertaintyConfig(low_threshold=0.05, high_threshold=0.95)


# =====================================================================================
# Bloque A: forma de la traza (smoke E2E)
# =====================================================================================


def test_smoke_trace_shape_has_all_contracts(e2e_workdir: Path):
    """Spec general 9 + 8.1: traza con bloque base + bloque robust_agent + StepTrace."""
    repo = _init_clean_git_repo(e2e_workdir)
    outputs = [_make_output("submit", [_submit_command()], cost=0.01, confidence=1.0)]
    agent = _build_agent(repo, outputs, e2e_workdir / "trace.json")

    agent.run("demo")
    trace = _load_trace(e2e_workdir / "trace.json")

    # Coexisten formato base y bloque robusto.
    assert trace["trajectory_format"] == "mini-swe-agent-1.1"
    assert "messages" in trace and "info" in trace
    assert "robust_agent" in trace

    robust = trace["robust_agent"]
    assert robust["format_version"] == ROBUST_TRACE_FORMAT_VERSION
    assert robust["profile"] == {"name": "balanced"}
    assert robust["termination"] in {
        "model_submitted",
        "limits_exceeded",
        "controller_finalize",
        "precondition_failed",
        "exception",
    }

    # `episode_state_final` con todas las claves comprometidas por spec 7.
    expected_state_keys = {
        "profile",
        "step_index",
        "budget",
        "signals_history",
        "decisions_history",
        "git_refs",
        "validation_state",
        "errors",
    }
    assert expected_state_keys <= set(robust["episode_state_final"])

    # `steps` no vacio y cada entrada con las 9 claves de StepTrace (spec 8.1).
    assert len(robust["steps"]) >= 1
    expected_step_keys = {
        "step_id",
        "timestamp",
        "uncertainty",
        "structural_risk",
        "decision",
        "validation_outcome",
        "budget_state",
        "git_refs",
        "errors",
    }
    for step in robust["steps"]:
        assert set(step) == expected_step_keys
        assert isinstance(step["step_id"], int) and step["step_id"] >= 1
        assert isinstance(step["timestamp"], float) and step["timestamp"] > 0


# =====================================================================================
# Bloque B: vias de terminacion
# =====================================================================================


def test_termination_model_submitted_records_partial_step(e2e_workdir: Path):
    """Submitted aborta dentro de execute_actions: StepTrace sin decision ni risk."""
    repo = _init_clean_git_repo(e2e_workdir)
    outputs = [_make_output("submit", [_submit_command()], cost=0.01, confidence=1.0)]
    agent = _build_agent(repo, outputs, e2e_workdir / "trace.json")

    agent.run("demo")
    trace = _load_trace(e2e_workdir / "trace.json")
    robust = trace["robust_agent"]

    assert robust["termination"] == "model_submitted"
    assert trace["messages"][-1]["role"] == "exit"
    assert trace["messages"][-1]["extra"]["exit_status"] == "Submitted"
    last_step = robust["steps"][-1]
    assert last_step["decision"] is None
    assert last_step["structural_risk"] == {}
    # La incertidumbre sÃ­ pudo evaluarse (super().query() retornÃ³).
    assert "score" in last_step["uncertainty"]


def test_termination_limits_exceeded_skips_aborted_step(e2e_workdir: Path):
    """LimitsExceeded antes de incrementar n_calls: el paso abortado no aparece en steps."""
    repo = _init_clean_git_repo(e2e_workdir)
    outputs = [
        _make_output("step 1", [{"command": "echo one"}], cost=0.01, confidence=1.0),
        # Este output nunca deberÃ­a llegar a consumirse: en el paso 2 query()
        # lanza LimitsExceeded antes de invocar al modelo.
        _make_output("never reached", [{"command": "echo never"}], cost=0.01, confidence=1.0),
    ]
    agent = _build_agent(repo, outputs, e2e_workdir / "trace.json", step_limit=1)

    agent.run("demo")
    trace = _load_trace(e2e_workdir / "trace.json")
    robust = trace["robust_agent"]

    assert robust["termination"] == "limits_exceeded"
    assert trace["messages"][-1]["extra"]["exit_status"] == "LimitsExceeded"
    # Solo el paso 1 tiene StepTrace; el paso 2 abortado no genera entrada.
    assert len(robust["steps"]) == 1
    assert robust["steps"][0]["step_id"] == 1


def test_termination_precondition_failed_emits_no_steps(
    e2e_workdir: Path, monkeypatch: pytest.MonkeyPatch
):
    """PR1 incumplida: termination=precondition_failed, sin steps ni llamadas al modelo.

    Se inyecta un fallo en `initialize_baseline` para verificar la ruta de salida
    controlada. Un repo con ficheros pre-modificados (imagenes SWE-bench) ya no bloquea.
    """
    repo = _init_clean_git_repo(e2e_workdir)
    outputs = [_make_output("never reached", [_submit_command()], cost=0.01)]
    agent = _build_agent(repo, outputs, e2e_workdir / "trace.json")
    monkeypatch.setattr(
        agent.structural_metrics_module,
        "initialize_baseline",
        lambda: (_ for _ in ()).throw(RuntimeError("fallo simulado en PR1")),
    )

    agent.run("demo")
    trace = _load_trace(e2e_workdir / "trace.json")
    robust = trace["robust_agent"]

    assert robust["termination"] == "precondition_failed"
    assert robust["steps"] == []
    assert robust["episode_state_final"]["signals_history"] == []
    assert agent.n_calls == 0
    assert trace["messages"][-1]["extra"]["exit_status"] == "PreconditionFailed"


def test_termination_controller_finalize_via_budget_clamp(e2e_workdir: Path):
    """BudgetGuard fuerza FINALIZE(abort) cuando RUN_VALIDATION no cabe en presupuesto."""
    repo = _init_clean_git_repo(e2e_workdir)
    # Output que dispara RUN_VALIDATION en strict (medium uncertainty + high risk).
    outputs = [
        _make_output(
            "step 1 medium confidence",
            [_modify_tracked_file_command()],
            cost=0.01,
            confidence=0.5,
        ),
    ]
    controller = ControllerConfig(tests_validation_enabled=True, self_review_enabled=True)
    agent = _build_agent(
        repo,
        outputs,
        e2e_workdir / "trace.json",
        profile="strict",
        cost_limit=0.04,  # 0.01 query + 0.05 combined no cabe.
        structural_metrics=_LOW_RISK_HIGH,
        uncertainty=_LOW_UNCERTAINTY_BOUNDS_MEDIUM,
        controller=controller,
    )

    agent.run("demo")
    trace = _load_trace(e2e_workdir / "trace.json")
    robust = trace["robust_agent"]

    assert robust["termination"] == "controller_finalize"
    last_step = robust["steps"][-1]
    assert last_step["decision"] is not None
    assert last_step["decision"]["action"] == "FINALIZE"
    assert last_step["decision"]["subtype"] == "abort"
    assert "budget_clamp" in last_step["decision"]["overrides"]
    assert trace["messages"][-1]["extra"]["exit_status"] == "ControllerFinalize"


# =====================================================================================
# Bloque C: aplicacion de la decision al bucle
# =====================================================================================


def test_decision_inject_feedback_appends_user_message(e2e_workdir: Path):
    """INJECT_FEEDBACK aplicado: aparece un user message con el texto del feedback."""
    repo = _init_clean_git_repo(e2e_workdir)
    # Paso 1: medium uncertainty + medium risk -> INJECT_FEEDBACK en balanced.
    # Paso 2: submit para cerrar el run con Ã©xito.
    outputs = [
        _make_output(
            "step 1",
            [_modify_tracked_file_command()],
            cost=0.01,
            confidence=0.5,
        ),
        _make_output("step 2", [_submit_command()], cost=0.01, confidence=1.0),
    ]
    medium_thresholds = StructuralMetricsConfig(low_threshold=0.0, high_threshold=0.95)
    agent = _build_agent(
        repo,
        outputs,
        e2e_workdir / "trace.json",
        structural_metrics=medium_thresholds,
        uncertainty=_LOW_UNCERTAINTY_BOUNDS_MEDIUM,
    )

    agent.run("demo")
    trace = _load_trace(e2e_workdir / "trace.json")
    robust = trace["robust_agent"]

    step1 = robust["steps"][0]
    assert step1["decision"] is not None
    assert step1["decision"]["action"] == "INJECT_FEEDBACK"

    feedback_text = step1["decision"]["payload"]["feedback_text"]
    assert "medium" in feedback_text  # incertidumbre o riesgo medium en plantilla
    # El feedback aparece en el historial de mensajes como user message.
    user_contents = [m["content"] for m in trace["messages"] if m["role"] == "user"]
    assert any(feedback_text in content for content in user_contents)
    # El run continuÃ³ al paso 2.
    assert robust["termination"] == "model_submitted"


def test_decision_run_validation_directive_in_history(e2e_workdir: Path):
    """RUN_VALIDATION(tests) inyecta la directiva de validacion en el historial."""
    repo = _init_clean_git_repo(e2e_workdir)
    outputs = [
        _make_output(
            "step 1",
            [_modify_tracked_file_command()],
            cost=0.01,
            confidence=0.5,
        ),
        _make_output("step 2", [_submit_command()], cost=0.01, confidence=1.0),
    ]
    controller = ControllerConfig(tests_validation_enabled=True)
    agent = _build_agent(
        repo,
        outputs,
        e2e_workdir / "trace.json",
        structural_metrics=_LOW_RISK_HIGH,
        uncertainty=_LOW_UNCERTAINTY_BOUNDS_MEDIUM,
        controller=controller,
    )

    agent.run("demo")
    trace = _load_trace(e2e_workdir / "trace.json")
    robust = trace["robust_agent"]

    step1 = robust["steps"][0]
    assert step1["decision"]["action"] == "RUN_VALIDATION"
    assert step1["decision"]["subtype"] == "tests"
    user_contents = [m["content"] for m in trace["messages"] if m["role"] == "user"]
    assert any("Validacion solicitada por el controlador" in c for c in user_contents)
    assert any("Valida tu cambio actual ejecutando tests" in c for c in user_contents)


# =====================================================================================
# Bloque D: contrato validation_outcome
# =====================================================================================


def test_validation_outcome_tests_directive_issued(e2e_workdir: Path):
    """RUN_VALIDATION(tests) inyecta directiva -> status=directive_issued, cost=0."""
    repo = _init_clean_git_repo(e2e_workdir)
    outputs = [
        _make_output(
            "step 1",
            [_modify_tracked_file_command()],
            cost=0.01,
            confidence=0.5,
        ),
        _make_output("step 2", [_submit_command()], cost=0.01, confidence=1.0),
    ]
    controller = ControllerConfig(tests_validation_enabled=True)
    agent = _build_agent(
        repo,
        outputs,
        e2e_workdir / "trace.json",
        structural_metrics=_LOW_RISK_HIGH,
        uncertainty=_LOW_UNCERTAINTY_BOUNDS_MEDIUM,
        controller=controller,
    )

    agent.run("demo")
    robust = _load_trace(e2e_workdir / "trace.json")["robust_agent"]
    outcome = robust["steps"][0]["validation_outcome"]

    assert outcome["subtype"] == "tests"
    assert outcome["status"] == "directive_issued"
    assert outcome["cost"] == 0.0
    assert "directive" in outcome["evidence"]
    assert "Valida tu cambio actual ejecutando tests" in outcome["evidence"]["directive"]
    assert outcome["evidence"]["forbidden_test_ids"] == []


def test_validation_outcome_tests_directive_includes_forbidden_block(e2e_workdir: Path):
    """forbidden_test_ids configurados aparecen renderizados en la directiva y en evidence."""
    repo = _init_clean_git_repo(e2e_workdir)
    outputs = [
        _make_output(
            "step 1",
            [_modify_tracked_file_command()],
            cost=0.01,
            confidence=0.5,
        ),
        _make_output("step 2", [_submit_command()], cost=0.01, confidence=1.0),
    ]
    forbidden = ["tests/foo::test_bug_fix", "tests/foo::test_regression"]
    controller = ControllerConfig(
        tests_validation_enabled=True,
        forbidden_test_ids=forbidden,
    )
    agent = _build_agent(
        repo,
        outputs,
        e2e_workdir / "trace.json",
        structural_metrics=_LOW_RISK_HIGH,
        uncertainty=_LOW_UNCERTAINTY_BOUNDS_MEDIUM,
        controller=controller,
    )

    agent.run("demo")
    robust = _load_trace(e2e_workdir / "trace.json")["robust_agent"]
    outcome = robust["steps"][0]["validation_outcome"]

    directive = outcome["evidence"]["directive"]
    assert "Restriccion importante" in directive
    for tid in forbidden:
        assert tid in directive
    assert outcome["evidence"]["forbidden_test_ids"] == forbidden


def test_validation_outcome_combined_with_self_review(e2e_workdir: Path):
    """combined inyecta directiva + ejecuta self_review e imputa coste a validation.self_review."""
    repo = _init_clean_git_repo(e2e_workdir)
    outputs = [
        _make_output(
            "step 1",
            [_modify_tracked_file_command()],
            cost=0.01,
            confidence=0.5,
        ),
        # Respuesta del modelo a la self-review.
        _make_output("self review reply", [], cost=0.05),
        # Paso 2: submit.
        _make_output("step 2", [_submit_command()], cost=0.01, confidence=1.0),
    ]
    controller = ControllerConfig(tests_validation_enabled=True, self_review_enabled=True)
    agent = _build_agent(
        repo,
        outputs,
        e2e_workdir / "trace.json",
        profile="strict",
        cost_limit=0.5,  # con margen para que no clamp.
        structural_metrics=_LOW_RISK_HIGH,
        uncertainty=_LOW_UNCERTAINTY_BOUNDS_MEDIUM,
        controller=controller,
    )

    agent.run("demo")
    robust = _load_trace(e2e_workdir / "trace.json")["robust_agent"]
    outcome = robust["steps"][0]["validation_outcome"]

    assert outcome["subtype"] == "combined"
    assert outcome["status"] == "directive_issued_with_review"
    assert outcome["cost"] == pytest.approx(0.05)
    assert "directive" in outcome["evidence"]
    assert outcome["evidence"]["review_received"] is True
    cost_by_attr = robust["episode_state_final"]["budget"]["cost_by_attribution"]
    assert cost_by_attr["validation.self_review"] == pytest.approx(0.05)


def test_validation_degenerates_to_proceed_without_capacity(e2e_workdir: Path):
    """Sin tests_validation_enabled, RUN_VALIDATION degenera a PROCEED y no emite validation_outcome."""
    repo = _init_clean_git_repo(e2e_workdir)
    outputs = [
        _make_output(
            "step 1",
            [_modify_tracked_file_command()],
            cost=0.01,
            confidence=0.5,
        ),
        _make_output("step 2", [_submit_command()], cost=0.01, confidence=1.0),
    ]
    controller = ControllerConfig(tests_validation_enabled=False, self_review_enabled=False)
    agent = _build_agent(
        repo,
        outputs,
        e2e_workdir / "trace.json",
        structural_metrics=_LOW_RISK_HIGH,
        uncertainty=_LOW_UNCERTAINTY_BOUNDS_MEDIUM,
        controller=controller,
    )

    agent.run("demo")
    robust = _load_trace(e2e_workdir / "trace.json")["robust_agent"]
    step1 = robust["steps"][0]

    assert step1["decision"]["action"] == "PROCEED"
    assert "validation_degenerated" in step1["decision"]["overrides"]
    assert step1["validation_outcome"] is None


# =====================================================================================
# Bloque E: contabilidad de presupuesto
# =====================================================================================


def test_budget_attribution_aggregates_query_and_self_review(e2e_workdir: Path):
    """BudgetGuard agrega `agent.query` y `validation.self_review` correctamente."""
    repo = _init_clean_git_repo(e2e_workdir)
    outputs = [
        _make_output(
            "step 1",
            [_modify_tracked_file_command()],
            cost=0.01,
            confidence=0.5,
        ),
        _make_output("self review reply", [], cost=0.05),
        _make_output("step 2", [_submit_command()], cost=0.01, confidence=1.0),
    ]
    controller = ControllerConfig(tests_validation_enabled=True, self_review_enabled=True)
    agent = _build_agent(
        repo,
        outputs,
        e2e_workdir / "trace.json",
        profile="strict",
        cost_limit=0.5,
        structural_metrics=_LOW_RISK_HIGH,
        uncertainty=_LOW_UNCERTAINTY_BOUNDS_MEDIUM,
        controller=controller,
    )

    agent.run("demo")
    robust = _load_trace(e2e_workdir / "trace.json")["robust_agent"]
    budget = robust["episode_state_final"]["budget"]

    assert budget["cost_by_attribution"]["agent.query"] == pytest.approx(0.02)
    assert budget["cost_by_attribution"]["validation.self_review"] == pytest.approx(0.05)
    assert budget["cost_used"] == pytest.approx(0.07)


# =====================================================================================
# Bloque F: corrector de finalizacion por estabilidad
# =====================================================================================


def test_stability_monitor_promotes_finalize_submit_after_stable_window(e2e_workdir: Path):
    """Paso 1 con cambio real + 9 pasos estables -> FINALIZE(submit) en paso 10.

    El StabilityMonitor usa la ``cumulative_surface_signature`` (firma del
    diff acumulado desde el baseline). Un cambio real en el paso 1 produce
    una firma constante y no vacía en los pasos 2-10 → se completa la
    ventana y el corrector promueve a FINALIZE(submit).
    """
    repo = _init_clean_git_repo(e2e_workdir)
    # Paso 1: modifica main.py para que haya un diff real tracked por git.
    # Pasos 2-10: echo idempotente (sin nuevos cambios); la firma acumulada
    # se mantiene igual porque el fichero ya fue modificado en el paso 1.
    outputs = [
        _make_output("step 1 modify", [_modify_tracked_file_command("fixed")], cost=0.01, confidence=1.0),
        *[
            _make_output(f"step {i}", [{"command": "echo step"}], cost=0.01, confidence=1.0)
            for i in range(2, 11)
        ],
    ]
    agent = _build_agent(repo, outputs, e2e_workdir / "trace.json", step_limit=15)

    agent.run("demo")
    robust = _load_trace(e2e_workdir / "trace.json")["robust_agent"]

    # El paso 10 es donde la ventana se completa y dispara la promocion.
    last_step = robust["steps"][-1]
    assert last_step["decision"] is not None
    assert last_step["decision"]["action"] == "FINALIZE"
    assert last_step["decision"]["subtype"] == "submit"
    assert "stability_promotion" in last_step["decision"]["overrides"]
    assert robust["termination"] == "controller_finalize"


def test_stability_monitor_does_not_promote_without_real_changes(e2e_workdir: Path):
    """Sin cambios reales en el repo, el StabilityMonitor no debe promover a submit.

    Un agente atascado (loop de comandos echo sin modificar ficheros) tiene
    ``cumulative_surface_signature`` vacía en todos los pasos. El monitor
    detecta esta condición y no dispara FINALIZE(submit), evitando que se
    envíe un parche vacío.
    """
    repo = _init_clean_git_repo(e2e_workdir)
    # 12 pasos echo sin modificar ningún fichero tracked.
    step_action = [{"command": "echo step"}]
    outputs = [
        _make_output(f"step {i}", step_action, cost=0.01, confidence=1.0)
        for i in range(1, 13)
    ]
    agent = _build_agent(repo, outputs, e2e_workdir / "trace.json", step_limit=12)

    agent.run("demo")
    robust = _load_trace(e2e_workdir / "trace.json")["robust_agent"]

    # El run debe terminar por límite de pasos, no por estabilidad.
    assert robust["termination"] == "limits_exceeded"
    # Ningún paso debe haber tenido FINALIZE(submit) por estabilidad.
    for step in robust["steps"]:
        dec = step.get("decision") or {}
        assert not (
            dec.get("action") == "FINALIZE"
            and dec.get("subtype") == "submit"
            and "stability_promotion" in dec.get("overrides", {})
        ), f"Paso {step['step_id']} disparó FINALIZE(submit) sin cambios reales"


# =====================================================================================
# Bloque G: entorno bash local (integracion con ejecucion real)
# =====================================================================================


def test_bash_executable_runs_commands_in_agent_loop(e2e_workdir: Path) -> None:
    """Regresion: bash resoluble y funcional (no stub WSL) ejecuta comandos en el bucle.

    Los demas tests E2E usan ``LocalEnvironment`` con ``echo`` (portable en cmd).
    Este test cubre el camino del CLI: ``BashLocalEnvironment`` + comandos POSIX
    como ``ls``/``cat``. Se omite si no hay bash usable en el host.
    """
    bash = resolve_bash()
    if bash is None:
        pytest.skip("bash funcional no disponible en este entorno")
    assert not {"system32", "syswow64"} & {p.lower() for p in Path(bash).parts}

    repo = _init_clean_git_repo(e2e_workdir)
    outputs = [
        _make_output("list repo", [{"command": "ls -F"}], cost=0.01, confidence=1.0),
        _make_output("read main", [{"command": "cat main.py"}], cost=0.01, confidence=1.0),
        _make_output("submit", [_submit_command()], cost=0.01, confidence=1.0),
    ]
    agent = _build_agent_with_bash_env(repo, outputs, e2e_workdir / "trace.json", step_limit=5)

    agent.run("demo")
    trace = _load_trace(e2e_workdir / "trace.json")

    observations = [
        m
        for m in trace["messages"]
        if m.get("role") == "user" and "raw_output" in m.get("extra", {})
    ]
    assert len(observations) >= 2, "se esperaban observaciones de ls y cat"
    ls_output = observations[0]["extra"]["raw_output"]
    cat_output = observations[1]["extra"]["raw_output"]
    assert observations[0]["extra"]["returncode"] == 0
    assert observations[1]["extra"]["returncode"] == 0
    assert "main.py" in ls_output
    assert "print('hello')" in cat_output
    for obs in observations[:2]:
        assert "WSL" not in obs["extra"]["raw_output"]
        assert "execvpe" not in obs["extra"]["raw_output"]

    assert trace["info"]["config"]["environment_type"].endswith("BashLocalEnvironment")
    assert trace["robust_agent"]["termination"] == "model_submitted"

