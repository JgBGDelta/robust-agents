"""Tests unitarios del ``JsonlLoader``.

Cubre: carga normal, lineas malformadas (JSON invalido, campos faltantes),
fichero vacio, lineas en blanco intercaladas y duplicados de instance_id.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis.external_metrics_module.jsonl_loader import JsonlLoader


# ---------------------------------------------------------------------------
# Fabricas auxiliares
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, records: list[dict]) -> None:
    """Auxiliar interno: write jsonl."""
    lines = [json.dumps(r) for r in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _loader() -> JsonlLoader:
    """Auxiliar interno: loader."""
    return JsonlLoader()


# ---------------------------------------------------------------------------
# Carga normal
# ---------------------------------------------------------------------------


def test_loader_parses_valid_records(tmp_path: Path):
    """Comprueba que loader parses valid records."""
    p = tmp_path / "preds.jsonl"
    _write_jsonl(p, [
        {"instance_id": "django__django-001", "model_patch": "diff x", "model_name_or_path": "m"},
        {"instance_id": "astropy__astropy-002", "model_patch": "", "model_name_or_path": "m2"},
    ])
    records = _loader().load(p)
    assert len(records) == 2
    assert records[0].instance_id == "django__django-001"
    assert records[0].model_patch == "diff x"
    assert records[1].model_patch == ""


def test_loader_handles_missing_model_name(tmp_path: Path):
    """model_name_or_path es opcional: si falta queda None."""
    p = tmp_path / "preds.jsonl"
    p.write_text('{"instance_id": "a__a-1", "model_patch": "diff"}\n', encoding="utf-8")
    records = _loader().load(p)
    assert len(records) == 1
    assert records[0].model_name_or_path is None


# ---------------------------------------------------------------------------
# Lineas malformadas: deben descartarse sin abortar
# ---------------------------------------------------------------------------


def test_loader_discards_non_json_lines(tmp_path: Path):
    """Comprueba que loader discards non json lines."""
    p = tmp_path / "preds.jsonl"
    p.write_text(
        '{"instance_id": "a__a-1", "model_patch": "diff"}\n'
        "not-json-at-all\n"
        '{"instance_id": "b__b-2", "model_patch": "diff2"}\n',
        encoding="utf-8",
    )
    records = _loader().load(p)
    assert len(records) == 2


def test_loader_discards_lines_missing_instance_id(tmp_path: Path):
    """Comprueba que loader discards lines missing instance id."""
    p = tmp_path / "preds.jsonl"
    p.write_text(
        '{"model_patch": "diff"}\n'
        '{"instance_id": "good__good-1", "model_patch": "diff2"}\n',
        encoding="utf-8",
    )
    records = _loader().load(p)
    assert len(records) == 1
    assert records[0].instance_id == "good__good-1"


def test_loader_discards_lines_missing_model_patch(tmp_path: Path):
    """Comprueba que loader discards lines missing model patch."""
    p = tmp_path / "preds.jsonl"
    p.write_text(
        '{"instance_id": "bad__bad-1"}\n'
        '{"instance_id": "good__good-1", "model_patch": ""}\n',
        encoding="utf-8",
    )
    records = _loader().load(p)
    assert len(records) == 1
    assert records[0].instance_id == "good__good-1"


# ---------------------------------------------------------------------------
# Casos borde
# ---------------------------------------------------------------------------


def test_loader_empty_file_returns_empty_list(tmp_path: Path):
    """Comprueba que loader empty file returns empty list."""
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    records = _loader().load(p)
    assert records == []


def test_loader_blank_lines_ignored(tmp_path: Path):
    """Comprueba que loader blank lines ignored."""
    p = tmp_path / "preds.jsonl"
    p.write_text(
        "\n"
        '{"instance_id": "a__a-1", "model_patch": "diff"}\n'
        "\n"
        '{"instance_id": "b__b-2", "model_patch": "diff2"}\n'
        "\n",
        encoding="utf-8",
    )
    records = _loader().load(p)
    assert len(records) == 2


def test_loader_preserves_order(tmp_path: Path):
    """Comprueba que loader preserves order."""
    ids = [f"repo__repo-{i}" for i in range(5)]
    p = tmp_path / "preds.jsonl"
    _write_jsonl(p, [{"instance_id": iid, "model_patch": f"diff{i}"} for i, iid in enumerate(ids)])
    records = _loader().load(p)
    assert [r.instance_id for r in records] == ids
