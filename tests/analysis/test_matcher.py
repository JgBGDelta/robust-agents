"""Tests unitarios del ``Matcher``.

Cubre: match completo, match parcial (unmatched), lista vacia de instancias,
patches con instance_ids duplicados (el segundo gana), y superconjunto de
patches (mas patches que instancias).
"""

from __future__ import annotations

from analysis.external_metrics_module.external_patch_record import ExternalPatchRecord
from analysis.external_metrics_module.matcher import Matcher


# ---------------------------------------------------------------------------
# Fabricas auxiliares
# ---------------------------------------------------------------------------


def _patch(instance_id: str, model_patch: str = "diff") -> ExternalPatchRecord:
    """Auxiliar interno: patch."""
    return ExternalPatchRecord(
        instance_id=instance_id,
        model_patch=model_patch,
        model_name_or_path="model-x",
    )


def _matcher() -> Matcher:
    """Auxiliar interno: matcher."""
    return Matcher()


# ---------------------------------------------------------------------------
# Match completo
# ---------------------------------------------------------------------------


def test_matcher_full_match():
    """Comprueba que matcher full match."""
    patches = [_patch("a__a-1"), _patch("b__b-2"), _patch("c__c-3")]
    instance_ids = ["a__a-1", "b__b-2", "c__c-3"]
    result = _matcher().match(patches, instance_ids)

    assert set(result.keys()) == set(instance_ids)
    assert all(v is not None for v in result.values())
    assert result["a__a-1"].instance_id == "a__a-1"


def test_matcher_returns_correct_patch_content():
    """Comprueba que matcher returns correct patch content."""
    patches = [_patch("a__a-1", model_patch="diff --git a/x b/x")]
    result = _matcher().match(patches, ["a__a-1"])
    assert result["a__a-1"].model_patch == "diff --git a/x b/x"


# ---------------------------------------------------------------------------
# Match parcial: instancias sin contraparte -> None
# ---------------------------------------------------------------------------


def test_matcher_unmatched_instance_returns_none():
    """Comprueba que matcher unmatched instance returns none."""
    patches = [_patch("a__a-1")]
    result = _matcher().match(patches, ["a__a-1", "missing__missing-99"])
    assert result["a__a-1"] is not None
    assert result["missing__missing-99"] is None


def test_matcher_all_unmatched():
    """Comprueba que matcher all unmatched."""
    patches = [_patch("other__other-1")]
    instance_ids = ["exp__exp-1", "exp__exp-2"]
    result = _matcher().match(patches, instance_ids)
    assert all(v is None for v in result.values())


# ---------------------------------------------------------------------------
# Casos borde
# ---------------------------------------------------------------------------


def test_matcher_empty_instance_ids():
    """Comprueba que matcher empty instance ids."""
    patches = [_patch("a__a-1")]
    result = _matcher().match(patches, [])
    assert result == {}


def test_matcher_empty_patches():
    """Comprueba que matcher empty patches."""
    result = _matcher().match([], ["a__a-1", "b__b-2"])
    assert result == {"a__a-1": None, "b__b-2": None}


def test_matcher_superconjunto_de_patches():
    """Mas patches que instancias: solo se emparejan los solicitados."""
    patches = [_patch(f"repo__repo-{i}") for i in range(50)]
    instance_ids = ["repo__repo-0", "repo__repo-25"]
    result = _matcher().match(patches, instance_ids)
    assert len(result) == 2
    assert result["repo__repo-0"] is not None
    assert result["repo__repo-25"] is not None


def test_matcher_duplicate_patch_instance_id_uses_last():
    """Si el JSONL tiene duplicados de instance_id, el Matcher usa el ultimo."""
    patches = [
        _patch("a__a-1", model_patch="primero"),
        _patch("a__a-1", model_patch="segundo"),
    ]
    result = _matcher().match(patches, ["a__a-1"])
    # El indice {instance_id: record} sobreescribe; el ultimo en la lista gana.
    assert result["a__a-1"].model_patch == "segundo"


def test_matcher_preserves_instance_id_order():
    """El dict resultado tiene el mismo orden que instance_ids."""
    patches = [_patch("c"), _patch("a"), _patch("b")]
    instance_ids = ["a", "b", "c"]
    result = _matcher().match(patches, instance_ids)
    assert list(result.keys()) == instance_ids
