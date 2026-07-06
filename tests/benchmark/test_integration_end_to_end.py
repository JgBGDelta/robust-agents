"""Tests de integracion end-to-end del Bloque 2.

Recorre el pipeline completo con un mini-lote de 2 instancias x 2 configs
(default + robust_balanced), sustituyendo los componentes externos:
  - `BenchmarkDatasetModule` con loader fake (sin HuggingFace),
  - `EnvironmentFactory` con repo git temporal en disco (sin Docker),
  - `AgentFactory` con `FakeAgent` controlado (sin LLMs),
  - `SbCliClient` con runner fake (sin CLI real).

Comprobaciones:
  1. Arbol `runs/<experiment_id>/` completo con artefactos comprometidos.
  2. `manifest.json` en estado `finalized` con estructura minima.
  3. Un `evaluation_result.json` por run; sin `results.jsonl`.
  4. `evaluation_result.json` con bloques `functional` y `extended`.
  5. Layout sin segmento de perfil: `instances/<id>/<agent_id>/<run_id>/`.
  6. Reanudacion parcial: los runs ya completados se omiten.
  7. `force_rerun` archiva el directorio previo.
  8. Contaminacion: `contamination_detected=True` cuando procede.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from benchmark import (
    AgentRunConfig,
    BenchmarkConfig,
    BenchmarkDatasetModule,
    BenchmarkEvaluationModule,
    BenchmarkExecutionModule,
    BenchmarkResultsModule,
    BenchmarkRunner,
    DatasetConfig,
    ExperimentConfig,
)
from benchmark.evaluation_module.sb_cli_client import SbCliClient
from benchmark.execution_module.agent_factory import AgentFactory
from benchmark.execution_module.environment_factory import (
    EnvironmentFactory,
    EnvironmentHandle,
    cleanup_temp_repo,
    make_clean_temp_repo,
)


@pytest.fixture(autouse=True)
def _default_swebench_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inyecta un API key falso para que _preflight_sb_cli no aborte en tests."""
    monkeypatch.setenv("SWEBENCH_API_KEY", "test-key")


# --- Fakes externos -------------------------------------------------------------


class _FakeAgent:
    """Agente fake para el pipeline E2E del benchmark."""

    def __init__(
        self,
        *,
        submission: str,
        trajectory: dict[str, Any],
        exit_status: str = "Submitted",
    ) -> None:
        """Inicializa el agente con parche y traza prefijados."""
        self._submission = submission
        self._trajectory = trajectory
        self._exit_status = exit_status

    def run(self, task: str = "") -> dict[str, Any]:
        """Ejecuta el agente simulado."""
        return {"exit_status": self._exit_status, "submission": self._submission}

    def serialize(self) -> dict[str, Any]:
        """Serializa el estado del agente simulado."""
        return dict(self._trajectory)


def _baseline_trajectory(submission: str) -> dict[str, Any]:
    """Auxiliar interno: baseline trajectory."""
    return {
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "fix"},
            {"role": "assistant", "content": "ok", "extra": {"actions": ["echo done"]}},
            {
                "role": "exit",
                "content": "Submitted",
                "extra": {"exit_status": "Submitted", "submission": submission},
            },
        ],
        "info": {"exit_status": "Submitted", "submission": submission},
        "trajectory_format": "mini-swe-agent-1.1",
    }


def _robust_trajectory(submission: str, *, attack_test_id: str | None = None) -> dict[str, Any]:
    """Auxiliar interno: robust trajectory."""
    actions = ["pytest tests/"]
    if attack_test_id:
        actions.append(f"pytest -k '{attack_test_id}'")
    return {
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "fix"},
            {"role": "assistant", "content": "step 0", "extra": {"actions": ["ls"]}},
            {"role": "assistant", "content": "step 1", "extra": {"actions": actions}},
            {
                "role": "exit",
                "content": "Submitted",
                "extra": {"exit_status": "Submitted", "submission": submission},
            },
        ],
        "info": {"exit_status": "Submitted", "submission": submission},
        "trajectory_format": "mini-swe-agent-1.1",
        "robust_agent": {
            "format_version": "robust-agent-1.0",
            "episode_state_final": {
                "budget": {
                    "cost_used": 0.1,
                    "cost_remaining": 1.9,
                    "steps_used": 2,
                    "steps_remaining": 48,
                },
            },
            "steps": [],
            "termination": "model_submitted",
        },
    }


def _fake_dataset_loader(rows: list[dict[str, Any]]):
    """Auxiliar interno: fake dataset loader."""
    class _Loader:
        """Loader fake que devuelve filas prefijadas sin HuggingFace."""

        def load(self, *, name: str, subset: str, split: str) -> list[dict[str, Any]]:
            """Devuelve la copia de las filas del dataset de prueba."""
            return list(rows)
    return _Loader()


def _sb_cli_runner_writes(report: dict[str, Any]):
    """Auxiliar interno: sb cli runner writes."""
    def runner(*args, **kwargs):  # noqa: ANN001
        """Simula sb-cli escribiendo el reporte JSON en `--output_dir`."""
        argv = args[0] if args else kwargs.get("args")
        out_dir: Path | None = None
        for i, token in enumerate(argv):
            if token == "--output_dir":
                out_dir = Path(argv[i + 1])
                break
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="ok", stderr="")
    return runner


# --- Constructor del runner integrado -------------------------------------------


_GOLD_PATCH = """\
diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,2 @@
-old
+new
 ctx
"""

_MODEL_PATCH = """\
diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,2 @@
-old
+new
 ctx
"""


def _make_dataset_rows() -> list[dict[str, Any]]:
    """Auxiliar interno: make dataset rows."""
    return [
        {
            "instance_id": "repoA__pkg-1",
            "repo": "repoA/pkg",
            "base_commit": "abc",
            "problem_statement": "Fix bug A.",
            "patch": _GOLD_PATCH,
            "FAIL_TO_PASS": json.dumps(["tests/test_a.py::test_foo"]),
            "PASS_TO_PASS": json.dumps(["tests/test_b.py::test_bar"]),
        },
        {
            "instance_id": "repoB__pkg-1",
            "repo": "repoB/pkg",
            "base_commit": "def",
            "problem_statement": "Fix bug B.",
            "patch": _GOLD_PATCH,
            "FAIL_TO_PASS": json.dumps(["tests/test_c.py::test_baz"]),
            "PASS_TO_PASS": json.dumps([]),
        },
    ]


def _agents() -> list[AgentRunConfig]:
    """Auxiliar interno: agents."""
    return [
        AgentRunConfig(
            agent_id="default",
            agent_class="minisweagent.agents.default.DefaultAgent",
            model_id="gemini/gemini-3-flash",
        ),
        AgentRunConfig(
            agent_id="robust_balanced",
            agent_class="agent.robust_agent.RobustAgent",
            model_id="gemini/gemini-3-flash",
        ),
    ]


def _build_runner(
    *,
    runs_root: Path,
    repo: Path,
    sb_cli_report: dict[str, Any],
    attack_test_id: str | None = None,
    experiment_id_explicit: str = "integration-exp",
    force_rerun: bool = False,
) -> BenchmarkRunner:
    """Auxiliar interno: build runner."""
    rows = _make_dataset_rows()
    cfg = ExperimentConfig(
        dataset=DatasetConfig(name="SWE-bench/SWE-bench_Lite", subset="lite", split="test"),
        agents=_agents(),
        benchmark=BenchmarkConfig(runs_root=str(runs_root), workers=1),
        experiment_id_explicit=experiment_id_explicit,
        force_rerun=force_rerun,
    )

    results_module = BenchmarkResultsModule(
        experiment_config=cfg,
        runs_root=str(runs_root),
        experiment_id_explicit=experiment_id_explicit,
        force_rerun=force_rerun,
    )

    dataset_module = BenchmarkDatasetModule(
        config=cfg.benchmark, loader=_fake_dataset_loader(rows)
    )

    def env_build(_inst, _agent):
        """Env build."""
        return EnvironmentHandle(cwd=repo, raw_env=None, image_digest="sha256:fake")

    env_factory = EnvironmentFactory(build_fn=env_build, cleanup_fn=lambda _h: None)

    def agent_build(instance, agent_run_config, env):  # noqa: ANN001
        """Construye `_FakeAgent` con traza baseline o robusta segun la clase."""
        if "RobustAgent" in agent_run_config.agent_class:
            traj = _robust_trajectory(_MODEL_PATCH, attack_test_id=attack_test_id)
        else:
            traj = _baseline_trajectory(_MODEL_PATCH)
        return _FakeAgent(submission=_MODEL_PATCH, trajectory=traj)

    agent_factory = AgentFactory(build_fn=agent_build)

    execution_module = BenchmarkExecutionModule(
        config=cfg.benchmark,
        results_module=results_module,
        environment_factory=env_factory,
        agent_factory=agent_factory,
    )

    sb_cli = SbCliClient(runner=_sb_cli_runner_writes(sb_cli_report), max_retries=0)
    evaluation_module = BenchmarkEvaluationModule(
        config=cfg.benchmark, results_module=results_module, sb_cli_client=sb_cli
    )

    return BenchmarkRunner(
        cfg,
        results_module=results_module,
        dataset_module=dataset_module,
        execution_module=execution_module,
        evaluation_module=evaluation_module,
        sb_cli_client=sb_cli,
    )


@pytest.fixture
def temp_repo() -> Path:
    """Fixture de pytest: repositorio git temporal limpio para el E2E."""
    path = make_clean_temp_repo()
    try:
        yield path
    finally:
        cleanup_temp_repo(path)


# --- Test 1: pipeline end-to-end completo ---------------------------------------


def test_end_to_end_runs_full_pipeline_and_finalizes(tmp_path: Path, temp_repo: Path):
    """Pipeline completo: dataset -> ejecucion -> evaluacion -> finalize."""
    sb_cli_report = {"resolved_ids": ["repoA__pkg-1"]}
    runner = _build_runner(
        runs_root=tmp_path / "runs",
        repo=temp_repo,
        sb_cli_report=sb_cli_report,
    )

    export = runner.run()

    assert export.status == "finalized"
    assert export.runs_count == 4  # 2 instances x 2 agents
    exp_root = tmp_path / "runs" / "integration-exp"
    assert export.experiment_path == exp_root
    assert export.manifest_path == exp_root / "manifest.json"

    assert (exp_root / "manifest.json").is_file()
    assert (exp_root / "config.yaml").is_file()
    assert (exp_root / "dataset_summary.json").is_file()
    assert (exp_root / "instances").is_dir()
    assert (exp_root / "groups").is_dir()
    assert not (exp_root / "results.jsonl").exists()

    manifest = json.loads((exp_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "finalized"
    assert manifest["matrix_size"] == 4
    assert manifest["runs_completed"] == 4
    assert manifest["updated_at"] is not None
    assert "runs" not in manifest
    assert "finalized_at" not in manifest

    # Layout sin segmento de perfil: instances/<id>/<agent_id>/<run_id>/
    for instance_id in ("repoA__pkg-1", "repoB__pkg-1"):
        for agent_id in ("default", "robust_balanced"):
            agent_dir = exp_root / "instances" / instance_id / agent_id
            run_dirs = list(agent_dir.iterdir())
            assert len(run_dirs) == 1, f"{instance_id}/{agent_id}: {run_dirs}"
            run_dir = run_dirs[0]
            assert (run_dir / "run_record.json").is_file()
            assert (run_dir / "evaluation_result.json").is_file()
            assert (run_dir / "trajectory.traj.json").is_file()
            assert (run_dir / "model.patch").is_file()
            assert not (run_dir / "functional_result.json").exists()
            assert not (run_dir / "extended_result.json").exists()

            # evaluation_result.json con bloques functional y extended
            ev = json.loads((run_dir / "evaluation_result.json").read_text(encoding="utf-8"))
            assert "functional" in ev, f"functional ausente en {run_dir}"
            assert "extended" in ev, f"extended ausente en {run_dir}"
            assert "run_metrics" in ev["extended"]
            assert "patch_metrics" in ev["extended"]
            assert "availability" in ev["extended"]
            for field in ("uncertainty_summary", "structural_risk_summary", "timing", "cost"):
                assert field not in ev, f"campo inesperado {field!r} en evaluation_result"

    # Groups: preds.json + functional_report.
    group_dirs = sorted((exp_root / "groups").iterdir())
    assert len(group_dirs) == 2
    for group_dir in group_dirs:
        assert (group_dir / "preds.json").is_file()
        assert (group_dir / "functional_report").is_dir()


def test_end_to_end_same_keys_for_default_and_robust_balanced(
    tmp_path: Path, temp_repo: Path
):
    """Default y Robust producen las mismas claves en evaluation_result.json."""
    runner = _build_runner(
        runs_root=tmp_path / "runs",
        repo=temp_repo,
        sb_cli_report={},
    )
    runner.run()

    exp_root = tmp_path / "runs" / "integration-exp"

    default_run_dir = next(
        (exp_root / "instances" / "repoA__pkg-1" / "default").iterdir()
    )
    robust_run_dir = next(
        (exp_root / "instances" / "repoA__pkg-1" / "robust_balanced").iterdir()
    )
    default_ev = json.loads((default_run_dir / "evaluation_result.json").read_text(encoding="utf-8"))
    robust_ev = json.loads((robust_run_dir / "evaluation_result.json").read_text(encoding="utf-8"))

    assert set(default_ev.keys()) == set(robust_ev.keys())
    assert set(default_ev["extended"]["run_metrics"].keys()) == set(
        robust_ev["extended"]["run_metrics"].keys()
    )

    # Default no tiene trace_format; Robust si.
    assert default_ev["trace_format"] is None
    assert robust_ev["trace_format"] == "robust-agent-1.0"


# --- Test 2: reanudacion parcial -------------------------------------------------


def test_partial_resumption_skips_completed_runs(tmp_path: Path, temp_repo: Path):
    """Tras una primera tirada, la segunda no re-ejecuta runs ya completados."""
    runs_root = tmp_path / "runs"
    runner_first = _build_runner(
        runs_root=runs_root, repo=temp_repo, sb_cli_report={"resolved_ids": ["repoA__pkg-1"]}
    )
    runner_first.run()

    exp_root = runs_root / "integration-exp"
    record_paths: list[Path] = []
    for instance_dir in (exp_root / "instances").iterdir():
        for agent_dir in instance_dir.iterdir():
            if agent_dir.is_dir():
                for run_dir in agent_dir.iterdir():
                    if run_dir.is_dir():
                        record_paths.append(run_dir / "run_record.json")
    assert len(record_paths) == 4
    mtimes_before = {p: p.stat().st_mtime_ns for p in record_paths}

    runner_second = _build_runner(
        runs_root=runs_root, repo=temp_repo, sb_cli_report={"resolved_ids": ["repoA__pkg-1"]}
    )
    export = runner_second.run()

    assert export.status == "finalized"
    mtimes_after = {p: p.stat().st_mtime_ns for p in record_paths}
    for path, before in mtimes_before.items():
        assert mtimes_after[path] == before, f"{path} fue reescrito"


def test_resumption_also_reuses_evaluation_result_if_valid(tmp_path: Path, temp_repo: Path):
    """Al reanudar, `evaluation_result.json` valido no se recomputa."""
    runs_root = tmp_path / "runs"
    runner_first = _build_runner(runs_root=runs_root, repo=temp_repo, sb_cli_report={})
    runner_first.run()

    exp_root = runs_root / "integration-exp"
    ev_paths = []
    for instance_dir in (exp_root / "instances").iterdir():
        for agent_dir in instance_dir.iterdir():
            for run_dir in agent_dir.iterdir():
                ev_paths.append(run_dir / "evaluation_result.json")
    assert len(ev_paths) == 4
    mtimes = {p: p.stat().st_mtime_ns for p in ev_paths}

    runner_second = _build_runner(runs_root=runs_root, repo=temp_repo, sb_cli_report={})
    runner_second.run()

    for p, before in mtimes.items():
        assert p.stat().st_mtime_ns == before, f"{p} fue reescrito"


def test_force_rerun_archives_previous_directory_and_starts_fresh(
    tmp_path: Path, temp_repo: Path
):
    """Comprueba que force rerun archives previous directory and starts fresh."""
    runs_root = tmp_path / "runs"
    runner_first = _build_runner(
        runs_root=runs_root, repo=temp_repo, sb_cli_report={"resolved_ids": []}
    )
    runner_first.run()
    exp_root = runs_root / "integration-exp"
    sentinel = exp_root / "instances" / "_sentinel.txt"
    sentinel.write_text("first run", encoding="utf-8")

    runner_second = _build_runner(
        runs_root=runs_root,
        repo=temp_repo,
        sb_cli_report={"resolved_ids": []},
        force_rerun=True,
    )
    runner_second.run()

    assert not sentinel.exists()
    backups = list(runs_root.glob("integration-exp.bak-*"))
    assert backups, "force_rerun deberia archivar el directorio previo en .bak-*"


# --- Test 3: contaminacion detectada --------------------------------------------


def test_contamination_detected_when_agent_invokes_forbidden_test_id(
    tmp_path: Path, temp_repo: Path
):
    """Comprueba que contamination detected when agent invokes forbidden test id."""
    runner = _build_runner(
        runs_root=tmp_path / "runs",
        repo=temp_repo,
        sb_cli_report={"resolved_ids": []},
        attack_test_id="tests/test_a.py::test_foo",
    )
    runner.run()

    exp_root = tmp_path / "runs" / "integration-exp"
    robust_run_dir = next(
        (exp_root / "instances" / "repoA__pkg-1" / "robust_balanced").iterdir()
    )
    record = json.loads((robust_run_dir / "run_record.json").read_text(encoding="utf-8"))
    assert record["contamination_detected"] is True
    assert record["contamination_evidence"] is not None

    default_run_dir = next(
        (exp_root / "instances" / "repoA__pkg-1" / "default").iterdir()
    )
    default_record = json.loads(
        (default_run_dir / "run_record.json").read_text(encoding="utf-8")
    )
    assert default_record["contamination_detected"] is False
