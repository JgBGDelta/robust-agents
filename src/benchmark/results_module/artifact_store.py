"""Escritura atomica de artefactos del experimento.

Centraliza la escritura a disco bajo el principio "tmp + rename" para
que una interrupcion no deje archivos truncados que confundan a la
deteccion de runs ya completados (principio 10 del plan de desarrollo
y `modulo_resultados.md` MR.12 "escritura atomica").
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import yaml


class ArtifactStore:
    """Persiste artefactos JSON/YAML con escritura a temporal + rename."""

    @staticmethod
    def write_json(path: Path, payload: dict[str, Any]) -> None:
        """Escribe `payload` como JSON en `path` de forma atomica."""
        text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        ArtifactStore._write_text_atomic(path, text)

    @staticmethod
    def write_yaml(path: Path, payload: dict[str, Any]) -> None:
        """Escribe `payload` como YAML en `path` de forma atomica."""
        text = yaml.safe_dump(payload, sort_keys=True, allow_unicode=True)
        ArtifactStore._write_text_atomic(path, text)

    @staticmethod
    def read_json(path: Path) -> dict[str, Any]:
        """Lee un JSON desde disco."""
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def read_yaml(path: Path) -> dict[str, Any]:
        """Lee un YAML desde disco como diccionario."""
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Formato YAML invalido (se esperaba diccionario) en {path}")
        return raw

    @staticmethod
    def copy_tree(source: Path, destination: Path) -> None:
        """Copia un arbol de directorios reemplazando el destino si existe."""
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)

    @staticmethod
    def _write_text_atomic(path: Path, text: str) -> None:
        """Escribe `text` en `path` de forma atomica (tmp + rename)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_path, path)
