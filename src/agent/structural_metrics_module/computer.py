"""Computo de metricas estructurales sobre diffs git."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any


class StructuralMetricsComputer:
    """Calcula la bateria de metricas de `StructuralRiskResult.metrics`."""

    def __init__(self, test_globs: list[str] | None = None):
        """Inicializa patrones para detectar ficheros de test."""
        self._test_globs = test_globs or ["tests/**", "**/test_*.py", "**/*_test.py", "**/*test*.py"]

    def compute(self, diff_payload: dict[str, Any], tracked_files_count: int) -> tuple[dict[str, float | int], dict[str, Any]]:
        """Calcula metricas y resumen de diff a partir del payload recolectado."""
        # Entradas base del diff incremental y acumulado.
        files_changed = diff_payload["files_changed"]
        incremental_stats = self._parse_numstat(diff_payload["incremental_numstat"])
        cumulative_stats = self._parse_numstat(diff_payload["cumulative_numstat"])
        # Cálculo de hunks (bloques de cambios delimitados por cabeceras @@ en un patch git).
        hunks = self._count_hunks(diff_payload["hunks_patch"])

        # Volumen de cambio para las métricas de líneas.
        files_count = len(files_changed)
        lines_added = sum(item["added"] for item in incremental_stats)
        lines_deleted = sum(item["deleted"] for item in incremental_stats)
        touched_lines = lines_added + lines_deleted

        # Vector principal de métricas del contrato.
        metrics: dict[str, float | int] = {
            "files_changed": files_count,
            "files_changed_ratio": (files_count / tracked_files_count) if tracked_files_count else 0.0,
            "hunks": hunks,
            "hunks_per_file": (hunks / files_count) if files_count else 0.0,
            "lines_added": lines_added,
            "lines_deleted": lines_deleted,
            "net_lines": lines_added - lines_deleted,
            "dispersion_score": self._dispersion_score(files_changed),
            "test_touch_ratio": self._test_touch_ratio(incremental_stats, touched_lines),
        }

        # Resumen ligero del diff para trazabilidad y estabilidad.
        summary = {
            "files_touched": files_changed,
            "extension_counts": diff_payload["extension_counts"],
            "hunks": hunks,
            "surface_signature": self._surface_signature(diff_payload["hunks_patch"]),
            "cumulative_surface_signature": self._surface_signature(
                diff_payload.get("cumulative_hunks_patch", "")
            ),
            "cumulative_lines_changed": sum(item["added"] + item["deleted"] for item in cumulative_stats),
        }
        return metrics, summary

    def _parse_numstat(self, numstat: str) -> list[dict[str, Any]]:
        """Parsea la salida `git diff --numstat` en filas estructuradas.

        Cada linea valida tiene el formato `<added>\\t<deleted>\\t<path>`.
        Cuando git devuelve `-` (por ejemplo, en binarios), se normaliza a 0
        para mantener el contrato numérico de las metricas.
        """
        rows: list[dict[str, Any]] = []
        for line in numstat.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            added_raw, deleted_raw, file_path = parts
            added = int(added_raw) if added_raw.isdigit() else 0
            deleted = int(deleted_raw) if deleted_raw.isdigit() else 0
            rows.append({"added": added, "deleted": deleted, "path": file_path})
        return rows

    def _count_hunks(self, patch: str) -> int:
        """Cuenta hunks (bloques @@) en el patch unificado del paso."""
        return sum(1 for line in patch.splitlines() if line.startswith("@@"))

    def _dispersion_score(self, files: list[str]) -> float:
        """Estima dispersion de cambios entre directorios de primer nivel."""
        if not files:
            return 0.0
        bucket: dict[str, int] = {}
        for file in files:
            root = Path(file).parts[0] if Path(file).parts else "."
            bucket[root] = bucket.get(root, 0) + 1
        total = len(files)
        entropy = 0.0
        for count in bucket.values():
            p = count / total
            entropy -= p * math.log2(p)
        max_entropy = math.log2(len(bucket)) if len(bucket) > 1 else 1.0
        return min(entropy / max_entropy, 1.0)

    def _test_touch_ratio(self, incremental_stats: list[dict[str, Any]], touched_lines: int) -> float:
        """Calcula la proporcion de lineas tocadas que pertenecen a tests."""
        if touched_lines == 0:
            return 0.0
        test_lines = 0
        for row in incremental_stats:
            path = row["path"]
            if any(Path(path).match(pattern) for pattern in self._test_globs):
                test_lines += row["added"] + row["deleted"]
        return test_lines / touched_lines

    def _surface_signature(self, patch: str) -> str:
        """Genera una firma compacta del area tocada (archivo + cabecera de hunk).

        Devuelve cadena vacía si el patch no contiene cambios reales.
        Esto evita que el StabilityMonitor trate «sin cambios incrementales»
        como una firma estable y promueva a FINALIZE(submit) erróneamente.
        """
        tokens: list[str] = []
        current_file = ""
        for line in patch.splitlines():
            if line.startswith("+++ b/"):
                current_file = line.removeprefix("+++ b/").strip()
                continue
            if line.startswith("@@"):
                tokens.append(f"{current_file}:{line}")
        if not tokens:
            return ""
        digest = hashlib.sha256("|".join(tokens).encode("utf-8")).hexdigest()
        return digest[:16]
