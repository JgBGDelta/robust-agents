"""Tests del ``ExternalMetricsModule``: interfaz publica + test e2e.

Estructura:
- Tests de ``extract_metrics``: logica de emparejamiento y calculo de metricas.
- Tests de ``write_output`` / ``load_patches`` granulares.
- Test e2e con fixture completo de una instancia: simula el pipeline completo
  desde el JSONL de origen hasta el fichero ``extracted_metrics.jsonl`` de salida.

Sin red real: descargas mockeadas. Sin HuggingFace: gold_patch_lookup es un stub.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from analysis.external_metrics_module import (
    ExternalMetricsConfig,
    ExternalMetricsModule,
    ExternalMetricsRecord,
    ExternalPatchRecord,
    ExternalSourceConfig,
)


# ---------------------------------------------------------------------------
# Fabricas auxiliares
# ---------------------------------------------------------------------------

_GOLD_PATCH = (
    "diff --git a/foo.py b/foo.py\n"
    "--- a/foo.py\n"
    "+++ b/foo.py\n"
    "@@ -1,2 +1,2 @@\n"
    " context\n"
    "-old\n"
    "+new\n"
)

_MODEL_PATCH = (
    "diff --git a/foo.py b/foo.py\n"
    "--- a/foo.py\n"
    "+++ b/foo.py\n"
    "@@ -1,2 +1,2 @@\n"
    " context\n"
    "-old\n"
    "+new\n"
)


def _source_config(tmp_path: Path, source_id: str = "test_agent") -> ExternalSourceConfig:
    """Auxiliar interno: source config."""
    return ExternalSourceConfig(
        source_id=source_id,
        release_url="https://example.com/preds.jsonl",
        archive_member=None,
        local_cache_dir=tmp_path / source_id,
        default_agent_id=source_id,
    )


def _module() -> ExternalMetricsModule:
    """Auxiliar interno: module."""
    return ExternalMetricsModule(ExternalMetricsConfig(max_retries=1))


def _patch_record(instance_id: str, patch_text: str = _MODEL_PATCH) -> ExternalPatchRecord:
    """Auxiliar interno: patch record."""
    return ExternalPatchRecord(
        instance_id=instance_id,
        model_patch=patch_text,
        model_name_or_path="gpt-4o",
    )


def _gold_lookup(gold: dict[str, str]) -> callable:
    """Auxiliar interno: gold lookup."""
    return lambda iid: gold.get(iid)


# ---------------------------------------------------------------------------
# extract_metrics: logica de emparejamiento y calculo
# ---------------------------------------------------------------------------


def test_extract_metrics_matched_produces_patch_metrics():
    """Comprueba que extract metrics matched produces patch metrics."""
    patches = [_patch_record("django__django-001")]
    records = _module().extract_metrics(
        patches,
        instance_ids=["django__django-001"],
        gold_patch_lookup=_gold_lookup({"django__django-001": _GOLD_PATCH}),
    )
    assert len(records) == 1
    r = records[0]
    assert r.matched is True
    assert r.model_patch == _MODEL_PATCH
    assert r.patch_metrics["lines_added"] >= 0
    assert r.patch_metrics["files_modified"] == ["foo.py"]
    assert isinstance(r.patch_metrics["jaccard_files"], float)


def test_extract_metrics_unmatched_produces_empty_metrics():
    """Comprueba que extract metrics unmatched produces empty metrics."""
    records = _module().extract_metrics(
        patches=[],
        instance_ids=["missing__missing-99"],
        gold_patch_lookup=_gold_lookup({}),
    )
    assert len(records) == 1
    r = records[0]
    assert r.matched is False
    assert r.model_patch is None
    # PatchMetricsExtractor devuelve ceros para parche vacio.
    assert r.patch_metrics["lines_added"] == 0
    assert r.patch_metrics["hunks_count"] == 0


def test_extract_metrics_without_gold_patch_has_null_comparatives():
    """Comprueba que extract metrics without gold patch has null comparatives."""
    patches = [_patch_record("a__a-1")]
    records = _module().extract_metrics(
        patches,
        instance_ids=["a__a-1"],
        gold_patch_lookup=_gold_lookup({}),  # sin gold
    )
    r = records[0]
    assert r.matched is True
    assert r.patch_metrics["jaccard_files"] is None
    assert r.patch_metrics["jaccard_lines"] is None
    # Las metricas propias si deben estar disponibles.
    assert r.patch_metrics["lines_added"] >= 0


def test_extract_metrics_preserves_instance_id_order():
    """Comprueba que extract metrics preserves instance id order."""
    ids = [f"repo__repo-{i}" for i in range(5)]
    patches = [_patch_record(iid) for iid in ids]
    records = _module().extract_metrics(
        patches, instance_ids=ids, gold_patch_lookup=_gold_lookup({})
    )
    assert [r.instance_id for r in records] == ids


def test_extract_metrics_empty_patch_produces_zero_metrics():
    """Comprueba que extract metrics empty patch produces zero metrics."""
    patches = [ExternalPatchRecord("a__a-1", model_patch="", model_name_or_path=None)]
    records = _module().extract_metrics(
        patches,
        instance_ids=["a__a-1"],
        gold_patch_lookup=_gold_lookup({}),
    )
    r = records[0]
    assert r.matched is True
    # Parche vacio: PatchMetricsExtractor devuelve ceros.
    assert r.patch_metrics["lines_added"] == 0


def test_extract_metrics_resolved_is_null_by_default():
    """resolved=None en todos los registros (version inicial no lo rellena)."""
    patches = [_patch_record("a__a-1")]
    records = _module().extract_metrics(
        patches, instance_ids=["a__a-1"], gold_patch_lookup=_gold_lookup({})
    )
    assert records[0].resolved is None
    assert records[0].resolved_source is None


# ---------------------------------------------------------------------------
# write_output: serializacion / deserializacion
# ---------------------------------------------------------------------------


def test_write_output_creates_jsonl_file(tmp_path: Path):
    """Comprueba que write output creates jsonl file."""
    source = _source_config(tmp_path)
    records = [
        ExternalMetricsRecord(
            instance_id="a__a-1",
            source_id="test_agent",
            agent_id="test_agent",
            model_id="gpt-4o",
            model_patch=_MODEL_PATCH,
            matched=True,
            patch_metrics={"lines_added": 1, "lines_deleted": 1},
            resolved=None,
            resolved_source=None,
        )
    ]
    module = _module()
    out = module.write_output(records, source)

    assert out.exists()
    lines = [l for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    loaded = json.loads(lines[0])
    assert loaded["instance_id"] == "a__a-1"
    assert loaded["matched"] is True


def test_write_output_is_idempotent(tmp_path: Path):
    """Relanzar write_output sobreescribe sin error."""
    source = _source_config(tmp_path)
    records = [
        ExternalMetricsRecord(
            instance_id="a__a-1",
            source_id="test_agent",
            agent_id="test_agent",
            model_id=None,
            model_patch=None,
            matched=False,
            patch_metrics={},
            resolved=None,
            resolved_source=None,
        )
    ]
    module = _module()
    module.write_output(records, source)
    out = module.write_output(records, source)
    assert out.exists()


def test_write_output_roundtrip_via_load_jsonl(tmp_path: Path):
    """Los registros escritos se pueden leer de vuelta sin perdida."""
    source = _source_config(tmp_path)
    original = ExternalMetricsRecord(
        instance_id="django__django-999",
        source_id="test_agent",
        agent_id="test_agent",
        model_id="claude-x",
        model_patch=_MODEL_PATCH,
        matched=True,
        patch_metrics={"lines_added": 3, "jaccard_files": 0.75},
        resolved=None,
        resolved_source=None,
    )
    module = _module()
    out = module.write_output([original], source)
    loaded = ExternalMetricsRecord.load_jsonl(out)

    assert len(loaded) == 1
    r = loaded[0]
    assert r.instance_id == original.instance_id
    assert r.model_id == original.model_id
    assert r.patch_metrics == original.patch_metrics
    assert r.matched is True


# ---------------------------------------------------------------------------
# load_patches: invoca ensure_downloaded y parsea
# ---------------------------------------------------------------------------


def test_load_jsonl_applies_source_defaults_for_known_sources(tmp_path: Path):
    """JSONL antiguos sin metadatos de fuente se enriquecen al cargar."""
    path = tmp_path / "agentless.jsonl"
    path.write_text(
        json.dumps(
            {
                "instance_id": "a__a-1",
                "source_id": "agentless_v1.5",
                "agent_id": "agentless_v1.5",
                "model_id": "agentless",
                "model_patch": _MODEL_PATCH,
                "matched": True,
                "patch_metrics": {"lines_added": 1},
                "resolved": None,
                "resolved_source": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    loaded = ExternalMetricsRecord.load_jsonl(path)[0]
    assert loaded.external_release == "v1.5.0"
    assert loaded.external_source_name == "Agentless"


def test_load_patches_returns_records_from_jsonl(tmp_path: Path):
    """Comprueba que load patches returns records from jsonl."""
    source = _source_config(tmp_path)
    sample_jsonl = b'{"instance_id": "a__a-1", "model_patch": "diff", "model_name_or_path": "m"}\n'
    module = _module()
    with patch.object(module._downloader, "_fetch", return_value=sample_jsonl):
        patches = module.load_patches(source)
    assert len(patches) == 1
    assert patches[0].instance_id == "a__a-1"


# ---------------------------------------------------------------------------
# Test e2e con fixture de una instancia real
# ---------------------------------------------------------------------------


def test_e2e_single_instance(tmp_path: Path):
    """Pipeline completo: descarga simulada -> parseo -> metricas -> JSONL de salida.

    Simula el flujo exacto que ejecutaria AnalysisRunner para Agentless v1.5
    sobre una unica instancia del experimento.
    """
    source = ExternalSourceConfig(
        source_id="agentless_v1.5",
        release_url="https://github.com/example/agentless/releases/download/v1.5.0/preds.zip",
        archive_member=None,
        local_cache_dir=tmp_path / "agentless_v1.5",
        default_agent_id="agentless_v1.5",
    )

    # JSONL de predicciones simulando el formato real de Agentless.
    instance_id = "astropy__astropy-12907"
    model_patch = (
        "diff --git a/astropy/modeling/separable.py b/astropy/modeling/separable.py\n"
        "--- a/astropy/modeling/separable.py\n"
        "+++ b/astropy/modeling/separable.py\n"
        "@@ -300,7 +300,7 @@\n"
        " context\n"
        "-old_line\n"
        "+new_line\n"
        " more context\n"
    )
    gold_patch = (
        "diff --git a/astropy/modeling/separable.py b/astropy/modeling/separable.py\n"
        "--- a/astropy/modeling/separable.py\n"
        "+++ b/astropy/modeling/separable.py\n"
        "@@ -300,7 +300,7 @@\n"
        " context\n"
        "-old_line\n"
        "+new_line\n"
        " more context\n"
    )

    jsonl_content = json.dumps({
        "instance_id": instance_id,
        "model_patch": model_patch,
        "model_name_or_path": "claude-3-5-sonnet-20241022",
    }).encode("utf-8") + b"\n"

    # Stub de gold_patch_lookup (en produccion vendria de HuggingFace).
    gold_lookup = _gold_lookup({instance_id: gold_patch})

    module = ExternalMetricsModule(ExternalMetricsConfig(max_retries=1))

    with patch.object(module._downloader, "_fetch", return_value=jsonl_content):
        records = module.run(
            source_config=source,
            instance_ids=[instance_id],
            gold_patch_lookup=gold_lookup,
        )

    # --- Verificar registros en memoria ---
    assert len(records) == 1
    r = records[0]
    assert r.instance_id == instance_id
    assert r.source_id == "agentless_v1.5"
    assert r.agent_id == "agentless_v1.5"
    assert r.model_id == "claude-3-5-sonnet-20241022"
    assert r.matched is True
    assert r.model_patch == model_patch
    assert isinstance(r.resolved, bool)
    assert r.resolved_source == "published"

    pm = r.patch_metrics
    assert pm["files_modified"] == ["astropy/modeling/separable.py"]
    assert pm["lines_added"] == 1
    assert pm["lines_deleted"] == 1
    assert pm["hunks_count"] == 1
    assert pm["jaccard_files"] == pytest.approx(1.0)

    # --- Verificar que el JSONL de salida existe y es legible ---
    out_path = source.local_cache_dir / "extracted_metrics.jsonl"
    assert out_path.exists()

    loaded = ExternalMetricsRecord.load_jsonl(out_path)
    assert len(loaded) == 1
    loaded_r = loaded[0]
    assert loaded_r.instance_id == r.instance_id
    assert loaded_r.source_id == r.source_id
    assert loaded_r.matched == r.matched
    assert loaded_r.patch_metrics["jaccard_files"] == pytest.approx(1.0)


def test_e2e_two_sources_independent(tmp_path: Path):
    """Dos fuentes distintas producen ficheros de salida independientes."""
    instance_ids = ["repo__repo-1", "repo__repo-2"]

    for source_id, patch_text in [("src_a", "diff a"), ("src_b", "diff b")]:
        source = _source_config(tmp_path, source_id=source_id)
        jsonl = "\n".join(
            json.dumps({"instance_id": iid, "model_patch": patch_text})
            for iid in instance_ids
        ).encode("utf-8")

        module = ExternalMetricsModule(ExternalMetricsConfig(max_retries=1))
        with patch.object(module._downloader, "_fetch", return_value=jsonl):
            records = module.run(source, instance_ids, gold_patch_lookup=_gold_lookup({}))

        assert len(records) == 2
        assert all(r.agent_id == source_id for r in records)
        out = source.local_cache_dir / "extracted_metrics.jsonl"
        assert out.exists()

    # Los dos directorios son independientes.
    assert (tmp_path / "src_a" / "extracted_metrics.jsonl").exists()
    assert (tmp_path / "src_b" / "extracted_metrics.jsonl").exists()
