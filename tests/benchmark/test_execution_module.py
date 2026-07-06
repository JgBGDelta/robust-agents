"""Tests unitarios del `BenchmarkExecutionModule`.

Sin Docker y sin red: la fabrica de entorno se sustituye por un repo
git temporal en disco, la fabrica de agente devuelve un fake con
`run`/`serialize` controlados. Los tests cubren:
- `build_run_list` y `run_id` deterministas,
- ciclo de vida de `run_one`, retries, contaminacion, PR1 fallido,
- salto de runs ya completados via `BenchmarkResultsModule`,
- `AgentFactory`: resolucion de perfil desde `agent_id` o `config_overrides`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from benchmark.config import (
    AgentRunConfig,
    BenchmarkConfig,
    DatasetConfig,
    ExperimentConfig,
)
from benchmark.dataset_module.benchmark_instance import BenchmarkInstance
from benchmark.execution_module import BenchmarkExecutionModule, BenchmarkRunRecord
from benchmark.execution_module.agent_factory import AgentFactory
from benchmark.execution_module.contamination_detector import ContaminationDetector
from benchmark.execution_module.environment_factory import (
    EnvironmentFactory,
    EnvironmentHandle,
    PreconditionChecker,
    cleanup_temp_repo,
    docker_image_for_instance,
    make_clean_temp_repo,
)
from benchmark.execution_module.retry_policy import RetryPolicy
from benchmark.results_module import BenchmarkResultsModule
from benchmark.results_module.artifact_store import ArtifactStore
from benchmark.results_module.contracts import PlannedRun


# --- Fakes minimos --------------------------------------------------------------


class FakeAgent:
    """Agente fake con `run`/`serialize` controlados para tests de ejecucion."""

    def __init__(
        self,
        *,
        exit_status: str = "Submitted",
        submission: str = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@\n-old\n+new\n",
        trajectory: dict[str, Any] | None = None,
        raise_on_run: Exception | None = None,
    ) -> None:
        """Inicializa el agente fake con salida y traza configurables."""
        self._exit_status = exit_status
        self._submission = submission
        self._trajectory = trajectory or {
            "messages": [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "fix bug"},
                {"role": "assistant", "content": "ok", "extra": {"actions": ["echo done"]}},
                {
                    "role": "exit",
                    "content": exit_status,
                    "extra": {"exit_status": exit_status, "submission": submission},
                },
            ],
            "info": {"exit_status": exit_status, "submission": submission},
            "trajectory_format": "mini-swe-agent-1.1",
        }
        self._raise_on_run = raise_on_run

    def run(self, task: str = "") -> dict[str, Any]:
        """Ejecuta el agente simulado."""
        if self._raise_on_run is not None:
            raise self._raise_on_run
        return {"exit_status": self._exit_status, "submission": self._submission}

    def serialize(self) -> dict[str, Any]:
        """Serializa el estado del agente simulado."""
        return self._trajectory


class ScriptedAgentFactory:
    """`AgentFactory` que devuelve agentes fake en orden de invocacion."""

    def __init__(self, agents: list[FakeAgent]) -> None:
        """Inicializa `ScriptedAgentFactory`."""
        self._agents = list(agents)
        self.calls: list[tuple[str, str]] = []

    def __call__(
        self,
        instance: BenchmarkInstance,
        agent_run_config: AgentRunConfig,
        env: Any,
    ) -> FakeAgent:
        """Invoca el callable del mock."""
        self.calls.append((instance.instance_id, agent_run_config.agent_id))
        if not self._agents:
            raise RuntimeError("ScriptedAgentFactory: no quedan agentes en la cola")
        return self._agents.pop(0)


# --- Helpers de fixtures --------------------------------------------------------


def _agent_config(
    *,
    agent_id: str = "robust_balanced",
    agent_class: str = "agent.robust_agent.RobustAgent",
    seed: int | None = 42,
    model_id: str = "gemini/gemini-3-flash",
    config_overrides: dict[str, Any] | None = None,
) -> AgentRunConfig:
    """Auxiliar interno: agent config."""
    return AgentRunConfig(
        agent_id=agent_id,
        agent_class=agent_class,
        model_id=model_id,
        seed=seed,
        config_overrides=config_overrides or {},
    )


def _instance(
    *,
    instance_id: str = "inst-a",
    fail_to_pass: list[str] | None = None,
    pass_to_pass: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> BenchmarkInstance:
    """Auxiliar interno: instance."""
    return BenchmarkInstance(
        instance_id=instance_id,
        dataset_name="SWE-bench/SWE-bench_Lite",
        subset="lite",
        split="test",
        repo="org/repo",
        base_commit="abc",
        problem_statement="Fix the bug.",
        fail_to_pass=fail_to_pass or ["tests/test_a.py::test_foo"],
        pass_to_pass=pass_to_pass or ["tests/test_b.py::test_bar"],
        metadata=metadata or {},
    )


def _experiment_config(
    runs_root: Path,
    *,
    workers: int = 1,
    max_retries: int = 1,
    agents: list[AgentRunConfig] | None = None,
) -> ExperimentConfig:
    """Auxiliar interno: experiment config."""
    return ExperimentConfig(
        dataset=DatasetConfig(slice_size=2),
        agents=agents or [_agent_config()],
        benchmark=BenchmarkConfig(
            runs_root=str(runs_root),
            workers=workers,
            retry_policy={"max_retries": max_retries, "transient_errors_only": True},
        ),
    )


def _build_results_module(
    runs_root: Path,
    *,
    experiment_id: str = "exp-test",
    instances: list[BenchmarkInstance] | None = None,
    agents: list[AgentRunConfig] | None = None,
) -> BenchmarkResultsModule:
    """Auxiliar interno: build results module."""
    instances = instances or [_instance()]
    config = _experiment_config(runs_root, agents=agents)
    module = BenchmarkResultsModule(
        experiment_config=config,
        dataset_summary={"instance_ids": [i.instance_id for i in instances]},
        experiment_id_explicit=experiment_id,
    )
    module.initialize(matrix_size=len(instances) * len(config.agents))
    return module


@pytest.fixture
def temp_repo() -> Path:
    """Fixture de pytest: temp repo."""
    path = make_clean_temp_repo()
    yield path
    cleanup_temp_repo(path)


def _env_factory_for(repo: Path) -> EnvironmentFactory:
    """Auxiliar interno: env factory for."""
    def build(_instance, _config):
        """Construye el objeto bajo test."""
        return EnvironmentHandle(cwd=repo, raw_env=None, image_digest="sha256:fake")
    return EnvironmentFactory(build_fn=build)


# --- build_run_list y run_id ----------------------------------------------------


def test_build_run_list_cartesian_product():
    """Comprueba que build run list cartesian product."""
    instances = [_instance(instance_id="a"), _instance(instance_id="b")]
    config = _experiment_config(
        Path("runs"),
        agents=[
            _agent_config(agent_id="robust_balanced"),
            _agent_config(agent_id="default", agent_class="DefaultAgent"),
        ],
    )

    run_list = BenchmarkExecutionModule.build_run_list(config, instances)

    assert len(run_list) == 4
    assert [(c.instance_id, c.agent_run_config.agent_id) for c in run_list] == [
        ("a", "robust_balanced"),
        ("a", "default"),
        ("b", "robust_balanced"),
        ("b", "default"),
    ]


def test_build_run_list_respects_per_agent_dataset_slice_size():
    """Agentes con dataset_slice_size solo aparecen en su sub-slice de instancias."""
    # 6 instancias (el helper usa repo="org/repo" fijo; sequential toma las primeras N)
    instances = [_instance(instance_id=f"inst-{i:02d}") for i in range(6)]

    main_agent = _agent_config(agent_id="default", agent_class="DefaultAgent")
    ablation_agent = AgentRunConfig(
        agent_id="ablation",
        agent_class="agent.robust_agent.RobustAgent",
        model_id="gemini/gemini-3-flash",
        seed=42,
        dataset_slice_size=2,  # solo 2 de las 6 instancias
    )
    config = _experiment_config(
        Path("runs"),
        agents=[main_agent, ablation_agent],
    )
    # El DatasetConfig del experimento debe tener la misma estrategia para que
    # el sub-slice sea reproducible; usamos sequential para simplicidad.
    from benchmark.config import DatasetConfig
    config = config.model_copy(
        update={"dataset": DatasetConfig(slice_size=6, slice_strategy="sequential")}
    )

    run_list = BenchmarkExecutionModule.build_run_list(config, instances)

    default_runs = [r for r in run_list if r.agent_run_config.agent_id == "default"]
    ablation_runs = [r for r in run_list if r.agent_run_config.agent_id == "ablation"]

    assert len(default_runs) == 6, "main agent should run on all 6 instances"
    assert len(ablation_runs) == 2, "ablation agent should run on its 2-instance sub-slice"
    # El sub-slice secuencial toma las primeras 2 instancias
    ablation_ids = {r.instance_id for r in ablation_runs}
    assert ablation_ids == {"inst-00", "inst-01"}


def test_run_id_is_deterministic_and_path_safe():
    """Comprueba que run id is deterministic and path safe."""
    config = _agent_config(model_id="gemini/gemini-3-flash")
    rid_a = BenchmarkExecutionModule.run_id(instance_id="inst-1", agent_run_config=config)
    rid_b = BenchmarkExecutionModule.run_id(instance_id="inst-1", agent_run_config=config)

    assert rid_a == rid_b
    assert "/" not in rid_a
    assert rid_a.startswith("r-")


def test_run_id_changes_with_seed():
    """Comprueba que run id changes with seed."""
    cfg_a = _agent_config(seed=1)
    cfg_b = _agent_config(seed=2)
    rid_a = BenchmarkExecutionModule.run_id(instance_id="inst-1", agent_run_config=cfg_a)
    rid_b = BenchmarkExecutionModule.run_id(instance_id="inst-1", agent_run_config=cfg_b)
    assert rid_a != rid_b


def test_run_id_has_no_profile_in_hash():
    """Sin perfil en la clave: dos configs identicos dan el mismo run_id."""
    cfg = _agent_config(agent_id="robust_balanced")
    rid = BenchmarkExecutionModule.run_id(instance_id="inst-1", agent_run_config=cfg)
    assert rid.startswith("r-")
    assert len(rid) == 10  # "r-" + 8 hex


def test_retry_run_id_format():
    """Comprueba que retry run id format."""
    base = "r-abcd1234"
    assert BenchmarkExecutionModule.retry_run_id(base, attempt=1) == "r-abcd1234-r1"
    assert BenchmarkExecutionModule.retry_run_id(base, attempt=2) == "r-abcd1234-r2"


# --- RetryPolicy ----------------------------------------------------------------


def test_retry_policy_classifies_timeout_as_transient():
    """Comprueba que retry policy classifies timeout as transient."""
    assert RetryPolicy.is_transient(TimeoutError("docker pull timed out")) is True
    assert RetryPolicy.is_transient(ConnectionError("connection reset")) is True


def test_retry_policy_classifies_value_error_as_non_transient():
    """Comprueba que retry policy classifies value error as non transient."""
    assert RetryPolicy.is_transient(ValueError("bad parse")) is False
    assert RetryPolicy.is_transient(KeyError("missing")) is False


def test_retry_policy_classifies_http_5xx_as_transient():
    """Comprueba que retry policy classifies http 5xx as transient."""
    assert RetryPolicy.is_transient(RuntimeError("HTTP 503 service unavailable")) is True
    assert RetryPolicy.is_transient(RuntimeError("HTTP 404 not found")) is False


def test_retry_policy_decide_from_error_respects_transient_flag():
    """Comprueba que retry policy decide from error respects transient flag."""
    policy = RetryPolicy(max_retries=1)
    assert policy.decide_from_error(
        error={"transient": True, "exception_type": "X", "message": ""},
        attempts_done=1,
    ).should_retry is True
    assert policy.decide_from_error(
        error={"transient": False, "exception_type": "X", "message": ""},
        attempts_done=1,
    ).should_retry is False
    assert policy.decide_from_error(
        error={"transient": True, "exception_type": "X", "message": ""},
        attempts_done=2,
    ).should_retry is False


# --- ContaminationDetector ------------------------------------------------------


def test_contamination_detector_returns_no_matches_when_clean():
    """Comprueba que contamination detector returns no matches when clean."""
    trajectory = {"messages": [{"role": "assistant", "content": "patching the file"}]}
    result = ContaminationDetector.audit(trajectory, ["tests/test_secret.py::test_x"])
    assert result.detected is False
    assert result.matches == []


def test_contamination_detector_finds_substring_in_assistant_message():
    """Comprueba que contamination detector finds substring in assistant message."""
    trajectory = {
        "messages": [
            {"role": "system", "content": "do not execute tests/test_secret.py::test_x"},
            {
                "role": "assistant",
                "content": "I will run pytest tests/test_secret.py::test_x to check",
            },
        ]
    }
    result = ContaminationDetector.audit(trajectory, ["tests/test_secret.py::test_x"])
    assert result.detected is True
    assert len(result.matches) == 1
    assert result.matches[0].step_index == 1
    assert result.matches[0].role == "assistant"


def test_contamination_detector_finds_match_in_extra_actions():
    """Comprueba que contamination detector finds match in extra actions."""
    trajectory = {
        "messages": [
            {
                "role": "assistant",
                "content": "running test",
                "extra": {"actions": ["pytest tests/forbidden.py::test_z"]},
            }
        ]
    }
    result = ContaminationDetector.audit(trajectory, ["tests/forbidden.py::test_z"])
    assert result.detected is True


def test_contamination_detector_returns_no_matches_when_forbidden_empty():
    """Comprueba que contamination detector returns no matches when forbidden empty."""
    trajectory = {"messages": [{"role": "assistant", "content": "any"}]}
    assert ContaminationDetector.audit(trajectory, []).detected is False
    assert ContaminationDetector.audit(trajectory, None).detected is False


# --- AgentFactory: resolucion de perfil via agent_id ----------------------------


def test_agent_factory_enables_validation_for_robust_balanced():
    """Comprueba que agent factory enables validation for robust balanced."""
    assert AgentFactory.should_enable_tests_validation(
        _agent_config(agent_id="robust_balanced")
    ) is True


def test_agent_factory_enables_validation_for_robust_strict():
    """Comprueba que agent factory enables validation for robust strict."""
    assert AgentFactory.should_enable_tests_validation(
        _agent_config(agent_id="robust_strict")
    ) is True


def test_agent_factory_disables_validation_for_robust_permissive():
    """Comprueba que agent factory disables validation for robust permissive."""
    assert AgentFactory.should_enable_tests_validation(
        _agent_config(agent_id="robust_permissive")
    ) is False


def test_agent_factory_disables_validation_for_default_agent():
    """Comprueba que agent factory disables validation for default agent."""
    assert AgentFactory.should_enable_tests_validation(
        _agent_config(agent_id="default", agent_class="agent.default.DefaultAgent")
    ) is False


def test_agent_factory_enables_validation_via_config_overrides():
    """Profile explicito en `config_overrides` tiene prioridad."""
    assert AgentFactory.should_enable_tests_validation(
        _agent_config(
            agent_id="robust",
            config_overrides={"profile": "balanced"},
        )
    ) is True


def test_agent_factory_overrides_populate_forbidden_test_ids():
    """Comprueba que agent factory overrides populate forbidden test ids."""
    instance = _instance(
        fail_to_pass=["tests/a.py::t1"], pass_to_pass=["tests/b.py::t2"]
    )
    overrides = AgentFactory.build_robust_agent_overrides(
        agent_run_config=_agent_config(agent_id="robust_balanced"),
        instance=instance,
    )
    assert overrides["controller"]["tests_validation_enabled"] is True
    assert overrides["controller"]["forbidden_test_ids"] == [
        "tests/a.py::t1",
        "tests/b.py::t2",
    ]


def test_agent_factory_strict_enables_self_review():
    """El perfil `strict` activa `self_review_enabled` para materializar `combined`."""
    instance = _instance(
        fail_to_pass=["tests/a.py::t1"], pass_to_pass=["tests/b.py::t2"]
    )
    overrides = AgentFactory.build_robust_agent_overrides(
        agent_run_config=_agent_config(agent_id="robust_strict"),
        instance=instance,
    )
    assert overrides["controller"]["tests_validation_enabled"] is True
    assert overrides["controller"]["self_review_enabled"] is True


def test_agent_factory_balanced_does_not_enable_self_review():
    """El perfil `balanced` no activa `self_review_enabled` (solo `tests`)."""
    overrides = AgentFactory.build_robust_agent_overrides(
        agent_run_config=_agent_config(agent_id="robust_balanced"),
        instance=_instance(),
    )
    assert overrides["controller"]["tests_validation_enabled"] is True
    assert "self_review_enabled" not in overrides["controller"]


def test_agent_factory_no_overrides_for_permissive():
    """Comprueba que agent factory no overrides for permissive."""
    overrides = AgentFactory.build_robust_agent_overrides(
        agent_run_config=_agent_config(agent_id="robust_permissive"),
        instance=_instance(),
    )
    assert "controller" not in overrides


def test_agent_factory_real_build_requires_api_key(monkeypatch: pytest.MonkeyPatch):
    """Sin `build_fn`, el factory valida la API key antes de instanciar LiteLLM."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    factory = AgentFactory()
    with pytest.raises(ValueError, match="API key"):
        factory.build(
            instance=_instance(),
            agent_run_config=_agent_config(),
            env=object(),
        )


# --- Precondition (PR1) ---------------------------------------------------------


def test_pr1_passes_on_clean_temp_repo(temp_repo: Path):
    """Comprueba que pr1 passes on clean temp repo."""
    handle = EnvironmentHandle(cwd=temp_repo)
    clean, reason = PreconditionChecker.is_repo_clean(handle)
    assert clean is True
    assert reason is None


def test_pr1_fails_on_dirty_repo(temp_repo: Path):
    """Comprueba que pr1 fails on dirty repo."""
    (temp_repo / "dirty.txt").write_text("dirty", encoding="utf-8")
    handle = EnvironmentHandle(cwd=temp_repo)
    clean, reason = PreconditionChecker.is_repo_clean(handle)
    assert clean is False
    assert reason is not None and "unclean_repo" in reason


def test_pr1_check_status_fn_can_be_injected_for_tests():
    """Comprueba que pr1 check status fn can be injected for tests."""
    handle = EnvironmentHandle(cwd=Path("/nonexistent"))
    clean, reason = PreconditionChecker.is_repo_clean(
        handle, status_fn=lambda _cwd: (0, "", "")
    )
    assert clean is True


# --- run_one: ciclo de vida -----------------------------------------------------


def _build_module(
    tmp_path: Path,
    repo: Path,
    *,
    agent_factory: ScriptedAgentFactory,
    pr1_status_fn=None,
    workers: int = 1,
    max_retries: int = 1,
    agents: list[AgentRunConfig] | None = None,
    instances: list[BenchmarkInstance] | None = None,
) -> tuple[BenchmarkResultsModule, BenchmarkExecutionModule]:
    """Auxiliar interno: build module."""
    runs_root = tmp_path / "runs"
    results = _build_results_module(runs_root, instances=instances, agents=agents)
    execution = BenchmarkExecutionModule(
        config=BenchmarkConfig(
            runs_root=str(runs_root),
            workers=workers,
            retry_policy={"max_retries": max_retries, "transient_errors_only": True},
        ),
        results_module=results,
        environment_factory=_env_factory_for(repo),
        agent_factory=AgentFactory(build_fn=agent_factory),
        pr1_status_fn=pr1_status_fn,
        clock=lambda: 1000.0,
    )
    return results, execution


def test_run_one_completes_with_fake_agent(tmp_path: Path, temp_repo: Path):
    """Comprueba que run one completes with fake agent."""
    factory = ScriptedAgentFactory([FakeAgent()])
    results, execution = _build_module(tmp_path, temp_repo, agent_factory=factory)

    record = execution.run_one(_instance(), _agent_config())

    assert record.status == "completed"
    assert record.exit_status == "Submitted"
    assert record.preds_entry["model_patch"].startswith("diff --git")
    assert record.contamination_detected is False
    assert record.environment["pr1_check"] is True


def test_run_one_persists_trajectory_and_model_patch_under_run_dir(
    tmp_path: Path, temp_repo: Path
):
    """Comprueba que run one persists trajectory and model patch under run dir."""
    factory = ScriptedAgentFactory([FakeAgent(submission="custom patch content")])
    results, execution = _build_module(tmp_path, temp_repo, agent_factory=factory)

    record = execution.run_one(_instance(), _agent_config())

    # Sin segmento de perfil: instances/<id>/<agent_id>/<run_id>/
    run_dir = (
        results.export_path()
        / "instances/inst-a/robust_balanced"
        / record.run_id
    )
    assert (run_dir / "trajectory.traj.json").is_file()
    assert (run_dir / "model.patch").read_text(encoding="utf-8") == "custom patch content"
    assert record.trajectory_path.endswith("/trajectory.traj.json")
    assert record.model_patch_path.endswith("/model.patch")
    assert "\\" not in record.trajectory_path


def test_run_one_pr1_failure_is_non_blocking(
    tmp_path: Path, temp_repo: Path
):
    """PR1 sucio no aborta el run: algunas imagenes SWE-bench oficiales tienen
    ficheros pre-modificados por configuracion de entorno. El check es informativo."""
    factory = ScriptedAgentFactory([FakeAgent(exit_status="Submitted")])

    results, execution = _build_module(
        tmp_path,
        temp_repo,
        agent_factory=factory,
        pr1_status_fn=lambda _cwd: (0, " M unstaged.txt\n", ""),
    )

    record = execution.run_one(_instance(), _agent_config())

    # El agente SÍ se invoca aunque el repo esté sucio.
    assert len(factory.calls) == 1 and factory.calls[0][0] == _instance().instance_id
    # El pr1_check registrado en el record es False (informativo).
    assert record.environment["pr1_check"] is False
    assert record.environment.get("pr1_detail") is not None
    # El run completa normalmente (no PreconditionFailed).
    assert record.status != "precondition_failed"
    assert record.exit_status != "PreconditionFailed"


def test_run_one_traduces_excepciones_a_failed_record(tmp_path: Path, temp_repo: Path):
    """Comprueba que run one traduces excepciones a failed record."""
    factory = ScriptedAgentFactory([FakeAgent(raise_on_run=ValueError("bad parse"))])
    results, execution = _build_module(
        tmp_path, temp_repo, agent_factory=factory, max_retries=0
    )

    record = execution.run_one(_instance(), _agent_config())

    assert record.status == "failed"
    assert record.error["exception_type"] == "ValueError"
    assert record.error["transient"] is False


def test_run_one_retries_on_transient_then_succeeds(tmp_path: Path, temp_repo: Path):
    """Comprueba que run one retries on transient then succeeds."""
    factory = ScriptedAgentFactory(
        [
            FakeAgent(raise_on_run=TimeoutError("docker pull timeout")),
            FakeAgent(),
        ]
    )

    results, execution = _build_module(
        tmp_path, temp_repo, agent_factory=factory, max_retries=1
    )

    record = execution.run_one(_instance(), _agent_config())

    assert record.status == "completed"
    assert record.environment.get("retry_of") is not None
    instance_dir = results.export_path() / "instances/inst-a/robust_balanced"
    run_dirs = sorted(p.name for p in instance_dir.iterdir() if p.is_dir())
    assert len(run_dirs) == 2


def test_run_one_no_retry_on_non_transient(tmp_path: Path, temp_repo: Path):
    """Comprueba que run one no retry on non transient."""
    factory = ScriptedAgentFactory(
        [FakeAgent(raise_on_run=ValueError("bad parse"))]
    )

    results, execution = _build_module(
        tmp_path, temp_repo, agent_factory=factory, max_retries=1
    )

    record = execution.run_one(_instance(), _agent_config())

    assert record.status == "failed"
    instance_dir = results.export_path() / "instances/inst-a/robust_balanced"
    run_dirs = [p for p in instance_dir.iterdir() if p.is_dir()]
    assert len(run_dirs) == 1


def test_run_one_detects_contamination_in_trajectory(tmp_path: Path, temp_repo: Path):
    """Comprueba que run one detects contamination in trajectory."""
    forbidden_id = "tests/forbidden.py::test_secret"
    instance = _instance(fail_to_pass=[forbidden_id], pass_to_pass=[])
    trajectory = {
        "messages": [
            {"role": "system", "content": "do not run forbidden tests"},
            {
                "role": "assistant",
                "content": f"I'll run pytest {forbidden_id} to check coverage",
                "extra": {"actions": [f"pytest {forbidden_id}"]},
            },
            {
                "role": "exit",
                "content": "Submitted",
                "extra": {"exit_status": "Submitted", "submission": "diff --git\n"},
            },
        ],
        "info": {"exit_status": "Submitted", "submission": "diff --git\n"},
    }
    factory = ScriptedAgentFactory(
        [FakeAgent(trajectory=trajectory, submission="diff --git\n")]
    )

    results, execution = _build_module(tmp_path, temp_repo, agent_factory=factory)

    record = execution.run_one(instance, _agent_config(agent_id="robust_balanced"))

    assert record.contamination_detected is True
    assert record.contamination_evidence is not None
    assert forbidden_id in record.contamination_evidence["matched_test_ids"]


def test_run_one_does_not_audit_contamination_for_permissive(
    tmp_path: Path, temp_repo: Path
):
    """Comprueba que run one does not audit contamination for permissive."""
    forbidden_id = "tests/forbidden.py::test_secret"
    instance = _instance(fail_to_pass=[forbidden_id], pass_to_pass=[])
    trajectory = {
        "messages": [
            {"role": "assistant", "content": f"running pytest {forbidden_id}"},
            {"role": "exit", "content": "Submitted",
             "extra": {"exit_status": "Submitted", "submission": ""}},
        ],
        "info": {"exit_status": "Submitted", "submission": ""},
    }
    factory = ScriptedAgentFactory([FakeAgent(trajectory=trajectory, submission="")])

    results, execution = _build_module(tmp_path, temp_repo, agent_factory=factory)

    record = execution.run_one(instance, _agent_config(agent_id="robust_permissive"))

    assert record.contamination_detected is False


def test_run_one_empty_submission_is_failed(tmp_path: Path, temp_repo: Path):
    """Comprueba que run one empty submission is failed."""
    trajectory = {
        "messages": [
            {"role": "exit", "content": "Submitted",
             "extra": {"exit_status": "Submitted", "submission": ""}},
        ],
        "info": {"exit_status": "Submitted", "submission": ""},
    }
    factory = ScriptedAgentFactory([FakeAgent(trajectory=trajectory, submission="")])
    results, execution = _build_module(tmp_path, temp_repo, agent_factory=factory)

    record = execution.run_one(_instance(), _agent_config())

    assert record.status == "failed"
    assert record.exit_status == "EmptySubmission"
    assert record.error == {
        "reason": "empty_model_patch",
        "agent_exit_status": "Submitted",
    }
    patch_file = results.export_path().joinpath(*record.model_patch_path.split("/"))
    assert patch_file.read_text(encoding="utf-8") == ""


# --- run_matrix -----------------------------------------------------------------


def test_run_matrix_skips_runs_already_completed(tmp_path: Path, temp_repo: Path):
    """Comprueba que run matrix skips runs already completed."""
    instances = [_instance(instance_id="a"), _instance(instance_id="b")]
    factory = ScriptedAgentFactory([FakeAgent(), FakeAgent()])
    results, execution = _build_module(
        tmp_path, temp_repo, agent_factory=factory, instances=instances
    )

    config = _experiment_config(tmp_path / "runs")
    run_list = BenchmarkExecutionModule.build_run_list(config, instances)
    completed_slot = run_list[0]
    run_dir = (
        results.export_path()
        / f"instances/{completed_slot.instance_id}/robust_balanced/{completed_slot.run_id}"
    )
    run_dir.mkdir(parents=True)
    ArtifactStore.write_json(
        run_dir / "run_record.json",
        {"status": "completed", "run_id": completed_slot.run_id},
    )

    records = execution.run_matrix(config, instances)

    assert len(records) == 1
    assert records[0].instance_id == "b"
    assert len(factory.calls) == 1


def test_run_matrix_iterates_in_instance_grouped_order(tmp_path: Path, temp_repo: Path):
    """Comprueba que run matrix iterates in instance grouped order."""
    instances = [_instance(instance_id="a"), _instance(instance_id="b")]
    factory = ScriptedAgentFactory([FakeAgent(), FakeAgent()])
    results, execution = _build_module(
        tmp_path, temp_repo, agent_factory=factory, instances=instances
    )
    config = _experiment_config(tmp_path / "runs")
    records = execution.run_matrix(config, instances)
    assert [r.instance_id for r in records] == ["a", "b"]


# --- EnvironmentFactory: Docker SWE-bench ---------------------------------------


def test_docker_image_for_instance_uses_swebench_convention():
    """Comprueba que docker image for instance uses swebench convention."""
    inst = _instance(instance_id="astropy__astropy-12907")
    assert docker_image_for_instance(inst) == (
        "docker.io/swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest"
    )


def test_docker_image_for_instance_respects_metadata_override():
    """Comprueba que docker image for instance respects metadata override."""
    inst = _instance(
        instance_id="astropy__astropy-12907",
        metadata={"docker_image": "custom/image:tag"},
    )
    assert docker_image_for_instance(inst) == "custom/image:tag"


class _DockerEnvStub:
    """Stub de entorno Docker para probar PR1 con salida de `git status`."""

    def __init__(self, *, output: str = "", returncode: int = 0) -> None:
        """Configura la salida simulada de `git status`."""
        self._output = output
        self._returncode = returncode
        self.executed: list[dict[str, Any]] = []

    def execute(self, action: dict[str, Any], cwd: str = "", *, timeout: int | None = None):
        """Ejecuta el comando simulado del entorno."""
        self.executed.append({"action": action, "cwd": cwd, "timeout": timeout})
        return {"output": self._output, "returncode": self._returncode, "exception_info": ""}


def test_pr1_runs_git_status_inside_docker_environment():
    """Comprueba que pr1 runs git status inside docker environment."""
    env = _DockerEnvStub()
    handle = EnvironmentHandle(cwd=Path("/testbed"), raw_env=env)
    clean, reason = PreconditionChecker.is_repo_clean(handle)
    assert clean is True
    assert reason is None
    assert env.executed[0]["action"] == {"command": "git status --porcelain"}
    assert env.executed[0]["cwd"] == "/testbed"


def test_pr1_fails_when_docker_repo_is_dirty():
    """Comprueba que pr1 fails when docker repo is dirty."""
    env = _DockerEnvStub(output=" M file.py\n")
    handle = EnvironmentHandle(cwd=Path("/testbed"), raw_env=env)
    clean, reason = PreconditionChecker.is_repo_clean(handle)
    assert clean is False
    assert reason is not None and "unclean_repo" in reason


def test_environment_factory_build_docker_without_injected_build_fn(monkeypatch):
    """Comprueba que environment factory build docker without injected build fn."""
    created: dict[str, Any] = {}

    class FakeDockerEnv:
        """Entorno Docker fake capturado por el monkeypatch."""

        def __init__(self, **kwargs: Any) -> None:
            """Registra los kwargs de construccion del entorno."""
            created.update(kwargs)

        def cleanup(self) -> None:
            """Cleanup."""
            pass

    monkeypatch.setattr(
        "minisweagent.environments.get_environment_class",
        lambda _spec: FakeDockerEnv,
    )
    factory = EnvironmentFactory()
    handle = factory.build(
        instance=_instance(instance_id="astropy__astropy-12907"),
        agent_run_config=_agent_config(),
    )
    assert created["image"] == (
        "docker.io/swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest"
    )
    assert created["cwd"] == "/testbed"
    assert handle.raw_env is not None
    factory.cleanup(handle)
