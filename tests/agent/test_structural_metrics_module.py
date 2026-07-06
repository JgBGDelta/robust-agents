"""Tests unitarios del modulo de metricas estructurales."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agent.config import StructuralMetricsConfig
from agent.episode_state import EpisodeState, Profile
from agent.structural_metrics_module import StructuralMetricsModule, StructuralRiskResult


def _run_git(repo_path: Path, *args: str) -> str:
    """Auxiliar interno: run git."""
    result = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def _init_clean_git_repo(tmp_path: Path) -> Path:
    """Auxiliar interno: init clean git repo."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _run_git(repo_path, "init")
    _run_git(repo_path, "config", "user.email", "test@example.com")
    _run_git(repo_path, "config", "user.name", "test")
    (repo_path / "main.py").write_text("print('hello')\n", encoding="utf-8")
    _run_git(repo_path, "add", ".")
    _run_git(repo_path, "commit", "-m", "init")
    return repo_path


def _new_module(
    repo_path: Path,
    *,
    env: Any | None = None,
    logical_cwd: str | None = None,
) -> tuple[StructuralMetricsModule, EpisodeState]:
    """Auxiliar interno: new module."""
    episode_state = EpisodeState(profile=Profile(name="balanced"))
    module = StructuralMetricsModule(
        config=StructuralMetricsConfig(),
        episode_state=episode_state,
        repo_path=logical_cwd or repo_path,
        env=env,
    )
    return module, episode_state


class _DockerLikeEnvStub:
    """Simula un entorno con cwd lógico `/testbed` y repo real en el host."""

    def __init__(self, host_repo: Path, *, logical_cwd: str = "/testbed") -> None:
        """Inicializa `_DockerLikeEnvStub`."""
        self._host_repo = host_repo
        self.config = type("Cfg", (), {"cwd": logical_cwd})()

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict:
        """Ejecuta el comando simulado del entorno."""
        del timeout
        command = action.get("command", "")
        actual_cwd = str(self._host_repo) if cwd in {"", self.config.cwd} else cwd
        result = subprocess.run(
            command,
            cwd=actual_cwd,
            shell=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        return {
            "output": result.stdout,
            "returncode": result.returncode,
            "exception_info": "",
        }


def test_initialize_baseline_sets_git_refs(tmp_path: Path):
    """Comprueba que initialize baseline sets git refs."""
    repo_path = _init_clean_git_repo(tmp_path)
    module, episode_state = _new_module(repo_path)

    module.initialize_baseline()
    expected = _run_git(repo_path, "rev-parse", "HEAD")

    assert episode_state.git_refs["initial"] == expected
    assert episode_state.git_refs["previous"] == expected
    assert episode_state.git_refs["current"] == expected


def test_initialize_baseline_succeeds_when_repo_dirty(tmp_path: Path):
    """Un repo con ficheros pre-modificados (imagenes SWE-bench) NO bloquea el agente.

    Ademas, el baseline debe ser un stash ref (no HEAD) para que los diffs
    de los pasos reflejen solo los cambios del agente, no los pre-existentes.
    """
    repo_path = _init_clean_git_repo(tmp_path)
    head_ref = subprocess.run(
        "git rev-parse HEAD", cwd=repo_path, shell=True, capture_output=True, text=True
    ).stdout.strip()
    (repo_path / "main.py").write_text("print('dirty')\n", encoding="utf-8")
    module, episode_state = _new_module(repo_path)

    module.initialize_baseline()

    initial = episode_state.git_refs["initial"]
    assert initial != ""
    # El baseline debe ser un stash ref distinto de HEAD para aislar los cambios del agente
    assert initial != head_ref, "Con repo sucio el baseline debe ser un stash ref, no HEAD"


def test_evaluate_returns_structural_risk_and_updates_state(tmp_path: Path):
    """Comprueba que evaluate returns structural risk and updates state."""
    repo_path = _init_clean_git_repo(tmp_path)
    module, episode_state = _new_module(repo_path)
    module.initialize_baseline()

    (repo_path / "main.py").write_text("print('hello')\nprint('change')\n", encoding="utf-8")
    result = module.evaluate(action_outputs=[{"returncode": 0, "output": "ok", "exception_info": ""}])

    assert isinstance(result, StructuralRiskResult)
    assert result.level in {"low", "medium", "high"}
    assert 0.0 <= result.score <= 1.0
    assert result.metrics["files_changed"] >= 1
    assert result.diff_summary["surface_signature"] != ""
    assert len(episode_state.signals_history) == 1
    assert episode_state.errors == []
    assert episode_state.git_refs["previous"] == episode_state.git_refs["current"]


def test_evaluate_degrades_cleanly_when_refs_are_missing(tmp_path: Path):
    """Comprueba que evaluate degrades cleanly when refs are missing."""
    repo_path = _init_clean_git_repo(tmp_path)
    module, episode_state = _new_module(repo_path)

    result = module.evaluate(action_outputs=[{"returncode": 0, "output": "", "exception_info": ""}])

    assert result.level == "medium"
    assert result.score == pytest.approx(0.5)
    assert result.metrics["files_changed"] == 0
    assert "error" in result.evidence
    assert len(episode_state.errors) == 1
    assert episode_state.errors[0]["component"] == "structural_metrics"
    assert len(episode_state.signals_history) == 1


def test_cleanup_is_safe_noop(tmp_path: Path):
    """Comprueba que cleanup is safe noop."""
    repo_path = _init_clean_git_repo(tmp_path)
    module, _ = _new_module(repo_path)
    module.initialize_baseline()

    assert module.cleanup() is None


def test_structural_risk_result_is_json_serializable():
    """Comprueba que structural risk result is json serializable."""
    result = StructuralRiskResult(
        score=0.42,
        level="medium",
        metrics={"files_changed": 2, "files_changed_ratio": 0.1},
        diff_summary={"files_touched": ["a.py"], "surface_signature": "abc123"},
        cost_overhead=0.0,
        evidence={"omitted_components": []},
    )
    payload = result.to_dict()

    assert payload["score"] == 0.42
    assert payload["level"] == "medium"
    assert payload["metrics"]["files_changed"] == 2
    assert payload["diff_summary"]["surface_signature"] == "abc123"
    json.dumps(payload)


def test_initialize_baseline_via_environment_with_logical_testbed_cwd(tmp_path: Path):
    """PR1 y baseline funcionan cuando el repo vive en un entorno aislado (Docker)."""
    repo_path = _init_clean_git_repo(tmp_path)
    env = _DockerLikeEnvStub(repo_path, logical_cwd="/testbed")
    module, episode_state = _new_module(repo_path, env=env, logical_cwd="/testbed")

    module.initialize_baseline()
    expected = _run_git(repo_path, "rev-parse", "HEAD")

    assert episode_state.git_refs["initial"] == expected


def test_evaluate_via_environment_updates_structural_risk(tmp_path: Path):
    """Comprueba que evaluate via environment updates structural risk."""
    repo_path = _init_clean_git_repo(tmp_path)
    env = _DockerLikeEnvStub(repo_path, logical_cwd="/testbed")
    module, episode_state = _new_module(repo_path, env=env, logical_cwd="/testbed")
    module.initialize_baseline()

    (repo_path / "main.py").write_text("print('hello')\nprint('change')\n", encoding="utf-8")
    result = module.evaluate(action_outputs=[{"returncode": 0, "output": "ok", "exception_info": ""}])

    assert result.metrics["files_changed"] >= 1
    assert episode_state.errors == []
