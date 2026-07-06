"""Extractor de `patch_metrics` para `EvaluationResult.extended`.

Compara `model_patch` con `gold_patch`. Sin gold publicado, los campos
comparativos quedan a `null`; las metricas del propio parche siguen
disponibles.

Soporta dos formatos de diff unificado:
- ``diff --git a/<file> b/<file>`` — formato extendido de Git (el mas comun).
- ``--- a/<file>`` / ``+++ b/<file>`` — formato unified diff estandar (p. ej.
  Moatless), sin la cabecera ``diff --git``.

Acoplamientos:
- Invocado por src/benchmark/evaluation_module/evaluator.py, src/benchmark/evaluation_module/extractors/__init__.py, tests/benchmark/test_evaluation.py."""

from __future__ import annotations

import re
from typing import Any

_FILE_HEADER_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)
# Formato unified diff estandar: "--- a/path" seguido de "+++ b/path".
# Capturamos el path del fichero de destino (b/), que es el nombre tras la edicion.
# Si el destino es /dev/null (borrado), usamos el origen (a/).
_UNIFIED_MINUS_RE = re.compile(r"^--- a/(.+)$", re.MULTILINE)
_UNIFIED_PLUS_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)
_HUNK_RE = re.compile(r"^@@ ", re.MULTILINE)

# Metricas con ceros cuando no hay parche.
_EMPTY_PATCH_METRICS: dict[str, Any] = {
    "lines_added": 0,
    "lines_deleted": 0,
    "hunks_count": 0,
    "files_modified": [],
    "files_intersection_with_gold": None,
    "files_unexpected": None,
    "files_missing_vs_gold": None,
    "jaccard_files": None,
    "jaccard_lines": None,
}


class PatchMetricsExtractor:
    """Calcula `patch_metrics` y su entrada de disponibilidad."""

    @staticmethod
    def extract(
        *,
        model_patch: str | None,
        gold_patch: str | None,
    ) -> dict[str, Any]:
        """Devuelve el diccionario `patch_metrics`.

        Cuando `model_patch` esta vacio: ceros en metricas propias y `null` en
        comparativas. Cuando `gold_patch` es `None`: comparativas a `null`.
        """
        if not model_patch:
            return dict(_EMPTY_PATCH_METRICS)

        self_metrics = _patch_self_metrics(model_patch)

        if not gold_patch:
            return {
                **self_metrics,
                "files_intersection_with_gold": None,
                "files_unexpected": None,
                "files_missing_vs_gold": None,
                "jaccard_files": None,
                "jaccard_lines": None,
            }

        gold_files_set = set(_files_modified(gold_patch))
        model_files_set = set(self_metrics["files_modified"])
        intersection = sorted(model_files_set & gold_files_set)
        union = model_files_set | gold_files_set
        unexpected = sorted(model_files_set - gold_files_set)
        missing = sorted(gold_files_set - model_files_set)
        jaccard_files = (len(intersection) / len(union)) if union else 0.0
        jaccard_lines = _jaccard_lines(model_patch, gold_patch)

        return {
            **self_metrics,
            "files_intersection_with_gold": intersection,
            "files_unexpected": unexpected,
            "files_missing_vs_gold": missing,
            "jaccard_files": jaccard_files,
            "jaccard_lines": jaccard_lines,
        }

    @staticmethod
    def availability(*, model_patch: str | None, gold_patch: str | None) -> dict[str, Any]:
        """Devuelve la entrada de disponibilidad para `patch_metrics`."""
        if not model_patch:
            return {"status": "not_applicable", "cause": "empty_model_patch"}
        if not gold_patch:
            return {"status": "not_applicable", "cause": "no_gold_patch"}
        return {"status": "available", "cause": None}


# --- Funciones privadas ----------------------------------------------------------


def _patch_self_metrics(patch: str) -> dict[str, Any]:
    """Auxiliar interno: patch self metrics."""
    files = sorted(set(_files_modified(patch)))
    added, deleted = _count_added_deleted(patch)
    hunks = len(_HUNK_RE.findall(patch))
    return {
        "lines_added": added,
        "lines_deleted": deleted,
        "hunks_count": hunks,
        "files_modified": files,
    }


def _files_modified(patch: str) -> list[str]:
    """Extrae los nombres de fichero modificados por el parche.

    Maneja dos formatos:
    - ``diff --git a/X b/X`` (Git extended diff).
    - ``--- a/X`` + ``+++ b/X`` (unified diff estandar, sin cabecera Git).
      En este caso empareja cada par ``---``/``+++`` consecutivos dentro
      del mismo bloque de fichero.
    """
    # Intentar con cabeceras Git primero (mas fiables, evitan falsos positivos).
    git_files = [
        b_path if b_path != "/dev/null" else a_path
        for a_path, b_path in _FILE_HEADER_RE.findall(patch)
    ]
    if git_files:
        return git_files

    # Fallback: unified diff estandar (--- a/X ... +++ b/Y).
    # Emparejamos posicionalmente: cada "--- a/X" va seguido de "+++ b/Y".
    # Capturamos tambien el posible "+++ /dev/null" (borrado de fichero).
    _PLUS_ANY_RE = re.compile(r"^\+\+\+ (?:b/)?(.+)$", re.MULTILINE)
    minus_paths = _UNIFIED_MINUS_RE.findall(patch)
    plus_paths = _PLUS_ANY_RE.findall(patch)
    files: list[str] = []
    for a_path, b_path in zip(minus_paths, plus_paths):
        # Quitar posibles timestamps (GNU diff añade TAB + fecha tras el path).
        b_path = b_path.split("\t")[0].rstrip()
        a_path = a_path.split("\t")[0].rstrip()
        files.append(b_path if b_path not in ("/dev/null", "") else a_path)
    return files


def _count_added_deleted(patch: str) -> tuple[int, int]:
    """Auxiliar interno: count added deleted."""
    added = 0
    deleted = 0
    for line in patch.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            deleted += 1
    return added, deleted


def _jaccard_lines(model_patch: str, gold_patch: str) -> float:
    """Auxiliar interno: jaccard lines."""
    model_lines = _modified_lines_set(model_patch)
    gold_lines = _modified_lines_set(gold_patch)
    if not model_lines and not gold_lines:
        return 0.0
    inter = len(model_lines & gold_lines)
    union = len(model_lines | gold_lines)
    return inter / union if union else 0.0


def _modified_lines_set(patch: str) -> set[str]:
    """Auxiliar interno: modified lines set."""
    out: set[str] = set()
    for line in patch.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if not (line.startswith("+") or line.startswith("-")):
            continue
        normalized = line[1:].strip()
        if not normalized:
            continue
        out.add(normalized)
    return out
