"""Tests de `ConsolidationModule`: fachada + test e2e.

Estructura:
- Tests de `consolidate` sobre arboles `runs/` sinteticos: reintentos,
  evaluacion pendiente (PA6), traza `robust_agent`, JSON corrupto.
- Tests de `write_csv`: columnas y contenido del CSV escrito.
- Test e2e completo: arbol `runs/` sintetico con varios agentes +
  `extracted_metrics.jsonl` externo real -> ambos CSV.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis.consolidation_module import ConsolidationModule
from analysis.external_metrics_module import ExternalMetricsRecord


def _write_json(path: Path, data: dict) -> None:
    """Auxiliar interno: write json."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _run_record(instance_id: str, agent_id: str, run_id: str, **overrides) -> dict:
    """Auxiliar interno: run record."""
    base = {
        "run_id": run_id,
        "instance_id": instance_id,
        "status": "completed",
        "exit_status": "Submitted",
        "cost_usd": 0.01,
        "duration_seconds": 5.0,
        "started_at": 100.0,
        "ended_at": 105.0,
        "error": None,
        "model_patch_path": f"instances/{instance_id}/{agent_id}/{run_id}/model.patch",
        "trajectory_path": f"instances/{instance_id}/{agent_id}/{run_id}/trajectory.traj.json",
        "agent_run_config": {
            "agent_id": agent_id,
            "model_id": "gemini/gemini-2.5-flash",
            "dataset_slice_size": None,
        },
        "preds_entry": {
            "model_patch": (
                "diff --git a/src/foo.py b/src/foo.py\n"
                "--- a/src/foo.py\n"
                "+++ b/src/foo.py\n"
                "@@ -1,1 +1,1 @@\n"
                "-old\n"
                "+new\n"
            )
        },
    }
    base.update(overrides)
    return base


def _evaluation_result(instance_id: str, resolved: bool = True) -> dict:
    """Auxiliar interno: evaluation result."""
    return {
        "functional": {"status": "evaluated", "resolved": resolved},
        "extended": {
            "run_metrics": {"steps_used": 3, "termination": "model_submitted"},
            "patch_metrics": {
                "files_modified": ["src/foo.py"],
                "lines_added": 1,
                "lines_deleted": 1,
                "hunks_count": 1,
                "files_intersection_with_gold": ["src/foo.py"],
                "files_unexpected": [],
                "files_missing_vs_gold": [],
                "jaccard_files": 1.0,
                "jaccard_lines": 1.0,
            },
        },
    }


def _make_own_run(
    runs_root: Path,
    instance_id: str,
    agent_id: str,
    run_id: str,
    *,
    with_evaluation: bool = True,
    with_trajectory: bool = False,
    robust_agent: bool = False,
) -> Path:
    """Auxiliar interno: make own run."""
    run_dir = runs_root / "instances" / instance_id / agent_id / run_id
    _write_json(run_dir / "run_record.json", _run_record(instance_id, agent_id, run_id))
    if with_evaluation:
        _write_json(run_dir / "evaluation_result.json", _evaluation_result(instance_id))
    if with_trajectory:
        trajectory: dict = {
            "messages": [{"role": "assistant", "extra": {"response": {"usage": {
                "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15
            }}}}],
            "info": {"model_stats": {"api_calls": 3}},
        }
        if robust_agent:
            trajectory["robust_agent"] = {
                "termination": "model_submitted",
                "episode_state_final": {
                    "signals_history": [
                        {"step_index": 1, "uncertainty": {"score": 0.1, "level": "low", "components": {}, "evidence": {}}},
                        {"step_index": 1, "structural_risk": {"score": 0.0, "level": "low", "metrics": {}, "diff_summary": {"files_touched": []}}},
                    ],
                    "decisions_history": [
                        {"step_index": 1, "decision": {"action": "PROCEED", "policy_snapshot": {}}},
                    ],
                },
                "steps": [
                    {
                        "step_id": 1,
                        "uncertainty": {"score": 0.1, "level": "low"},
                        "structural_risk": {"score": 0.0, "level": "low"},
                        "decision": {"action": "PROCEED"},
                        "validation_outcome": None,
                    }
                ],
            }
        _write_json(run_dir / "trajectory.traj.json", trajectory)
    return run_dir


def _make_external_jsonl(tmp_path: Path, source_id: str, records: list[ExternalMetricsRecord]) -> Path:
    """Auxiliar interno: make external jsonl."""
    out = tmp_path / source_id / "extracted_metrics.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r.to_dict()) for r in records]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# consolidate: runs propios
# ---------------------------------------------------------------------------


def test_consolidate_single_own_run(tmp_path: Path):
    """Comprueba que consolidate single own run."""
    runs_root = tmp_path / "runs" / "exp"
    _make_own_run(runs_root, "repo__repo-1", "default", "r-aaaaaaaa")

    run_rows, step_rows = ConsolidationModule().consolidate(runs_root, external_metrics_paths=[])

    assert len(run_rows) == 1
    assert run_rows[0].instance_id == "repo__repo-1"
    assert run_rows[0].source_type == "own"
    assert run_rows[0].resolved is True
    assert step_rows == []


def test_consolidate_picks_canonical_run_among_retries(tmp_path: Path):
    """Comprueba que consolidate picks canonical run among retries."""
    runs_root = tmp_path / "runs" / "exp"
    _make_own_run(runs_root, "repo__repo-1", "default", "r-aaaaaaaa", with_evaluation=False)
    _make_own_run(runs_root, "repo__repo-1", "default", "r-aaaaaaaa-r1")

    run_rows, _ = ConsolidationModule().consolidate(runs_root, external_metrics_paths=[])

    assert len(run_rows) == 1
    assert run_rows[0].run_id == "r-aaaaaaaa-r1"
    assert run_rows[0].run_sequence == 1
    assert run_rows[0].retry_count == 1


def test_consolidate_degrades_cleanly_without_evaluation(tmp_path: Path):
    """PA6: fila incluida con campos de run_record.json aunque no haya evaluacion."""
    runs_root = tmp_path / "runs" / "exp"
    _make_own_run(runs_root, "repo__repo-1", "default", "r-aaaaaaaa", with_evaluation=False)

    run_rows, _ = ConsolidationModule().consolidate(runs_root, external_metrics_paths=[])

    assert len(run_rows) == 1
    row = run_rows[0]
    assert row.run_status == "completed"
    assert row.resolved is None
    assert row.evaluable is None


def test_consolidate_ignores_run_without_run_record(tmp_path: Path):
    """Comprueba que consolidate ignores run without run record."""
    runs_root = tmp_path / "runs" / "exp"
    empty_dir = runs_root / "instances" / "repo__repo-1" / "default" / "r-aaaaaaaa"
    empty_dir.mkdir(parents=True)

    run_rows, _ = ConsolidationModule().consolidate(runs_root, external_metrics_paths=[])
    assert run_rows == []


def test_consolidate_handles_corrupt_evaluation_json(tmp_path: Path):
    """Comprueba que consolidate handles corrupt evaluation json."""
    runs_root = tmp_path / "runs" / "exp"
    run_dir = _make_own_run(runs_root, "repo__repo-1", "default", "r-aaaaaaaa", with_evaluation=False)
    (run_dir / "evaluation_result.json").write_text("{not valid json", encoding="utf-8")

    run_rows, _ = ConsolidationModule().consolidate(runs_root, external_metrics_paths=[])

    assert len(run_rows) == 1
    assert run_rows[0].resolved is None
    assert run_rows[0].evaluation_path is None


def test_consolidate_robust_agent_run_produces_step_rows(tmp_path: Path):
    """Comprueba que consolidate robust agent run produces step rows."""
    runs_root = tmp_path / "runs" / "exp"
    _make_own_run(
        runs_root, "repo__repo-1", "robust_balanced", "r-bbbbbbbb",
        with_trajectory=True, robust_agent=True,
    )

    run_rows, step_rows = ConsolidationModule().consolidate(runs_root, external_metrics_paths=[])

    assert len(run_rows) == 1
    assert run_rows[0].uncertainty_mean == 0.1
    assert run_rows[0].prompt_tokens == 10
    assert len(step_rows) == 1
    assert step_rows[0].instance_id == "repo__repo-1"


def test_consolidate_default_agent_trajectory_has_no_step_rows(tmp_path: Path):
    """`default` tiene tokens pero no bloque `robust_agent`: sin filas de paso."""
    runs_root = tmp_path / "runs" / "exp"
    _make_own_run(runs_root, "repo__repo-1", "default", "r-aaaaaaaa", with_trajectory=True, robust_agent=False)

    run_rows, step_rows = ConsolidationModule().consolidate(runs_root, external_metrics_paths=[])

    assert run_rows[0].prompt_tokens == 10
    assert run_rows[0].uncertainty_mean is None
    assert step_rows == []


def test_consolidate_multiple_instances_and_agents(tmp_path: Path):
    """Comprueba que consolidate multiple instances and agents."""
    runs_root = tmp_path / "runs" / "exp"
    _make_own_run(runs_root, "repo__repo-1", "default", "r-aaaaaaaa")
    _make_own_run(runs_root, "repo__repo-1", "robust_strict", "r-bbbbbbbb")
    _make_own_run(runs_root, "repo__repo-2", "default", "r-cccccccc")

    run_rows, _ = ConsolidationModule().consolidate(runs_root, external_metrics_paths=[])
    assert len(run_rows) == 3


# ---------------------------------------------------------------------------
# consolidate: agentes externos
# ---------------------------------------------------------------------------


def test_consolidate_includes_external_records(tmp_path: Path):
    """Comprueba que consolidate includes external records."""
    external_records = [
        ExternalMetricsRecord(
            instance_id="repo__repo-1",
            source_id="agentless_v1.5",
            agent_id="agentless_v1.5",
            model_id="claude-3-5-sonnet-20241022",
            model_patch="diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n",
            matched=True,
            patch_metrics={
                "files_modified": ["x.py"], "lines_added": 1, "lines_deleted": 1, "hunks_count": 1,
                "files_intersection_with_gold": ["x.py"], "files_unexpected": [], "files_missing_vs_gold": [],
                "jaccard_files": 1.0, "jaccard_lines": 1.0,
            },
            resolved=None,
            resolved_source=None,
        )
    ]
    external_path = _make_external_jsonl(tmp_path, "agentless_v1.5", external_records)

    run_rows, _ = ConsolidationModule().consolidate(tmp_path / "runs" / "exp", external_metrics_paths=[external_path])

    assert len(run_rows) == 1
    assert run_rows[0].source_type == "external"
    assert run_rows[0].agent_id == "agentless_v1.5"
    assert run_rows[0].sample_group == "external"


def test_consolidate_combines_own_and_external_rows(tmp_path: Path):
    """Comprueba que consolidate combines own and external rows."""
    runs_root = tmp_path / "runs" / "exp"
    _make_own_run(runs_root, "repo__repo-1", "default", "r-aaaaaaaa")

    external_records = [
        ExternalMetricsRecord(
            instance_id="repo__repo-1", source_id="agentless_v1.5", agent_id="agentless_v1.5",
            model_id="claude-3-5-sonnet-20241022", model_patch="diff", matched=True,
            patch_metrics={}, resolved=None, resolved_source=None,
        )
    ]
    external_path = _make_external_jsonl(tmp_path, "agentless_v1.5", external_records)

    run_rows, _ = ConsolidationModule().consolidate(runs_root, external_metrics_paths=[external_path])

    assert len(run_rows) == 2
    source_types = {r.source_type for r in run_rows}
    assert source_types == {"own", "external"}


def test_consolidate_missing_external_file_is_skipped(tmp_path: Path):
    """Comprueba que consolidate missing external file is skipped."""
    run_rows, _ = ConsolidationModule().consolidate(
        tmp_path / "runs" / "exp", external_metrics_paths=[tmp_path / "does-not-exist.jsonl"]
    )
    assert run_rows == []


# ---------------------------------------------------------------------------
# write_csv
# ---------------------------------------------------------------------------


def test_write_csv_creates_both_files_with_headers(tmp_path: Path):
    """Comprueba que write csv creates both files with headers."""
    runs_root = tmp_path / "runs" / "exp"
    _make_own_run(
        runs_root, "repo__repo-1", "robust_balanced", "r-bbbbbbbb",
        with_trajectory=True, robust_agent=True,
    )
    module = ConsolidationModule()
    run_rows, step_rows = module.consolidate(runs_root, external_metrics_paths=[])

    output_dir = tmp_path / "analysis"
    runs_csv, steps_csv = module.write_csv(run_rows, step_rows, output_dir)

    assert runs_csv.exists()
    assert steps_csv.exists()

    import csv as csv_module

    with runs_csv.open(encoding="utf-8") as f:
        reader = csv_module.DictReader(f)
        rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["instance_id"] == "repo__repo-1"

    with steps_csv.open(encoding="utf-8") as f:
        reader = csv_module.DictReader(f)
        rows = list(reader)
    assert len(rows) == 1


def test_write_csv_writes_header_only_when_no_step_rows(tmp_path: Path):
    """Comprueba que write csv writes header only when no step rows."""
    runs_root = tmp_path / "runs" / "exp"
    _make_own_run(runs_root, "repo__repo-1", "default", "r-aaaaaaaa")
    module = ConsolidationModule()
    run_rows, step_rows = module.consolidate(runs_root, external_metrics_paths=[])

    _, steps_csv = module.write_csv(run_rows, step_rows, tmp_path / "analysis")
    content = steps_csv.read_text(encoding="utf-8").strip().splitlines()
    assert len(content) == 1  # solo cabecera


def test_write_csv_is_idempotent(tmp_path: Path):
    """Comprueba que write csv is idempotent."""
    runs_root = tmp_path / "runs" / "exp"
    _make_own_run(runs_root, "repo__repo-1", "default", "r-aaaaaaaa")
    module = ConsolidationModule()
    run_rows, step_rows = module.consolidate(runs_root, external_metrics_paths=[])

    output_dir = tmp_path / "analysis"
    module.write_csv(run_rows, step_rows, output_dir)
    runs_csv, steps_csv = module.write_csv(run_rows, step_rows, output_dir)
    assert runs_csv.exists()
    assert steps_csv.exists()


# ---------------------------------------------------------------------------
# Test e2e: arbol runs/ sintetico completo + externos
# ---------------------------------------------------------------------------


def test_e2e_full_pipeline_synthetic_tree_with_external(tmp_path: Path):
    """Arbol runs/ con 2 instancias x 3 agentes (uno robust_agent), reintento,
    evaluacion pendiente en uno, mas una fuente externa -> ambos CSV completos."""
    runs_root = tmp_path / "runs" / "exp"

    # Instancia 1: default (ok), robust_balanced (con traza robust_agent).
    _make_own_run(runs_root, "django__django-1", "default", "r-11111111")
    _make_own_run(
        runs_root, "django__django-1", "robust_balanced", "r-22222222",
        with_trajectory=True, robust_agent=True,
    )

    # Instancia 2: default con reintento (canonico = -r1), evaluacion pendiente.
    _make_own_run(runs_root, "astropy__astropy-2", "default", "r-33333333", with_evaluation=False)
    _make_own_run(runs_root, "astropy__astropy-2", "default", "r-33333333-r1", with_evaluation=False)

    # Fuente externa: agentless_v1.5 con una instancia emparejada.
    external_records = [
        ExternalMetricsRecord(
            instance_id="django__django-1",
            source_id="agentless_v1.5",
            agent_id="agentless_v1.5",
            model_id="claude-3-5-sonnet-20241022",
            model_patch="diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n",
            matched=True,
            patch_metrics={
                "files_modified": ["x.py"], "lines_added": 1, "lines_deleted": 1, "hunks_count": 1,
                "files_intersection_with_gold": ["x.py"], "files_unexpected": [], "files_missing_vs_gold": [],
                "jaccard_files": 1.0, "jaccard_lines": 1.0,
            },
            resolved=None,
            resolved_source=None,
        ),
        ExternalMetricsRecord(
            instance_id="astropy__astropy-2",
            source_id="agentless_v1.5",
            agent_id="agentless_v1.5",
            model_id="claude-3-5-sonnet-20241022",
            model_patch=None,
            matched=False,
            patch_metrics={
                "files_modified": [], "lines_added": 0, "lines_deleted": 0, "hunks_count": 0,
                "files_intersection_with_gold": None, "files_unexpected": None, "files_missing_vs_gold": None,
                "jaccard_files": None, "jaccard_lines": None,
            },
            resolved=None,
            resolved_source=None,
        ),
    ]
    external_path = _make_external_jsonl(tmp_path, "agentless_v1.5", external_records)

    module = ConsolidationModule()
    run_rows, step_rows = module.consolidate(runs_root, external_metrics_paths=[external_path])

    # 3 runs propios (uno por instance x agent, reintento colapsado) + 2 externos.
    assert len(run_rows) == 5

    own_rows = [r for r in run_rows if r.source_type == "own"]
    external_rows = [r for r in run_rows if r.source_type == "external"]
    assert len(own_rows) == 3
    assert len(external_rows) == 2

    retry_row = next(r for r in own_rows if r.instance_id == "astropy__astropy-2")
    assert retry_row.run_id == "r-33333333-r1"
    assert retry_row.retry_count == 1
    assert retry_row.resolved is None  # evaluacion pendiente

    robust_row = next(r for r in own_rows if r.agent_id == "robust_balanced")
    assert robust_row.uncertainty_mean is not None
    assert len(step_rows) == 1

    matched_external = next(r for r in external_rows if r.instance_id == "django__django-1")
    assert matched_external.patch_available is True
    unmatched_external = next(r for r in external_rows if r.instance_id == "astropy__astropy-2")
    assert unmatched_external.patch_available is False

    output_dir = tmp_path / "analysis" / "exp"
    runs_csv, steps_csv = module.write_csv(run_rows, step_rows, output_dir)

    import csv as csv_module

    with runs_csv.open(encoding="utf-8") as f:
        csv_rows = list(csv_module.DictReader(f))
    assert len(csv_rows) == 5
    assert {r["source_type"] for r in csv_rows} == {"own", "external"}

    with steps_csv.open(encoding="utf-8") as f:
        csv_steps = list(csv_module.DictReader(f))
    assert len(csv_steps) == 1
