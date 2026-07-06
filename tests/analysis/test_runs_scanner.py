"""Tests de `RunsScanner`: escaneo del arbol `runs/` y seleccion canonica.

Cubre el algoritmo de `especificacion_analisis.md` S6.3.1 sobre arboles
sinteticos: sin reintentos, con reintentos `-rN`, con carpetas invalidas
(sin `run_record.json`) y con el caso real observado de dos raices de hash
distintas para el mismo `(instance_id, agent_id)` (fallback Vertex/AI Studio).
"""

from __future__ import annotations

import json
from pathlib import Path

from analysis.consolidation_module.runs_scanner import RunsScanner


def _make_run_dir(root: Path, instance_id: str, agent_id: str, run_name: str) -> Path:
    """Auxiliar interno: make run dir."""
    run_dir = root / "instances" / instance_id / agent_id / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_record.json").write_text(json.dumps({"run_id": run_name}), encoding="utf-8")
    return run_dir


def test_scan_returns_empty_list_when_instances_dir_missing(tmp_path: Path):
    """Comprueba que scan returns empty list when instances dir missing."""
    result = RunsScanner().scan(tmp_path / "does-not-exist")
    assert result == []


def test_scan_single_run_no_retries(tmp_path: Path):
    """Comprueba que scan single run no retries."""
    _make_run_dir(tmp_path, "repo__repo-1", "default", "r-aaaaaaaa")

    result = RunsScanner().scan(tmp_path)

    assert len(result) == 1
    run = result[0]
    assert run.instance_id == "repo__repo-1"
    assert run.agent_id == "default"
    assert run.run_dir.name == "r-aaaaaaaa"
    assert run.run_sequence == 0
    assert run.retry_count == 0


def test_scan_ignores_folders_without_run_record(tmp_path: Path):
    """Comprueba que scan ignores folders without run record."""
    # Carpeta valida.
    _make_run_dir(tmp_path, "repo__repo-1", "default", "r-aaaaaaaa")
    # Carpeta sin run_record.json: debe ignorarse.
    empty_dir = tmp_path / "instances" / "repo__repo-1" / "default" / "r-bbbbbbbb"
    empty_dir.mkdir(parents=True)

    result = RunsScanner().scan(tmp_path)

    assert len(result) == 1
    assert result[0].run_dir.name == "r-aaaaaaaa"


def test_scan_picks_highest_retry_suffix_as_canonical(tmp_path: Path):
    """Comprueba que scan picks highest retry suffix as canonical."""
    _make_run_dir(tmp_path, "repo__repo-1", "default", "r-aaaaaaaa")
    _make_run_dir(tmp_path, "repo__repo-1", "default", "r-aaaaaaaa-r1")
    _make_run_dir(tmp_path, "repo__repo-1", "default", "r-aaaaaaaa-r2")

    result = RunsScanner().scan(tmp_path)

    assert len(result) == 1
    run = result[0]
    assert run.run_dir.name == "r-aaaaaaaa-r2"
    assert run.run_sequence == 2
    assert run.retry_count == 2


def test_scan_single_retry_has_sequence_one(tmp_path: Path):
    """Comprueba que scan single retry has sequence one."""
    _make_run_dir(tmp_path, "repo__repo-1", "default", "r-aaaaaaaa")
    _make_run_dir(tmp_path, "repo__repo-1", "default", "r-aaaaaaaa-r1")

    result = RunsScanner().scan(tmp_path)

    assert len(result) == 1
    assert result[0].run_sequence == 1
    assert result[0].retry_count == 1


def test_scan_different_hash_roots_produce_independent_rows(tmp_path: Path):
    """Caso real: fallback Vertex/AI Studio produce dos `run_id` distintos
    (hash distinto) para el mismo (instance_id, agent_id), sin relacion de
    reintento (`retry_of`). El algoritmo de agrupacion por raiz de hash los
    trata como dos runs logicos independientes, cada uno sin reintentos."""
    _make_run_dir(tmp_path, "astropy__astropy-1", "default", "r-87a4eb60")
    _make_run_dir(tmp_path, "astropy__astropy-1", "default", "r-eba0977d")

    result = RunsScanner().scan(tmp_path)

    assert len(result) == 2
    run_dir_names = {r.run_dir.name for r in result}
    assert run_dir_names == {"r-87a4eb60", "r-eba0977d"}
    assert all(r.retry_count == 0 for r in result)
    assert all(r.run_sequence == 0 for r in result)


def test_scan_multiple_agents_and_instances(tmp_path: Path):
    """Comprueba que scan multiple agents and instances."""
    _make_run_dir(tmp_path, "repo__repo-1", "default", "r-aaaaaaaa")
    _make_run_dir(tmp_path, "repo__repo-1", "robust_strict", "r-bbbbbbbb")
    _make_run_dir(tmp_path, "repo__repo-2", "default", "r-cccccccc")

    result = RunsScanner().scan(tmp_path)

    combos = {(r.instance_id, r.agent_id) for r in result}
    assert combos == {
        ("repo__repo-1", "default"),
        ("repo__repo-1", "robust_strict"),
        ("repo__repo-2", "default"),
    }


def test_scan_ignores_unrecognized_folder_names(tmp_path: Path):
    """Comprueba que scan ignores unrecognized folder names."""
    _make_run_dir(tmp_path, "repo__repo-1", "default", "r-aaaaaaaa")
    weird_dir = tmp_path / "instances" / "repo__repo-1" / "default" / "not-a-run-dir"
    weird_dir.mkdir(parents=True)
    (weird_dir / "run_record.json").write_text("{}", encoding="utf-8")

    result = RunsScanner().scan(tmp_path)

    assert len(result) == 1
    assert result[0].run_dir.name == "r-aaaaaaaa"
